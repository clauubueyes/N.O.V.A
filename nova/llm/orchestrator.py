from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from typing import Any

from nova.core.audit import AuditLog
from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    LLMProvider,
    NOVAProviderError,
    StreamCancellation,
)
from nova.llm.privacy import PrivacyRedactor
from nova.llm.router import RoutingDecision


class CloudConfirmationRequired(NOVAProviderError):
    pass


class InferenceOrchestrator:
    """Executes routing decisions without leaking provider details into callers."""

    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        *,
        local_provider: str = "ollama",
        audit: AuditLog | None = None,
        redactor: PrivacyRedactor | None = None,
    ) -> None:
        self.providers = dict(providers)
        self.local_provider = local_provider
        self.audit = audit
        self.redactor = redactor or PrivacyRedactor()

    def provider_for(self, decision: RoutingDecision) -> LLMProvider:
        provider = self.providers.get(decision.provider)
        if provider is None:
            raise NOVAProviderError(f"Provider {decision.provider!r} is not configured", provider=decision.provider)
        return provider

    def prepare(self, request: ChatCompletionRequest, decision: RoutingDecision) -> tuple[ChatCompletionRequest, int]:
        if decision.location != "cloud" or not decision.redact:
            return request, 0
        return self.redactor.request(request)

    def _record(self, decision: RoutingDecision, *, ok: bool, fallback: bool, duration_ms: float, redactions: int = 0, usage: dict[str, Any] | None = None) -> None:
        if self.audit is None:
            return
        self.audit.record(
            tool="model_router",
            action="inference",
            decision="allow" if ok else "error",
            args={
                "task_type": decision.task_kind,
                "provider": decision.provider,
                "model": decision.model,
                "location": decision.location,
                "routing_reason": decision.reason,
                "fallback": fallback,
                "redactions": redactions,
            },
            ok=ok,
            data={"requests": 1, "usage": usage} if usage else {"requests": 1},
            duration_ms=duration_ms,
        )

    def chat(
        self,
        request: ChatCompletionRequest,
        decision: RoutingDecision,
        *,
        cloud_confirmed: bool = False,
    ) -> ChatCompletionResponse:
        if decision.confirmation_required and not cloud_confirmed:
            raise CloudConfirmationRequired("Cloud routing requires explicit user confirmation", provider=decision.provider)
        prepared, redactions = self.prepare(request, decision)
        started = time.monotonic()
        try:
            response = self.provider_for(decision).chat(prepared)
        except NOVAProviderError as primary_error:
            local = self.providers.get(self.local_provider)
            if decision.location == "local" and decision.fallbacks:
                if decision.fallback_confirmation_required and not cloud_confirmed:
                    raise CloudConfirmationRequired(
                        "Cloud fallback requires explicit user confirmation",
                        provider=decision.fallbacks[0][0],
                    ) from primary_error
                for fallback_provider, fallback_model in decision.fallbacks:
                    target = self.providers.get(fallback_provider)
                    if target is None:
                        continue
                    fallback_request = ChatCompletionRequest(
                        messages=request.messages, model=fallback_model,
                        temperature=request.temperature, max_tokens=request.max_tokens,
                        tools=request.tools, response_format=request.response_format,
                    )
                    fallback_request, fallback_redactions = self.redactor.request(fallback_request) if decision.redact else (fallback_request, 0)
                    try:
                        response = target.chat(fallback_request)
                    except NOVAProviderError:
                        continue
                    self._record(decision, ok=True, fallback=True, duration_ms=(time.monotonic() - started) * 1000, redactions=fallback_redactions, usage=response.usage)
                    return response
            if decision.location != "cloud" or local is None:
                self._record(decision, ok=False, fallback=False, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions)
                raise
            fallback_request = ChatCompletionRequest(
                messages=request.messages,
                model=getattr(getattr(local, "_settings", None), "default_model", None),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                response_format=request.response_format,
            )
            response = local.chat(fallback_request)
            self._record(decision, ok=True, fallback=True, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions, usage=response.usage)
            return response
        self._record(decision, ok=True, fallback=False, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions, usage=response.usage)
        return response

    def stream(
        self,
        request: ChatCompletionRequest,
        decision: RoutingDecision,
        *,
        cancellation: StreamCancellation | None = None,
        cloud_confirmed: bool = False,
    ) -> Iterator[dict[str, Any]]:
        if decision.confirmation_required and not cloud_confirmed:
            raise CloudConfirmationRequired("Cloud routing requires explicit user confirmation", provider=decision.provider)
        prepared, redactions = self.prepare(request, decision)
        started = time.monotonic()
        emitted = False
        usage: dict[str, Any] | None = None
        try:
            for frame in self.provider_for(decision).stream(prepared, cancellation=cancellation):
                emitted = emitted or bool(frame.get("content"))
                if frame.get("usage"):
                    usage = frame["usage"]
                yield frame
        except NOVAProviderError:
            local = self.providers.get(self.local_provider)
            if decision.location == "local" and decision.fallbacks and not emitted:
                if decision.fallback_confirmation_required and not cloud_confirmed:
                    raise CloudConfirmationRequired(
                        "Cloud fallback requires explicit user confirmation",
                        provider=decision.fallbacks[0][0],
                    )
                for fallback_provider, fallback_model in decision.fallbacks:
                    target = self.providers.get(fallback_provider)
                    if target is None:
                        continue
                    fallback_request = ChatCompletionRequest(
                        messages=request.messages, model=fallback_model,
                        temperature=request.temperature, max_tokens=request.max_tokens,
                        tools=request.tools, response_format=request.response_format,
                    )
                    fallback_request, fallback_redactions = self.redactor.request(fallback_request) if decision.redact else (fallback_request, 0)
                    candidate_emitted = False
                    try:
                        yield {"provider": fallback_provider}
                        for frame in target.stream(fallback_request, cancellation=cancellation):
                            candidate_emitted = candidate_emitted or bool(frame.get("content"))
                            yield frame
                    except NOVAProviderError:
                        if candidate_emitted:
                            raise
                        continue
                    self._record(decision, ok=True, fallback=True, duration_ms=(time.monotonic() - started) * 1000, redactions=fallback_redactions)
                    return
            if emitted or decision.location != "cloud" or local is None:
                self._record(decision, ok=False, fallback=False, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions)
                raise
            fallback_request = ChatCompletionRequest(
                messages=request.messages,
                model=getattr(getattr(local, "_settings", None), "default_model", None),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                response_format=request.response_format,
            )
            yield {"provider": self.local_provider}
            for frame in local.stream(fallback_request, cancellation=cancellation):
                yield frame
            self._record(decision, ok=True, fallback=True, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions)
            return
        self._record(decision, ok=True, fallback=False, duration_ms=(time.monotonic() - started) * 1000, redactions=redactions, usage=usage)
