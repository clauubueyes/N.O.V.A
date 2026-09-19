from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from nova.setup.state import StateStore, check_removal_path, repository_roots

MARKER = '.nova-bundle.json'


def _listening(port: int, host: str = '127.0.0.1') -> bool:
    """True when something is actually accepting connections on ``host:port``."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect((host, port))
        return True
    except OSError:
        return False


def _request_exit(port: int, token: str) -> None:
    """Ask a core to shut down gracefully. A core without the exit route (an
    old 0.17.x server) is simply left to the caller's timeout/force-kill."""
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=3, trust_env=False) as client:
            client.post('/v1/desktop/exit', headers=headers)
    except httpx.HTTPError:
        pass


def _discover_api_server_pids() -> set[int]:
    """PIDs of every running ``-m nova.api.server`` process (the web bridge core).

    Old versions of that server wrote no ``core-runtime.json`` and had no
    ``/v1/desktop/exit`` route, so the updater must be able to find it by its
    command line alone.
    """
    pids: set[int] = set()
    try:
        if os.name == 'nt':
            script = (
                "foreach ($p in Get-CimInstance Win32_Process) { "
                "if ($p.CommandLine -and $p.CommandLine -match 'nova\\.api\\.server') { $p.ProcessId } }"
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True, text=True, timeout=10,
            )
        else:
            result = subprocess.run(['ps', '-eo', 'pid=,args='], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return pids
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or 'nova.api.server' not in parts[1]:
            continue
        try:
            pids.add(int(parts[0]))
        except ValueError:
            continue
    return pids


def _terminate_process(pid: int) -> None:
    """Force-terminate a process tree (used only after a graceful exit refused)."""
    if pid <= 0:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
    else:
        try:
            os.kill(pid, getattr(signal, 'SIGKILL', 9))
        except OSError:
            pass


def stop_running_core(timeout: float = 15.0) -> None:
    """Stop every running N.O.V.A. core before its files are replaced.

    Two kinds of cores can be alive at update time, and both must die:

    * the desktop core, tracked by ``core-runtime.json`` (random port + pid),
      which might already be gone leaving a stale marker;
    * the API server started by the web bridge, which listens on the
      configured ``api.port`` but writes no marker — including an old 0.17.x
      process left running before the update.

    Each one is closed gracefully first (``POST /v1/desktop/exit``); a core
    that ignores it is force-terminated afterwards. A foreign process that
    simply happens to answer on the API port is never killed.
    """
    from nova.core.config import load_settings
    from nova.core.paths import installation_home

    home = installation_home()
    config_path = home / 'config.yaml'
    settings = load_settings(str(config_path)) if config_path.is_file() else load_settings()
    marker = home / 'core-runtime.json'

    marker_port = marker_pid = None
    if marker.is_file():
        try:
            state = json.loads(marker.read_text(encoding='utf-8'))
            marker_port = int(state['port'])
            marker_pid = int(state.get('pid') or 0) or None
        except (OSError, ValueError, KeyError):
            raise RuntimeError('No se puede verificar el núcleo activo; la actualización se ha detenido.')

    targets: list[tuple[int, str]] = []
    if marker_port is not None:
        if _listening(marker_port):
            targets.append((marker_port, settings.api.token or ''))
        else:
            # The pid that owned the marker is gone; don't block the update.
            marker.unlink(missing_ok=True)
    api_port = int(settings.api.port)
    if 1 <= api_port <= 65535 and api_port != marker_port and _listening(api_port):
        targets.append((api_port, settings.api.token or ''))

    if not targets:
        return

    deadline = time.monotonic() + timeout
    for port, token in targets:
        _request_exit(port, token)

    while time.monotonic() < deadline:
        if not any(_listening(port) for port, _ in targets):
            _clear_stale_marker(marker, marker_port)
            return
        time.sleep(0.1)

    pids = {pid for pid in (marker_pid,) if pid}
    pids.update(_discover_api_server_pids())
    for pid in pids:
        _terminate_process(pid)

    while time.monotonic() < deadline:
        if not any(_listening(port) for port, _ in targets):
            _clear_stale_marker(marker, marker_port)
            return
        time.sleep(0.1)
    raise RuntimeError('El núcleo anterior no se cerró a tiempo; la actualización se ha detenido.')


def _clear_stale_marker(marker: Path, marker_port: int | None) -> None:
    if marker_port is None or not marker.exists():
        return
    try:
        if json.loads(marker.read_text(encoding='utf-8')).get('port') == marker_port and not _listening(marker_port):
            marker.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def validate_target(target: Path) -> Path:
    if not target.is_absolute():
        raise ValueError('Elige una ubicación completa para instalar N.O.V.A.')
    target = check_removal_path(target, repository_roots())
    if target.exists() and any(target.iterdir()):
        marker = target / MARKER
        state = StateStore().load()
        if not state or not any(Path(r.path).resolve() == target and r.kind == 'binary' for r in state.resources):
            raise ValueError('Esta carpeta ya contiene archivos. Elige una carpeta vacía para N.O.V.A.')
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('product') != 'NOVA':
            raise ValueError('No podemos verificar la instalación anterior. Elige una carpeta nueva.')
    return target


def install_bundle(archive: Path, manifest: dict, target: Path, progress=lambda done, total: None) -> Path:
    """Verified staging plus rollback; application files and user data stay separate."""
    store = StateStore()
    target = validate_target(target)
    if target.exists():
        stop_running_core()
    with store.lock():
        target = validate_target(target)
        if sha256(archive) != manifest['sha256']:
            raise ValueError('El instalador está incompleto o dañado. Descárgalo de nuevo.')
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            total = sum(entry.file_size for entry in package.infolist())
            if shutil.disk_usage(parent).free < total * 2 + 1024**3:
                raise ValueError('No hay espacio suficiente para instalar N.O.V.A. con seguridad.')
            staging = Path(tempfile.mkdtemp(prefix='.nova-stage-', dir=parent)).resolve()
            backup = parent / ('.nova-backup-' + uuid.uuid4().hex)
            try:
                done = 0
                seen = set()
                for entry in package.infolist():
                    name = PurePosixPath(entry.filename)
                    if name.is_absolute() or '..' in name.parts or '\\' in entry.filename or ':' in entry.filename:
                        raise ValueError('El instalador contiene una ruta no válida.')
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('El instalador contiene un enlace no permitido.')
                    destination = (staging / Path(*name.parts)).resolve()
                    if not destination.is_relative_to(staging):
                        raise ValueError('El instalador contiene una ruta no válida.')
                    canonical = str(destination).casefold()
                    if canonical in seen:
                        raise ValueError('El instalador contiene archivos duplicados.')
                    seen.add(canonical)
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, destination.open('wb') as output:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            done += len(chunk)
                            progress(done, total)
                if not (staging / 'NOVA.exe').is_file():
                    raise ValueError('El instalador no contiene la aplicación.')
                (staging / MARKER).write_text(json.dumps(dict(product='NOVA', version=manifest['version'])), encoding='utf-8')
                validate_target(target)
                had_previous = target.exists()
                if had_previous:
                    target.rename(backup)
                try:
                    staging.rename(target)
                except OSError:
                    if had_previous:
                        backup.rename(target)
                    raise
                store.record_resource(target, 'binary')
                if backup.exists():
                    shutil.rmtree(check_removal_path(backup, repository_roots()))
            finally:
                if staging.exists():
                    shutil.rmtree(check_removal_path(staging, repository_roots()))
        return target / 'NOVA.exe'


def register_windows(target: Path, shortcut: bool = True):
    import winreg
    from PySide6.QtCore import QFile, QStandardPaths

    executable = target / 'NOVA.exe'
    store = StateStore()
    locations = [Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.ApplicationsLocation))]
    if shortcut:
        locations.append(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)))
    for location in locations:
        location.mkdir(parents=True, exist_ok=True)
        link = location / 'N.O.V.A..lnk'
        if not link.exists() and QFile.link(str(executable), str(link)):
            store.record_resource(link, 'integration')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NOVA.Desktop') as key:
        for name, value in {'DisplayName': 'N.O.V.A.', 'Publisher': 'N.O.V.A.',
                            'InstallLocation': str(target), 'DisplayIcon': str(executable),
                            'UninstallString': '"' + str(target / 'NOVA-Uninstall.exe') + '"'}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
