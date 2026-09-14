from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from nova.core.paths import installation_home

URL_RE = re.compile(r'(https://[a-zA-Z0-9.-]+\.trycloudflare\.com)')
_LOCALAPPDATA_BIN = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'NOVA' / 'bin' / 'cloudflared.exe'


class TunnelError(RuntimeError):
    pass


def cloudflared_binary() -> str | None:
    configured = os.environ.get('NOVA_CLOUDFLARED')
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    if _LOCALAPPDATA_BIN.is_file():
        return str(_LOCALAPPDATA_BIN)
    return shutil.which('cloudflared')


def core_local_url() -> str:
    try:
        info = json.loads((installation_home() / 'core-runtime.json').read_text(encoding='utf-8'))
        port = int(info['port'])
        if 1 <= port <= 65535:
            return f'http://127.0.0.1:{port}'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 'http://127.0.0.1:8000'


class TunnelManager:
    def __init__(self, binary: str | None = None, timeout: float = 30.0):
        self._binary = binary
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None

    @property
    def active(self) -> bool:
        if not self._proc or self._proc.poll() is not None:
            return False
        return bool(self._url)

    @property
    def url(self) -> str | None:
        return self._url

    @property
    def host(self) -> str | None:
        if not self._url:
            return None
        parsed = urllib.parse.urlparse(self._url)
        return parsed.hostname

    def start(self, local_url: str | None = None, timeout: float | None = None) -> str:
        if self.active:
            return self._url or ''
        binary = self._binary or cloudflared_binary()
        if not binary:
            raise TunnelError(
                'No se encontró cloudflared. Descárgalo o configura su ruta '
                'en la variable NOVA_CLOUDFLARED.'
            )
        target = local_url or core_local_url()
        command = [binary, 'tunnel', '--url', target, '--no-autoupdate']
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self._proc = proc
        url_queue: queue.Queue[str] = queue.Queue()

        def reader() -> None:
            try:
                for line in proc.stdout:
                    match = URL_RE.search(line)
                    if match:
                        url_queue.put(match.group(1))
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.monotonic() + (timeout or self._timeout)
        while not self._url and time.monotonic() < deadline:
            try:
                self._url = url_queue.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    break
        if not self._url:
            self.stop()
            raise TunnelError('No se pudo obtener la URL pública del túnel. Comprueba tu conexión e inténtalo de nuevo.')
        return self._url

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._url = None

    def __enter__(self) -> 'TunnelManager':
        return self

    def __exit__(self, *exc) -> None:
        self.stop()