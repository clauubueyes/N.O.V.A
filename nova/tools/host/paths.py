from __future__ import annotations

import os
from pathlib import Path

from nova.tools.base import ToolError


class PathBounds:
    """Filesystem bounds for host tools.

    Every path touched by a host tool must resolve inside one of the configured
    `host.roots`. On Windows the comparison is case-insensitive; symlinks/junctions
    are resolved before checking so no bound can be escaped through them.
    """

    def __init__(self, roots: list[str]) -> None:
        self._roots = [Path(root).expanduser().resolve() for root in roots if root.strip()]

    @property
    def enabled(self) -> bool:
        return bool(self._roots)

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def resolve_within(self, raw: str, what: str = "path") -> Path:
        """Resolve `raw` to an absolute path and require it to be inside a root."""
        if not self.enabled:
            raise ToolError(f"{what} not allowed: no host.roots configured")
        target = Path(raw).expanduser().resolve()
        for root in self._roots:
            if self._is_within(target, root):
                return target
        raise ToolError(f"{what} outside allowed roots: {target}")

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        child = os.path.normcase(str(path))
        base = os.path.normcase(str(root)).rstrip(os.sep)
        return child == base or child.startswith(base + os.sep)