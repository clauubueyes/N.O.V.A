from __future__ import annotations

"""Standalone stdlib helper for Windows' locked, currently running virtualenv."""

import json
import os
import shutil
import sys
import time
from pathlib import Path


def safe_target(path: Path, roots: list[Path]) -> Path:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("Unsafe cleanup target")
    for root in roots:
        if resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise ValueError("Repository protected")
    for parent in (path.absolute(), *path.absolute().parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Link/junction protected")
        if parent == Path.home().resolve():
            break
        if (parent / ".git").exists() or ((parent / "pyproject.toml").exists() and (parent / "nova").is_dir()):
            raise ValueError("Repository protected")
    if resolved.is_dir():
        for base, dirs, files in os.walk(resolved, followlinks=False):
            if ".git" in dirs or ".git" in files:
                raise ValueError("Nested repository protected")
            for name in dirs + files:
                child = Path(base) / name
                if child.is_symlink() or getattr(child.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError("Link/junction protected")
    return resolved


def cleanup(payload: dict) -> None:
    roots = [Path(p).resolve() for p in payload["roots"]]
    state_path = safe_target(Path(payload["state"]), roots)
    expected = payload.get("state_content")
    for _ in range(120):
        try:
            if expected is not None and state_path.read_text(encoding="utf-8") != expected:
                raise ValueError("Installation state changed after removal was scheduled")
            for raw in payload["paths"]:
                path = safe_target(Path(raw), roots)
                if path.exists():
                    shutil.rmtree(path)
            state_path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(1)
    raise OSError("Close remaining N.O.V.A. processes and rerun remove")


if __name__ == "__main__":
    payload = json.loads(sys.argv[1])
    time.sleep(2)
    try:
        cleanup(payload)
    except (OSError, ValueError) as exc:
        target = safe_target(Path(payload["state"]).with_suffix(".cleanup-error.txt"),
                             [Path(p).resolve() for p in payload["roots"]])
        target.write_text(str(exc), encoding="utf-8")
