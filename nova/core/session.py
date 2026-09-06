from __future__ import annotations

from nova.llm.base import ChatCompletionRequest, ChatMessage


class ChatSession:
    def __init__(self, *, max_history_messages: int, system_prompt: str) -> None:
        if max_history_messages < 1:
            raise ValueError("max_history_messages must be >= 1")
        self._max = max_history_messages
        self._messages: list[ChatMessage] = []
        if system_prompt:
            self._messages.append(ChatMessage(role="system", content=system_prompt))

    def add_user(self, content: str) -> None:
        self._append("user", content)

    def add_assistant(self, content: str) -> None:
        self._append("assistant", content)

    def _append(self, role: str, content: str) -> None:
        self._messages.append(ChatMessage(role=role, content=content))
        self._trim()

    def _trim(self) -> None:
        system = [m for m in self._messages if m.role == "system"]
        rest = [m for m in self._messages if m.role != "system"]
        if len(rest) > self._max:
            rest = rest[-self._max:]
        self._messages = system + rest

    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def build_request(self, model: str | None = None) -> ChatCompletionRequest:
        return ChatCompletionRequest(messages=self.messages(), model=model)

    def clear(self) -> None:
        self._messages = [m for m in self._messages if m.role == "system"]