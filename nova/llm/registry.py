from __future__ import annotations

from nova.core.config import LLMSettings, NovaSettings, OpenCodeProviderSettings
from nova.llm.base import LLMProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, type[LLMProvider]] = {}

    def register(self, name: str, provider_cls: type[LLMProvider]) -> None:
        self._providers[name] = provider_cls

    def get(self, name: str) -> type[LLMProvider]:
        try:
            return self._providers[name]
        except KeyError:
            raise ValueError(
                f"Unknown LLM provider: {name!r}. Available: {sorted(self._providers)}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._providers)


registry = ProviderRegistry()


def create_provider(
    settings: LLMSettings,
    open_code_settings: OpenCodeProviderSettings | None = None,
) -> LLMProvider:
    """Build a provider instance from the active registry.

    When the configured provider is ``opencode``, the factory uses
    ``open_code_settings`` (if provided) instead of ``LLMSettings``.
    """
    provider_cls = registry.get(settings.provider)
    if settings.provider == "opencode" and open_code_settings is not None:
        return provider_cls(settings=open_code_settings)
    return provider_cls(settings=settings)


def create_providers(
    settings: NovaSettings,
    *,
    local_provider: LLMProvider | None = None,
) -> dict[str, LLMProvider]:
    """Instantiate every explicitly enabled provider without probing the network."""
    from nova.llm.gemini import GeminiProvider
    from nova.llm.openai_compatible import OpenAICompatibleProvider, OpenAIProvider
    from nova.llm.opencode import OpenCodeProvider

    providers: dict[str, LLMProvider] = {
        settings.llm.provider: local_provider or create_provider(settings.llm)
    }
    if settings.openai.enabled and settings.openai.resolve_api_key():
        providers["openai"] = OpenAIProvider(settings.openai)
    if settings.gemini.enabled and settings.gemini.resolve_api_key():
        providers["gemini"] = GeminiProvider(settings.gemini)
    if settings.open_code.enabled:
        providers["opencode"] = OpenCodeProvider(settings.open_code)
    for item in settings.openai_compatible:
        if item.enabled and (item.location == "local" or item.resolve_api_key()):
            providers[item.name] = OpenAICompatibleProvider(item)
    return providers
