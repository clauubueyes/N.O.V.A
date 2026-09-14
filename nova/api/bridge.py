from __future__ import annotations

"""`nova://` bridge — start the local API server from a browser deep link.

Registered as the handler for the `nova://` URL protocol (see
`nova.setup.protocol`). Because the OS launches it while the browser tab stays
open (e.g. the Vercel-hosted web), it must not depend on the process CWD: the
`--app-root` argument pins the repository/installation root, and everything
else is resolved relative to it.

Actions
-------
start       Spawn the API server detached (no console window) if it is not
            already listening, then poll until it is healthy.
"""

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _probe(base: str, timeout: float = 2.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/healthz", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _server_base(args) -> tuple[str, str]:
    import os

    os.chdir(args.app_root)
    from nova.core.config import load_settings

    settings = load_settings()
    host = settings.api.host
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    return probe_host, str(settings.api.port)


def _spawn_server(args) -> subprocess.Popen:
    import os

    pythonw = Path(sys.executable).parent / "pythonw.exe"
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    cmd = [interpreter, "-m", "nova.api.server"]
    kwargs: dict = {"cwd": args.app_root}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _do_start(args) -> int:
    import os

    base = "http://{}:{}".format(*_server_base(args))
    if _probe(base, timeout=2.0) or (time.sleep(0.5) is None and _probe(base, timeout=2.0)):
        print(f"ALREADY_RUNNING {base}")
        return 0
    os.chdir(args.app_root)
    _spawn_server(args)
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if _probe(base, timeout=1.0):
            print(f"STARTED {base}")
            return 0
        time.sleep(0.5)
    print(f"TIMEOUT {base}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nova-api-bridge")
    parser.add_argument("action", choices=["start"])
    parser.add_argument("--app-root", required=True, help="repository or installation root")
    parser.add_argument("--wait", type=float, default=30.0, help="seconds to wait for the server")
    args = parser.parse_args(argv)
    return _do_start(args)


if __name__ == "__main__":
    sys.exit(main())