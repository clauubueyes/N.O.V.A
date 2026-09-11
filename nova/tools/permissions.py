from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from nova.core.config import AutonomyLevel, PermissionSettings

TOOL_CATEGORIES = {
    'read_file': 'reading', 'list_files': 'reading', 'list_dir': 'reading',
    'write_file': 'writing', 'delete_file': 'writing', 'open_app': 'applications',
    'open_url': 'applications', 'close_app': 'applications', 'run': 'commands',
    'install_software': 'system', 'system_config': 'system',
}
SENSITIVE_TOOLS = {'write_file', 'delete_file', 'run', 'install_software', 'system_config', 'close_app'}


class PermissionDecision(Enum):
    ALLOW = auto()
    DENY = auto()
    ASK = auto()


@dataclass(frozen=True)
class Authorization:
    decision: PermissionDecision
    reason: str


class PermissionSystem:
    """Decides whether a tool may run based on rules and the autonomy level.

    Rule precedence: explicit `deny` > explicit `allow` > autonomy level.
    """

    def __init__(self, settings: PermissionSettings) -> None:
        self._settings = settings

    @property
    def autonomy(self) -> AutonomyLevel:
        return self._settings.autonomy

    def authorize(self, tool_name: str) -> Authorization:
        if tool_name in self._settings.deny:
            return Authorization(PermissionDecision.DENY, f"denied by rule: {tool_name}")
        category = self._settings.categories.get(TOOL_CATEGORIES.get(tool_name, ''))
        if category == 'deny':
            return Authorization(PermissionDecision.DENY, 'denied by category')
        if category == 'ask':
            return Authorization(PermissionDecision.ASK, 'confirmation required by category')
        if category == 'allow':
            if tool_name in SENSITIVE_TOOLS:
                return Authorization(PermissionDecision.ASK, 'sensitive action requires confirmation')
            return Authorization(PermissionDecision.ALLOW, 'allowed by category')
        if tool_name in self._settings.allow:
            return Authorization(PermissionDecision.ALLOW, "allowed by rule")
        if self.autonomy is AutonomyLevel.off:
            return Authorization(PermissionDecision.DENY, "autonomy off (no allow rule)")
        if self.autonomy is AutonomyLevel.full:
            return Authorization(PermissionDecision.ALLOW, "autonomy full")
        return Authorization(PermissionDecision.ASK, "autonomy ask")
