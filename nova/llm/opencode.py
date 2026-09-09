from __future__ import annotations

"""PHASE 14 — OpenCode cloud provider backend.

Connects to a running ``opencode serve`` instance over HTTP and exposes the
same ``LLMProvider`` interface that N.O.V.A. uses internally.  The integration
is intentionally thin: it creates a disposable OpenCode session for each
``chat()`` call, sends the prompt, reads the assistant response, and tears
the session down.  This avoids any long-lived state between N.O.V.A. and
OpenCode while keeping the implementation dependency-free (httpx only).

OpenCode server API docs: https://opencode.ai/docs/server/
"""

import uuid

import httpx

from nova.core.config import OpenCodeProviderSettings
from nova.core.logging import get_logger
from nova.llm.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    LLMProvider,
    ModelInfo,
    NOVAProviderError,
)

logger = get_logger("llm.opencode")


class OpenCodeProvider(LLMProvider):
    """LLMProvider backed by an OpenCode HTTP server.

    Authentication is delegated to OpenCode itself (``~/.local/share/opencode/auth.json``
    or environment variables).  N.O.V.A. never handles API keys directly.
    """

    supports_embedding = False  # OpenCode does not expose an embedding endpoint.

    def __init__(
        self,
        settings: OpenCodeProviderSettings,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
        )

    # -- helpers ---------------------------------------------------------------

    def _post(self, path: str, json: dict | None = None) -> dict:
        """POST to the OpenCode server, raising ``NOVAProviderError`` on failure."""
        try:
            resp = self._client.post(path, json=json or {})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"OpenCode returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach OpenCode at {self._settings.base_url}: {exc}"
            ) from exc
        try:
            return resp.json()
        except Exception as exc:
            raise NOVAProviderError(
                f"OpenCode returned non-JSON response: {resp.text[:500]}"
            ) from exc

    def _get(self, path: str) -> dict:
        """GET from the OpenCode server, raising ``NOVAProviderError`` on failure."""
        try:
            resp = self._client.get(path)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NOVAProviderError(
                f"OpenCode returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc
        except httpx.RequestError as exc:
            raise NOVAProviderError(
                f"Could not reach OpenCode at {self._settings.base_url}: {exc}"
            ) from exc
        try:
            return resp.json()
        except Exception as exc:
            raise NOVAProviderError(
                f"OpenCode returned non-JSON response: {resp.text[:500]}"
            ) from exc

    # -- LLMProvider interface -------------------------------------------------

    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send a chat request through an ephemeral OpenCode session.

        Flow:
        1. Create a disposable session.
        2. Send the conversation as a single prompt.
        3. Extract the assistant response text.
        4. Delete the session (best-effort).
        """
        model_str = request.model or self._settings.default_model
        provider_id, model_id = _parse_model(model_str)

        # Build the prompt text from the conversation messages.
        prompt_text = _messages_to_prompt(request.messages)

        # 1) Create session
        session_id = _create_session(self._post, title="NOVA-hybrid")

        try:
            # 2) Send prompt
            body: dict = {
                "parts": [{"type": "text", "text": prompt_text}],
            }
            if provider_id and model_id:
                body["model"] = {"providerID": provider_id, "modelID": model_id}

            result = self._post(f"/session/{session_id}/message", json=body)

            # 3) Extract response
            parts = result.get("parts", [])
            answer_text = ""
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    answer_text += part.get("text", "")

            if not answer_text:
                raise NOVAProviderError(
                    "OpenCode returned an empty response (no text parts)."
                )

            return ChatCompletionResponse(
                message=ChatMessage(role="assistant", content=answer_text),
                model=model_str,
                usage=None,
            )
        finally:
            # 4) Best-effort session cleanup
            try:
                self._client.delete(f"/session/{session_id}")
            except Exception:  # noqa: BLE001
                pass

    def list_models(self) -> list[ModelInfo]:
        """List models from all connected OpenCode providers."""
        try:
            data = self._get("/config/providers")
        except NOVAProviderError:
            return []

        providers = data.get("providers", [])
        defaults = data.get("default", {})
        models: list[ModelInfo] = []
        for provider in providers:
            provider_id = provider.get("id", "")
            provider_models = provider.get("models", [])
            if isinstance(provider_models, dict):
                provider_models = [
                    {"id": k, "name": v.get("name", k) if isinstance(v, dict) else k}
                    for k, v in provider_models.items()
                ]
            for m in provider_models:
                if isinstance(m, dict):
                    mid = m.get("id", "")
                    mname = m.get("name", mid)
                else:
                    mid = str(m)
                    mname = mid
                models.append(
                    ModelInfo(
                        name=f"{provider_id}/{mid}",
                        size=0,
                        modified_at=mname,
                    )
                )
        return models

    def health(self) -> bool:
        """Return True when the OpenCode server is reachable and healthy."""
        try:
            data = self._get("/global/health")
            return bool(data.get("healthy", False))
        except (NOVAProviderError, Exception):  # noqa: BLE001
            return False

    def close(self) -> None:
        self._client.close()


# -- internal helpers ----------------------------------------------------------

def _parse_model(model_str: str) -> tuple[str, str]:
    """Parse ``'provider/model'`` into ``(provider_id, model_id)``.

    Falls back to ``('', model_str)`` when no slash is present.
    """
    if "/" in model_str:
        provider_id, _, model_id = model_str.partition("/")
        return provider_id.strip(), model_id.strip()
    return "", model_str.strip()


def _messages_to_prompt(messages: list[ChatMessage]) -> str:
    """Flatten a list of chat messages into a single prompt string.

    System messages are prepended as context.  The last user message is
    the actual prompt.
    """
    system_parts: list[str] = []
    user_parts: list[str] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        elif msg.role == "user":
            user_parts.append(msg.content)
        elif msg.role == "assistant":
            # Include assistant context in the prompt so OpenCode can follow.
            user_parts.append(f"Assistant: {msg.content}")

    parts: list[str] = []
    if system_parts:
        parts.append("[System context]\n" + "\n".join(system_parts))
    parts.extend(user_parts)
    return "\n\n".join(parts) if parts else ""


def _create_session(post_fn, title: str = "NOVA") -> str:
    """Create an OpenCode session and return its ID."""
    result = post_fn("/session", json={"title": title})
    session_id = result.get("id", "")
    if not session_id:
        raise NOVAProviderError("OpenCode did not return a session ID.")
    return session_id
