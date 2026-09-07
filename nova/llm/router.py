from __future__ import annotations

"""PHASE 7 — Model Router.

Picks the model for a task according to its complexity/kind, the available
resources and the privacy policy. The LLM never selects its own model: routing is
a Core decision so models stay local-first (ADR-013) and degrade gracefully when
there is no adequate local model.
"""

import re
from dataclasses import dataclass

from nova.core.config import LLMSettings, ModelRouterSettings
from nova.llm.resources import ResourceManager, SystemResources

# Roles the router understands (keys of the `llm.models` catalog).
_SMALL = "small"
_LOCAL = "local"
_CODING = "coding"
_VISION = "vision"
_EMBEDDING = "embedding"

_HEAVY_HINTS = re.compile(
    r"\b(?:diagnost|review|analyse|analy|análisis|analis|architect|design|refactor|debug|"
    r"explain|explica|resume|summar|complex|complej|complicat|large|big|risky|"
    r"profund|estudia)\w*\b",
    re.IGNORECASE,
)
_SIMPLE_HINTS = re.compile(
    r"\b(?:hello|saluda|presenta|gracias|thanks|hola|hey|ok|bye|date|fecha|"
    r"goodbye|adios)\w*\b|\b(?:hi|si|sí|no|what time)\b",
    re.IGNORECASE,
)
_CODING_HINTS = re.compile(
    r"\b(?:code|coding|python|script|bug|fix|function|class|refactor|"
    r"implement|[A-Za-z_]+\.py|carpeta|proyecto|proyect|función|funcion)\w*\b",
    re.IGNORECASE,
)
_VISION_HINTS = re.compile(
    r"\b(?:image|imagen|photo|foto|picture|vision|see|ver|look|mira|describe)\w*\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RoutingDecision:
    task_kind: str
    model: str
    role: str
    reason: str


class ModelRouter:
    """Classifies a task and chooses a model from the configured catalog."""

    def __init__(
        self,
        settings: ModelRouterSettings,
        catalog: dict[str, str],
        default_model: str,
        embedding_model: str,
        resources: ResourceManager | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = dict(catalog)
        self._default = default_model
        self._embedding = embedding_model
        self._resources = resources or ResourceManager()

    # -- helpers ------------------------------------------------------------
    def catalog(self) -> dict[str, str]:
        return dict(self._catalog)

    @property
    def default_model(self) -> str:
        return self._default

    def _get(self, role: str, fallback: str) -> str:
        return self._catalog.get(role) or fallback

    def _is_heavy_constrained(self, res: SystemResources) -> bool:
        """A heavy model should be avoided on low RAM, low battery or no AC."""
        if not self._catalog.get(_LOCAL) and not self._catalog.get(_CODING):
            return False
        min_ram = self._settings.min_ram_gb
        if res.ram_available_gb > 0 and res.ram_available_gb < min_ram:
            return True
        if self._settings.battery:
            if res.on_ac_power is False:
                return True
            if res.battery_percent is not None and res.battery_percent < 20.0:
                return True
        return False

    # -- public API ---------------------------------------------------------
    def classify(self, text: str) -> str:
        if _VISION_HINTS.search(text) and self._catalog.get(_VISION):
            return "vision"
        if _CODING_HINTS.search(text) and self._catalog.get(_CODING):
            return "coding"
        if _SIMPLE_HINTS.search(text) and self._catalog.get(_SMALL):
            return "simple"
        if _HEAVY_HINTS.search(text):
            return "heavy"
        return "general"

    def route_for(self, text: str) -> RoutingDecision:
        kind = self.classify(text)
        if kind == "simple":
            return RoutingDecision(
                task_kind=kind,
                model=self._get(_SMALL, self._default),
                role=_SMALL,
                reason=f"simple task classified as '{kind}'",
            )

        if kind in ("coding", "vision"):
            role = _CODING if kind == "coding" else _VISION
            model = self._get(role, self._default)
            res = self._resources.snapshot()
            if self._is_heavy_constrained(res) and self._catalog.get(_SMALL):
                return RoutingDecision(
                    task_kind=kind,
                    model=self._get(_SMALL, self._default),
                    role=_SMALL,
                    reason=(
                        f"'{role}' role unavailable under current resources "
                        f"(RAM {res.ram_available_gb:.1f}GB), falling back to small"
                    ),
                )
            return RoutingDecision(
                task_kind=kind, model=model, role=role, reason=f"task classified as '{kind}'"
            )

        if kind == "heavy":
            res = self._resources.snapshot()
            heavy = self._get(_LOCAL, self._default)
            if self._is_heavy_constrained(res) and self._catalog.get(_SMALL):
                return RoutingDecision(
                    task_kind=kind,
                    model=self._get(_SMALL, self._default),
                    role=_SMALL,
                    reason=(
                        f"heavy role constrained by resources "
                        f"(RAM {res.ram_available_gb:.1f}GB, ac={res.on_ac_power}), using small"
                    ),
                )
            return RoutingDecision(
                task_kind=kind, model=heavy, role=_LOCAL, reason=f"heavy task classified as '{kind}'"
            )

        # general
        res = self._resources.snapshot()
        if self._catalog.get(_LOCAL) and not self._is_heavy_constrained(res):
            return RoutingDecision(
                task_kind="general",
                model=self._get(_LOCAL, self._default),
                role=_LOCAL,
                reason="general task routed to local model",
            )
        return RoutingDecision(
            task_kind="general",
            model=self._default,
            role=_LOCAL,
            reason="general task routed to default model",
        )


def build_router(
    llm: LLMSettings,
    router: ModelRouterSettings,
    resources: ResourceManager | None = None,
) -> ModelRouter:
    return ModelRouter(
        settings=router,
        catalog=llm.models,
        default_model=llm.default_model,
        embedding_model=llm.embedding_model,
        resources=resources,
    )
