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
    GeminiProviderSettings,
    LLMSettings,
    ModelRouterSettings,
    OpenAICompatibleProviderSettings,
    OpenAIProviderSettings,
    OpenCodeProviderSettings,
    PrivacyPolicy,
    RoutingPolicy,
)
from nova.llm.model_registry import ModelLocation, ModelRegistry
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
_LONG_CONTEXT_HINTS = re.compile(
    r"\b(?:proyecto completo|entire project|whole project|long context|contexto largo|"
    r"documento extenso|muchos archivos|large codebase)\b",
    re.IGNORECASE,
)
_SENSITIVE_HINTS = re.compile(
    r"(?i)(?:api[_-]?key|password|token|secret)\s*[:=]|\bsk-[A-Za-z0-9_-]{8,}|"
    r"(?:[A-Z]:\\Users\\|/(?:home|Users)/)"
)


@dataclass(frozen=True)
class RoutingDecision:
    task_kind: str
    model: str
    role: str
    reason: str
    provider: str = "ollama"
    location: str = "local"
    confirmation_required: bool = False
    fallback_allowed: bool = False
    redact: bool = False
    fallbacks: tuple[tuple[str, str], ...] = ()
    fallback_confirmation_required: bool = False


@dataclass(frozen=True)
class TaskRequirements:
    task_kind: str
    context_tokens: int = 0
    capabilities: frozenset[str] = frozenset()
    latency: str = "normal"
    privacy: PrivacyPolicy | None = None


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
        model_registry: ModelRegistry | None = None,
        provider_catalogs: dict[str, dict[str, str]] | None = None,
        provider_defaults: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = dict(catalog)
        self._default = default_model
        self._embedding = embedding_model
        self._resources = resources or ResourceManager()
        self._registry = model_registry or ModelRegistry()
        # PHASE 14 — hybrid mode fields
        self._ai_mode = ai_mode
        self._ai_privacy = ai_privacy
        self._cloud_catalog = dict(cloud_catalog) if cloud_catalog else {}
        self._cloud_default = cloud_default_model
        self._provider_catalogs = dict(provider_catalogs or {})
        self._provider_defaults = dict(provider_defaults or {})
        if cloud_catalog or cloud_default_model:
            self._provider_catalogs.setdefault("opencode", dict(cloud_catalog or {}))
            self._provider_defaults.setdefault("opencode", cloud_default_model)

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

    @property
    def model_registry(self) -> ModelRegistry:
        return self._registry

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
        if res.cpu_count and res.cpu_count <= 2:
            return True
        if res.cpu_percent >= 95.0:
            return True
        if res.gpu_available and 0 < res.gpu_vram_gb < 4.0 and res.ram_available_gb < self._settings.high_resource_ram_gb:
            return True
        if self._settings.battery:
            if res.on_ac_power is False:
                return True
            if res.battery_percent is not None and res.battery_percent < 20.0:
                return True
        return False

    def resource_class(self, res: SystemResources | None = None) -> str:
        res = res or self._resources.snapshot()
        if self._is_heavy_constrained(res):
            return "low_resource"
        if (
            res.ram_total_gb >= self._settings.high_resource_ram_gb
            and (not res.cpu_count or res.cpu_count >= 8)
            and (not res.gpu_available or res.gpu_vram_gb >= 8.0)
        ):
            return "high_resource"
        return "medium_resource"

    def _cloud_available(self) -> bool:
        """True when cloud fallback is permitted and configured."""
        if self._ai_mode != AIMode.hybrid:
            return False
        if self._ai_privacy not in (
            PrivacyPolicy.cloud_allowed,
            PrivacyPolicy.cloud_required,
            PrivacyPolicy.user_confirmation_required,
        ):
            return False
        if self._settings.never_send_data_to_cloud:
            return False
        if self._settings.policy == RoutingPolicy.local:
            return False
        return bool(self._provider_catalogs or self._registry.candidates(location=ModelLocation.cloud))

    def _cloud_for_task(self, task_kind: str) -> tuple[str, str]:
        """Return ``(provider, model)`` for the task, or empty strings.

        Maps task kinds to catalog roles: heavy/general use the ``local`` role
        (the strongest cloud model), coding->coding, vision->vision. Falls back
        to the explicit role key, then the cloud default model.
        """
        providers = list(self._provider_catalogs)
        preferred = self._settings.preferred_cloud_provider
        if preferred in providers:
            providers.remove(preferred)
            providers.insert(0, preferred)
        roles = (_LOCAL, _REASONING) if task_kind in ("heavy", "general", "long_context") else (task_kind,)
        for provider in providers:
            if preferred == provider and self._settings.preferred_cloud_model:
                return provider, self._settings.preferred_cloud_model
            catalog = self._provider_catalogs[provider]
            for role in roles:
                if catalog.get(role):
                    return provider, catalog[role]
            if self._provider_defaults.get(provider):
                return provider, self._provider_defaults[provider]
        candidates = self._registry.candidates(location=ModelLocation.cloud)
        if candidates:
            return candidates[0].provider, candidates[0].model_id
        return "", ""

    def _fallbacks(self, task_kind: str, privacy: PrivacyPolicy) -> tuple[tuple[str, str], ...]:
        if not self._settings.allow_cloud_fallback:
            return ()
        if privacy not in (PrivacyPolicy.cloud_allowed, PrivacyPolicy.cloud_required, PrivacyPolicy.user_confirmation_required):
            return ()
        provider, model = self._cloud_for_task(task_kind)
        return ((provider, model),) if provider and model and self._cloud_available() else ()

    # -- public API ---------------------------------------------------------
    def classify(self, text: str) -> str:
        if _LONG_CONTEXT_HINTS.search(text):
            return "long_context"
        if _VISION_HINTS.search(text) and self._catalog.get(_VISION):
            return "vision"
        if _CODING_HINTS.search(text) and self._catalog.get(_CODING):
            return "coding"
        if _SIMPLE_HINTS.search(text) and self._catalog.get(_SMALL):
            return "simple"
        if _HEAVY_HINTS.search(text):
            return "heavy"
        return "general"

    def analyze(
        self,
        text: str,
        *,
        context_tokens: int = 0,
        require: str | None = None,
        latency: str = "normal",
        privacy: PrivacyPolicy | None = None,
    ) -> TaskRequirements:
        kind = self.classify(text)
        if context_tokens and self._settings.maximum_context and context_tokens > self._settings.maximum_context:
            kind = "long_context"
        capabilities = frozenset({require}) if require else frozenset()
        effective_privacy = privacy or self._ai_privacy
        if self._settings.never_send_sensitive_data_to_cloud and _SENSITIVE_HINTS.search(text):
            effective_privacy = PrivacyPolicy.local_only
        return TaskRequirements(kind, context_tokens, capabilities, latency, effective_privacy)

    def model_for_capability(self, capability: str) -> str | None:
        """Best model for a required capability.

        Preference: configured catalog role -> registry-discovered model -> None.
        """
        role = {"vision": _VISION, "embedding": _EMBEDDING}.get(capability, capability)
        if role in self._catalog:
            return self._catalog[role]
        found = self._registry.first_matching(capability)
        if found:
            return found
        return None

    def route_for(
        self,
        text: str,
        *,
        require: str | None = None,
        task: str | None = None,
        latency: str = "normal",
        context_tokens: int = 0,
        privacy: PrivacyPolicy | None = None,
        preferred_provider: str = "",
        preferred_model: str = "",
    ) -> RoutingDecision:
        requirements = self.analyze(
            text, context_tokens=context_tokens, require=require,
            latency=latency, privacy=privacy,
        )
        kind = (task or requirements.task_kind).lower()
        effective_privacy = requirements.privacy or self._ai_privacy

        if task and task.upper() in ("VOICE", "FAST_INTERACTION", "FAST_RESPONSE"):
            model = self._get(_SMALL, self._default)
            return RoutingDecision(
                task_kind="fast_interaction", model=model, role=_SMALL,
                reason="low-latency interaction requested", provider="ollama",
            )

        if context_tokens and self._settings.maximum_context and context_tokens > self._settings.maximum_context:
            kind = "long_context"

        if preferred_model:
            cloud = preferred_provider and preferred_provider != "ollama"
            if cloud and (self._settings.never_send_data_to_cloud or effective_privacy == PrivacyPolicy.local_only):
                return RoutingDecision(kind, self._default, _LOCAL, "explicit cloud model blocked by privacy policy")
            return RoutingDecision(
                kind, preferred_model, kind, "explicit user model preference",
                provider=preferred_provider or "ollama", location="cloud" if cloud else "local",
                confirmation_required=cloud and (effective_privacy == PrivacyPolicy.user_confirmation_required or self._settings.require_cloud_confirmation),
                fallback_allowed=cloud and self._settings.allow_cloud_fallback,
                redact=cloud and self._settings.redact_cloud_requests,
            )

        if require == "vision":
            # The message actually carries images: the text is not enough to
            # decide; the model MUST be able to see. Fall back to small/general
            # text only when no vision model exists (with a diagnostic reason).
            model = self.model_for_capability("vision")
            if model:
                return RoutingDecision(
                    task_kind="vision",
                    model=model,
                    role=_VISION,
                    reason="message has image attachments; vision model required",
                    provider="ollama",
                )
            return RoutingDecision(
                task_kind="vision",
                model=self._default,
                role=_LOCAL,
                reason="no vision model installed; routing to text default",
                provider="ollama",
            )

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

        if kind in ("heavy", "long_context", "reasoning"):
            res = self._resources.snapshot()
            heavy = self._get(_LOCAL, self._default)
            prefer_cloud = self._settings.policy == RoutingPolicy.performance or kind == "long_context"
            if self._is_heavy_constrained(res) or prefer_cloud:
                # PHASE 14 — cloud fallback for heavy tasks when allowed
                cloud_permitted = effective_privacy in (PrivacyPolicy.cloud_allowed, PrivacyPolicy.cloud_required, PrivacyPolicy.user_confirmation_required)
                if self._cloud_available() and cloud_permitted:
                    cloud_provider, cloud_model = self._cloud_for_task(kind)
                    if cloud_model:
                        return RoutingDecision(
                            task_kind=kind,
                            model=cloud_model,
                            role=_LOCAL,
                            reason=(
                                f"{kind} task selected cloud under "
                                f"{self._settings.policy.value} policy "
                                f"(RAM {res.ram_available_gb:.1f}GB)"
                            ),
                            provider=cloud_provider,
                            location="cloud",
                            confirmation_required=(effective_privacy == PrivacyPolicy.user_confirmation_required or self._settings.require_cloud_confirmation),
                            fallback_allowed=self._settings.allow_cloud_fallback,
                            redact=self._settings.redact_cloud_requests,
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
                fallback_allowed=bool(self._fallbacks(kind, effective_privacy)),
                fallbacks=self._fallbacks(kind, effective_privacy),
                fallback_confirmation_required=(
                    bool(self._fallbacks(kind, effective_privacy))
                    and self._settings.require_cloud_confirmation
                ),
                redact=(
                    bool(self._fallbacks(kind, effective_privacy))
                    and self._settings.redact_cloud_requests
                ),
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
                fallback_allowed=bool(self._fallbacks("general", effective_privacy)),
                fallbacks=self._fallbacks("general", effective_privacy),
                fallback_confirmation_required=(
                    bool(self._fallbacks("general", effective_privacy))
                    and self._settings.require_cloud_confirmation
                ),
                redact=(
                    bool(self._fallbacks("general", effective_privacy))
                    and self._settings.redact_cloud_requests
                ),
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
    model_registry: ModelRegistry | None = None,
    openai: OpenAIProviderSettings | None = None,
    gemini: GeminiProviderSettings | None = None,
    compatible: list[OpenAICompatibleProviderSettings] | None = None,
) -> ModelRouter:
    cloud_catalog = None
    cloud_default = ""
    if open_code:
        cloud_catalog = open_code.models or None
        cloud_default = open_code.default_model
    provider_catalogs: dict[str, dict[str, str]] = {}
    provider_defaults: dict[str, str] = {}
    for name, configured in (("openai", openai), ("gemini", gemini)):
        if configured and configured.enabled:
            provider_catalogs[name] = dict(configured.models)
            provider_defaults[name] = configured.default_model
    for configured in compatible or []:
        if configured.enabled:
            provider_catalogs[configured.name] = dict(configured.models)
            provider_defaults[configured.name] = configured.default_model
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
        model_registry=model_registry,
        provider_catalogs=provider_catalogs,
        provider_defaults=provider_defaults,
    )
