from __future__ import annotations

from typing import Type

from nova.core.config import LLMSettings
from nova.llm.base import LLMProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Type[LLMProvider]] = {}

    def register(self, name: str, provider_cls: Type[LLMProvider]) -> None:
        self._providers[name] = provider_cls

    def get(self, name: str) -> Type[LLMProvider]:
        try:
            return self._providers[name]
        except KeyError:
            raise ValueError(
                f"Unknown LLM provider: {name!r}. Available: {sorted(self._providers)}"
            ) from None


registry = ProviderRegistry()


def create_provider(settings: LLMSettings) -> LLMProvider:
    """Build a provider instance from the active registry."""
    provider_cls = registry.get(settings.provider)
    return provider_cls(settings=settings)