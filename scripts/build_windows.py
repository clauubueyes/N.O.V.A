from __future__ import annotations

"""Build the N.O.V.A. Windows distribution.

Produces:
  dist/NOVA/            onedir runtime (NOVA.exe + Qt + WebEngine + web asset)
  dist/NOVA-Uninstall.exe
  dist/NOVA-Setup.exe   onefile installer with the payload embedded
  dist/NOVA-Setup.sha256

Run it on Windows x64 with the `build` extra installed:
  python scripts/build_windows.py
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

# Optional extras that a setup/uninstall UI never needs.
SETUP_EXCLUDES = ("pyttsx3", "vosk", "sounddevice", "numpy")


def make_icon() -> Path:
    """Generate the N.O.V.A. .ico (mint rounded square with a dark "N")."""
    sizes = [16, 32, 48, 64, 128, 256]

    def _font(pixel: int) -> ImageFont.FreeTypeFont:
        for candidate in (
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ):
            if Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, int(pixel * 0.60))
                except OSError:
                    continue
        return ImageFont.load_default()

    frames = []
    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        shrink = max(1, int(size * 0.09))
        radius = max(2, int(size * 0.20))
        draw.rounded_rectangle(
            (shrink, shrink, size - shrink, size - shrink),
            radius=radius,
            fill=(127, 216, 196, 255),
        )
        font = _font(size)
        bbox = draw.textbbox((0, 0), "N", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size - w) / 2 - bbox[0]
        y = (size - h) / 2 - bbox[1] - int(size * 0.02)
        draw.text((x, y), "N", font=font, fill=(12, 16, 22, 255))
        frames.append(img)

    destination = ROOT / "build" / "icons" / "nova.ico"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_ico(destination, frames)
    return destination


def _write_ico(path: Path, frames: list[Image.Image]) -> None:
    """Serialize an .ico wrapping each frame as PNG (modern 32-bit format)."""
    import io
    import struct

    payloads = []
    for frame in frames:
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        payloads.append(buf.getvalue())

    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(payloads)))
    offset = 6 + 16 * len(payloads)
    for frame, data in zip(frames, payloads):
        w = 0 if frame.width == 256 else frame.width
        h = 0 if frame.height == 256 else frame.height
        out.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for data in payloads:
        out.write(data)

    path.write_bytes(out.getvalue())


def freeze(name: str, entry: str, extra=(), excludes=(), icon: Path | None = None):
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--noupx",
        "--name", name,
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "freeze"),
        "--specpath", str(ROOT / "build" / "specs"),
        "--paths", str(ROOT),
    ]
    for module in ("pytest", "tkinter", *excludes):
        command += ["--exclude-module", module]
    if icon is not None:
        command += ["--icon", str(icon)]
    command += [*extra, str(ROOT / entry)]

    log = ROOT / "build" / (name + ".log")
    print(f"\n=== Building {name} ===", flush=True)
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        print(log.read_text(encoding="utf-8", errors="replace")[-12000:])
        raise SystemExit(result.returncode)
    print(f"=== {name} built OK ===", flush=True)


def freeze_setup(payload: Path, icon: Path):
    freeze(
        "NOVA-Setup",
        "nova/setup/installer.py",
        extra=[
            "--onefile",
            "--add-data", str(payload) + ":payload",
        ],
        excludes=SETUP_EXCLUDES,
        icon=icon,
    )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build this Windows distribution on Windows x64.")

    icon = make_icon()
    print(f"Icon: {icon}")

    # 1. NOVA runtime (onedir with Qt, WebEngine and the web asset).
    freeze(
        "NOVA",
        "nova/desktop/__main__.py",
        extra=[
            "--onedir",
            "--add-data", str(ROOT / "web") + ":nova/api/static",
            "--add-data", str(ROOT / "LICENSE") + ":licenses",
            "--add-data", str(ROOT / "THIRD_PARTY.md") + ":licenses",
            "--hidden-import", "nova.plugins.builtin.text_tools",
            "--hidden-import", "nova.plugins.builtin.units",
            "--hidden-import", "uvicorn.logging",
            "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan.on",
            "--collect-data", "PySide6",
        ],
        icon=icon,
    )

    result = subprocess.run(
        [str(ROOT / "dist" / "NOVA" / "NOVA.exe"), "--smoke"], timeout=90
    )
    if result.returncode:
        raise SystemExit("Frozen runtime smoke test failed.")

    # 2. Uninstaller (small onefile, reuses the installer UI).
    freeze("NOVA-Uninstall", "nova/setup/installer.py", extra=["--onefile"], icon=icon)
    import shutil as _shutil

    _shutil.copy2(
        ROOT / "dist" / "NOVA-Uninstall.exe",
        ROOT / "dist" / "NOVA" / "NOVA-Uninstall.exe",
    )

    # 3. Payload archive (the frozen NOVA tree, checksummed).
    payload = ROOT / "build" / "payload"
    payload.mkdir(exist_ok=True)
    with zipfile.ZipFile(payload / "nova.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in sorted((ROOT / "dist" / "NOVA").rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(ROOT / "dist" / "NOVA").as_posix())
    import tomllib
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with (payload / "nova.zip").open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    (payload / "manifest.json").write_text(
        json.dumps(dict(version=version, sha256=digest, arch="amd64")), encoding="utf-8"
    )
    print(f"Payload ready: {payload / 'nova.zip'}  ({digest[:16]}…)")

    # 4. Setup (onefile with the payload embedded).
    freeze_setup(payload, icon)

    artifact = ROOT / "dist" / "NOVA-Setup.exe"
    if not artifact.exists():
        raise SystemExit("NOVA-Setup.exe was not produced.")
    with artifact.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    (ROOT / "dist" / "NOVA-Setup.sha256").write_text(digest + "  NOVA-Setup.exe\n", encoding="ascii")
    print(f"\n{artifact}")
    print(f"{artifact.stat().st_size / 1024**2:.1f} MB; SHA256 {digest}")


if __name__ == "__main__":
    main()