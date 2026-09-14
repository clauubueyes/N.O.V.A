from __future__ import annotations

"""Register/unregister the `nova://` URL protocol (Windows HKCU).

Kept minimal and read-only-friendly: only touches the current user's class
root (no admin, no HKLM). The handler runs a python (prefer `pythonw`) with the
`nova.api.bridge` module and an absolute `--app-root`, so a browser deep link
starts the local API server without a console window.

| Action     | Effect                                   |
|------------|------------------------------------------|
| Register   | `HKCU\\Software\\Classes\\nova` -> bridge |
| Unregister | Removes the `nova` class only            |
"""

import sys
from pathlib import Path

_CLASS_KEY = r"Software\Classes\nova"


class ProtocolError(Exception):
    pass


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def _handler_command() -> str:
    pythonw = (Path(sys.executable).parent / "pythonw.exe") if sys.platform.startswith("win") else None
    interpreter = str(pythonw) if pythonw and pythonw.exists() else sys.executable
    return f'"{interpreter}" -m nova.api.bridge start --app-root "{_project_root()}"'


def protocol_status() -> bool:
    if not sys.platform.startswith("win"):
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLASS_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "URL Protocol")
        return True
    except (FileNotFoundError, OSError):
        return False


def _register() -> None:
    import winreg

    command = _handler_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _CLASS_KEY, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:NOVA Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _CLASS_KEY + r"\DefaultIcon", 0, winreg.KEY_WRITE) as key:
            icon = command.split("-m", 1)[0].strip() + ",0"
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, icon)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _CLASS_KEY + r"\shell\open\command", 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
    except OSError as exc:
        raise ProtocolError(f"could not write registry: {exc}") from exc


def _unregister() -> None:
    import winreg

    for sub in (
        r"shell\open\command",
        r"shell\open",
        r"shell",
        r"DefaultIcon",
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _CLASS_KEY + "\\" + sub)
        except (FileNotFoundError, OSError):
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLASS_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for value in (None, "URL Protocol"):
                try:
                    winreg.DeleteValue(key, value)
                except FileNotFoundError:
                    pass
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _CLASS_KEY)
    except OSError as exc:
        raise ProtocolError(f"could not delete registry: {exc}") from exc


def set_protocol(enabled: bool) -> None:
    if not sys.platform.startswith("win"):
        raise ProtocolError("the nova:// protocol is only registered on Windows")
    if enabled:
        _register()
    else:
        _unregister()