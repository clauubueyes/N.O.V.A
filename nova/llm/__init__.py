from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
    ProviderCapabilities,
    ProviderErrorCode,
)
from nova.llm.gemini import GeminiProvider
from nova.llm.ollama import OllamaProvider
from nova.llm.openai_compatible import OpenAICompatibleProvider, OpenAIProvider
from nova.llm.opencode import OpenCodeProvider
from nova.llm.orchestrator import CloudConfirmationRequired, InferenceOrchestrator
from nova.llm.registry import (
    ProviderRegistry,
    create_provider,
    create_providers,
    registry,
)
from nova.llm.resources import (
    ResourceManager,
    SystemResources,
    default_resource_manager,
)
from nova.llm.router import ModelRouter, RoutingDecision, build_router

registry.register("ollama", OllamaProvider)
registry.register("opencode", OpenCodeProvider)
registry.register("openai", OpenAIProvider)
registry.register("gemini", GeminiProvider)
registry.register("openai_compatible", OpenAICompatibleProvider)

__all__ = [
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "CloudConfirmationRequired",
    "GeminiProvider",
    "InferenceOrchestrator",
    "LLMProvider",
    "ModelInfo",
    "ModelRouter",
    "NOVAProviderError",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "OpenCodeProvider",
    "ProviderCapabilities",
    "ProviderErrorCode",
    "ProviderRegistry",
    "ResourceManager",
    "RoutingDecision",
    "SystemResources",
    "build_router",
    "create_provider",
    "create_providers",
    "default_resource_manager",
    "registry",
]
