from __future__ import annotations

"""PHASE 7 + 14 — Model Router.

Picks the model for a task according to its complexity/kind, the available
resources and the privacy policy. The LLM never selects its own model: routing is
a Core decision so models stay local-first (ADR-013) and degrade gracefully when
there is no adequate local model.

PHASE 14 — Provider-aware routing.  When ``ai.mode`` is ``hybrid`` and
``ai.privacy`` is ``cloud_allowed``, the router may route heavy tasks to the
OpenCode cloud provider if local capacity is insufficient.  LOCAL mode and
``local_only`` privacy always force Ollama.
"""

import re
from dataclasses import dataclass

from nova.core.config import (
    AIMode,
    LLMSettings,
    ModelRouterSettings,
    OpenCodeProviderSettings,
    PrivacyPolicy,
)
from nova.llm.resources import ResourceManager, SystemResources

# Roles the router understands (keys of the `llm.models` catalog).
_SMALL = "small"
_LOCAL = "local"
_CODING = "coding"
_VISION = "vision"
_EMBEDDING = "embedding"
_REASONING = "reasoning"

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
    provider: str = "ollama"


class ModelRouter:
    """Classifies a task and chooses a provider + model from the configured catalog."""

    def __init__(
        self,
        settings: ModelRouterSettings,
        catalog: dict[str, str],
        default_model: str,
        embedding_model: str,
        resources: ResourceManager | None = None,
        ai_mode: AIMode = AIMode.local,
        ai_privacy: PrivacyPolicy = PrivacyPolicy.local_only,
        cloud_catalog: dict[str, str] | None = None,
        cloud_default_model: str = "",
    ) -> None:
        self._settings = settings
        self._catalog = dict(catalog)
        self._default = default_model
        self._embedding = embedding_model
        self._resources = resources or ResourceManager()
        # PHASE 14 — hybrid mode fields
        self._ai_mode = ai_mode
        self._ai_privacy = ai_privacy
        self._cloud_catalog = dict(cloud_catalog) if cloud_catalog else {}
        self._cloud_default = cloud_default_model

    # -- helpers ------------------------------------------------------------
    def catalog(self) -> dict[str, str]:
        return dict(self._catalog)

    @property
    def default_model(self) -> str:
        return self._default

    def validate_against(self, installed_models: list[str]) -> list[str]:
        """Remove catalog entries whose models are not in *installed_models*.

        Returns a list of human-readable warnings for every role that was
        disabled.  The ``embedding`` role is always kept (used by
        MemoryService, not the chat router).  If a role is removed but has
        a close candidate in *installed_models*, the warning suggests it.
        """
        installed = {m.strip().lower() for m in installed_models}
        warnings: list[str] = []

        for role in list(self._catalog):
            if role == _EMBEDDING:
                continue
            model = self._catalog[role]
            if model.strip().lower() not in installed:
                del self._catalog[role]
                candidates = [
                    m for m in installed_models
                    if model.split(":")[0].lower() in m.lower()
                ]
                hint = f" Closest: {candidates[0]}." if candidates else ""
                warnings.append(
                    f'Model "{model}" not installed — role "{role}" disabled.{hint}'
                )

        if not self._catalog.get(_LOCAL) and not self._catalog.get(_SMALL):
            # Try to auto-select the default model if it exists
            if self._default.strip().lower() in installed:
                self._catalog[_LOCAL] = self._default
                warnings.append(
                    f'Auto-selected "{self._default}" as primary model.'
                )
            else:
                for m in installed_models:
                    if m.strip().lower() != self._embedding.strip().lower():
                        self._catalog[_LOCAL] = m
                        warnings.append(
                            f'Auto-selected "{m}" as primary model.'
                        )
                        break

        return warnings

    @property
    def ai_mode(self) -> AIMode:
        return self._ai_mode

    @property
    def ai_privacy(self) -> PrivacyPolicy:
        return self._ai_privacy

    def _get(self, role: str, fallback: str) -> str:
        return self._catalog.get(role) or fallback

    def _cloud_get(self, role: str) -> str:
        """Return the cloud model for a role, or empty string."""
        return self._cloud_catalog.get(role, "")

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

    def _cloud_available(self) -> bool:
        """True when cloud fallback is permitted and configured."""
        if self._ai_mode != AIMode.hybrid:
            return False
        if self._ai_privacy != PrivacyPolicy.cloud_allowed:
            return False
        if not self._cloud_catalog and not self._cloud_default:
            return False
        return True

    def _cloud_for_task(self, task_kind: str) -> str:
        """Return a cloud model for the given task kind, or empty string.

        Maps task kinds to catalog roles: heavy/general use the ``local`` role
        (the strongest cloud model), coding->coding, vision->vision. Falls back
        to the explicit role key, then the cloud default model.
        """
        # Prefer the most capable cloud model for heavy/reasoning-like tasks.
        if task_kind in ("heavy", "general"):
            for role in (_LOCAL, _REASONING):
                cloud_model = self._cloud_get(role)
                if cloud_model:
                    return cloud_model
        cloud_model = self._cloud_get(task_kind)
        if cloud_model:
            return cloud_model
        # Fallback: use the cloud default model for any task.
        return self._cloud_default

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
                provider="ollama",
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
                    provider="ollama",
                )
            return RoutingDecision(
                task_kind=kind, model=model, role=role,
                reason=f"task classified as '{kind}'",
                provider="ollama",
            )

        if kind == "heavy":
            res = self._resources.snapshot()
            heavy = self._get(_LOCAL, self._default)
            if self._is_heavy_constrained(res):
                # PHASE 14 — cloud fallback for heavy tasks when allowed
                if self._cloud_available():
                    cloud_model = self._cloud_for_task(kind)
                    if cloud_model:
                        return RoutingDecision(
                            task_kind=kind,
                            model=cloud_model,
                            role=_LOCAL,
                            reason=(
                                f"heavy task constrained by local resources "
                                f"(RAM {res.ram_available_gb:.1f}GB), "
                                f"falling back to cloud"
                            ),
                            provider="opencode",
                        )
                # Fall back to small local model
                if self._catalog.get(_SMALL):
                    return RoutingDecision(
                        task_kind=kind,
                        model=self._get(_SMALL, self._default),
                        role=_SMALL,
                        reason=(
                            f"heavy role constrained by resources "
                            f"(RAM {res.ram_available_gb:.1f}GB, ac={res.on_ac_power}), using small"
                        ),
                        provider="ollama",
                    )
            return RoutingDecision(
                task_kind=kind, model=heavy, role=_LOCAL,
                reason=f"heavy task classified as '{kind}'",
                provider="ollama",
            )

        # general
        res = self._resources.snapshot()
        if self._catalog.get(_LOCAL) and not self._is_heavy_constrained(res):
            return RoutingDecision(
                task_kind="general",
                model=self._get(_LOCAL, self._default),
                role=_LOCAL,
                reason="general task routed to local model",
                provider="ollama",
            )
        return RoutingDecision(
            task_kind="general",
            model=self._default,
            role=_LOCAL,
            reason="general task routed to default model",
            provider="ollama",
        )


def build_router(
    llm: LLMSettings,
    router: ModelRouterSettings,
    resources: ResourceManager | None = None,
    ai_mode: AIMode = AIMode.local,
    ai_privacy: PrivacyPolicy = PrivacyPolicy.local_only,
    open_code: OpenCodeProviderSettings | None = None,
) -> ModelRouter:
    cloud_catalog = None
    cloud_default = ""
    if open_code:
        cloud_catalog = open_code.models or None
        cloud_default = open_code.default_model
    return ModelRouter(
        settings=router,
        catalog=llm.models,
        default_model=llm.default_model,
        embedding_model=llm.embedding_model,
        resources=resources,
        ai_mode=ai_mode,
        ai_privacy=ai_privacy,
        cloud_catalog=cloud_catalog,
        cloud_default_model=cloud_default,
    )
