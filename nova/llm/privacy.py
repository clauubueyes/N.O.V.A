from __future__ import annotations

import re
from dataclasses import replace

from nova.llm.base import ChatCompletionRequest, ChatMessage


class PrivacyRedactor:
    """Best-effort redaction before cloud inference; it is not anonymisation."""

    _patterns = (
        (re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{8,}"), "[REDACTED_SECRET]"),
        (re.compile(r"(?i)\bpassword\s*[:=]\s*\S+"), "[REDACTED_PASSWORD]"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_SECRET]"),
        (re.compile(r"(?i)\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S+"), "[REDACTED_SECRET]"),
        (re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+\\[^\s]*|/(?:home|Users)/[^/\s]+/[^\s]*)"), "[LOCAL_PATH]"),
        (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    )

    def redact(self, text: str) -> tuple[str, int]:
        count = 0
        for pattern, replacement in self._patterns:
            text, changed = pattern.subn(replacement, text)
            count += changed
        return text, count

    def request(self, request: ChatCompletionRequest) -> tuple[ChatCompletionRequest, int]:
        messages: list[ChatMessage] = []
        total = 0
        for message in request.messages:
            content, count = self.redact(message.content)
            total += count
            messages.append(ChatMessage(role=message.role, content=content, images=message.images))
        return replace(request, messages=messages), total
