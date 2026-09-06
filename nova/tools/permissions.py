from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from nova.core.config import AutonomyLevel, PermissionSettings


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
        self._deny = set(settings.deny)
        self._allow = set(settings.allow)

    @property
    def autonomy(self) -> AutonomyLevel:
        return self._settings.autonomy

    def authorize(self, tool_name: str) -> Authorization:
        if tool_name in self._deny:
            return Authorization(PermissionDecision.DENY, f"denied by rule: {tool_name}")
        if tool_name in self._allow:
            return Authorization(PermissionDecision.ALLOW, "allowed by rule")
        if self.autonomy is AutonomyLevel.off:
            return Authorization(PermissionDecision.DENY, "autonomy off (no allow rule)")
        if self.autonomy is AutonomyLevel.full:
            return Authorization(PermissionDecision.ALLOW, "autonomy full")
        return Authorization(PermissionDecision.ASK, "autonomy ask")