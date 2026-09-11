from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

from nova.core.config import load_settings
from nova.core.paths import installation_home
from nova.desktop.configuration import initialize


def command(*args: str) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    python = Path(sys.executable)
    windowed = python.with_name('pythonw.exe')
    if os.name == 'nt' and windowed.exists():
        python = windowed
    return [str(python), '-m', 'nova.desktop', *args]


def spawn(*args: str) -> subprocess.Popen:
    env = os.environ.copy()
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return subprocess.Popen(command(*args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                            env=env, close_fds=True)


def connection(home: Path | None = None) -> tuple[str, str] | None:
    home = home or installation_home()
    try:
        info = json.loads((home / 'core-runtime.json').read_text(encoding='utf-8'))
        port = int(info['port'])
        if not 1 <= port <= 65535:
            return None
        token = load_settings(str(home / 'config.yaml')).api.token
        base = f'http://127.0.0.1:{port}'
        with httpx.Client(timeout=1, trust_env=False) as client:
            reply = client.get(base + '/v1/desktop/status', headers={'Authorization': 'Bearer ' + token})
            if reply.status_code == 200 and reply.json().get('desktop'):
                return base, token
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        pass
    return None


def serve() -> int:
    import uvicorn
    from fastapi import HTTPException
    from nova.api.app import create_app
    from nova.core.logging import setup_logging
    from nova.setup.state import StateStore

    home = installation_home()
    config = initialize(home)
    with StateStore(home / 'core-process-lock').lock():
        settings = load_settings(str(config))
        setup_logging(settings.logging)
        app = create_app(settings, desktop=True, config_path=config)
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))

        async def shutdown():
            app.state.nova.paused = True
            app.state.nova.approvals.cancel_all()
            server.should_exit = True
            return {'stopping': True}
        app.router.routes.insert(0, __import__('fastapi').routing.APIRoute('/v1/desktop/exit', shutdown, methods=['POST']))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        marker = home / 'core-runtime.json'
        nonce = uuid.uuid4().hex
        marker.write_text(json.dumps(dict(port=listener.getsockname()[1], pid=os.getpid(), nonce=nonce)), encoding='utf-8')
        try:
            from nova.setup.detect import ensure_ollama_running
            import threading
            if settings.desktop.prepared:
                threading.Thread(target=ensure_ollama_running, args=(settings.llm.base_url,), daemon=True).start()
            server.run(sockets=[listener])
        finally:
            listener.close()
            if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('nonce') == nonce:
                marker.unlink()
    return 0
