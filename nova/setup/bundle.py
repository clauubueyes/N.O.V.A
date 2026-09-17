from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from nova.setup.state import StateStore, check_removal_path, repository_roots

MARKER = '.nova-bundle.json'


def stop_running_core(timeout: float = 15.0) -> None:
    """Ask the desktop core to exit and wait before replacing its files."""
    from nova.core.config import load_settings
    from nova.core.paths import installation_home

    home = installation_home()
    marker = home / 'core-runtime.json'
    if not marker.is_file():
        return
    try:
        runtime = json.loads(marker.read_text(encoding='utf-8'))
        port = int(runtime['port'])
        token = load_settings(str(home / 'config.yaml')).api.token
    except (OSError, ValueError, KeyError):
        raise RuntimeError('No se puede verificar el núcleo activo; la actualización se ha detenido.')

    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=3, trust_env=False) as client:
            response = client.post('/v1/desktop/exit', headers={'Authorization': 'Bearer ' + token})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError('No se puede cerrar correctamente el núcleo activo; la actualización se ha detenido.') from exc

    deadline = time.monotonic() + timeout
    while marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if marker.exists():
        raise RuntimeError('El núcleo anterior no se cerró a tiempo; la actualización se ha detenido.')


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
