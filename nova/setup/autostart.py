from __future__ import annotations

"""Optional autostart on login (Windows HKCU, Linux .config/autostart, macOS).

Kept minimal and read-only-friendly: only touches the current user's session
launchers, never HKLM / system-wide. The default launcher is `nova-agent` (the
local desktop agent) unless `target` is overridden to e.g. `nova-api`.
"""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "NOVA"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(Exception):
    pass


def _launcher_command(target: str = "nova-agent") -> str | None:
    """Return the absolute command to launch `target`, or None if unavailable."""
    # Prefer an existing console script on PATH.
    exe = shutil.which(target + (".exe" if sys.platform.startswith("win") else ""))
    if exe:
        return f'"{exe}"'
    # Fall back to `python -m nova`-style entry.
    python = shutil.which(sys.executable) or sys.executable
    return f'"{python}" -m {target}'


def autostart_status() -> bool:
    current = None
    if sys.platform.startswith("win"):
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ
            ) as key:
                current, _ = winreg.QueryValueEx(key, APP_NAME)
        except FileNotFoundError:
            current = None
        except OSError:
            current = None
    elif sys.platform.startswith("linux"):
        desktop = Path.home() / ".config" / "autostart" / f"{APP_NAME}.desktop"
        current = str(desktop) if desktop.exists() else None
    elif sys.platform.startswith("darwin"):
        plist = Path.home() / "Library" / "LaunchAgents" / f"com.nova.{APP_NAME}.plist"
        current = str(plist) if plist.exists() else None
    return current is not None


def set_autostart(enabled: bool, target: str = "nova-agent") -> bool:
    cmd = _launcher_command(target)
    if not cmd:
        raise AutostartError("N.O.V.A. entry point not found on PATH")
    if enabled:
        if sys.platform.startswith("win"):
            import winreg

            try:
                with winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE
                ) as key:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                return True
            except OSError as exc:
                raise AutostartError(f"could not write registry: {exc}") from exc
        elif sys.platform.startswith("linux"):
            folder = Path.home() / ".config" / "autostart"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{APP_NAME}.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=N.O.V.A.\nExec=" + cmd + "\n",
                encoding="utf-8",
            )
            return True
        elif sys.platform.startswith("darwin"):
            import plistlib

            folder = Path.home() / "Library" / "LaunchAgents"
            folder.mkdir(parents=True, exist_ok=True)
            plist = {
                "Label": f"com.nova.{APP_NAME}",
                "ProgramArguments": [t.strip('"') for t in cmd.split()],
                "RunAtLoad": True,
            }
            (folder / f"com.nova.{APP_NAME}.plist").write_bytes(plistlib.dumps(plist))
            return True
        raise AutostartError("unsupported platform")
    # Disable
    if sys.platform.startswith("win"):
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise AutostartError(f"could not delete registry: {exc}") from exc
        return True
    elif sys.platform.startswith("linux"):
        f = Path.home() / ".config" / "autostart" / f"{APP_NAME}.desktop"
        if f.exists():
            f.unlink()
        return True
    elif sys.platform.startswith("darwin"):
        f = Path.home() / "Library" / "LaunchAgents" / f"com.nova.{APP_NAME}.plist"
        if f.exists():
            f.unlink()
        return True
    raise AutostartError("unsupported platform")
