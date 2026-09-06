from nova.core.session import ChatSession


def test_system_message_preserved_and_history_trimmed() -> None:
    session = ChatSession(max_history_messages=3, system_prompt="system prompt")
    for i in range(6):
        session.add_user(f"q{i}")
        session.add_assistant(f"a{i}")

    messages = session.messages()
    assert messages[0].role == "system"
    assert messages[0].content == "system prompt"
    assert len(messages) == 1 + 3
    assert messages[-1].content == "a5"


def test_build_request_contains_messages() -> None:
    session = ChatSession(max_history_messages=5, system_prompt="sys")
    session.add_user("hi")
    request = session.build_request(model="qwen2.5-coder:7b")
    assert request.model == "qwen2.5-coder:7b"
    assert [m.role for m in request.messages] == ["system", "user"]


def test_clear_keeps_system_prompt() -> None:
    session = ChatSession(max_history_messages=5, system_prompt="sys")
    session.add_user("hello")
    session.add_assistant("hi")
    session.clear()
    assert [m.role for m in session.messages()] == ["system"]


def test_invalid_max_history_rejected() -> None:
    try:
        ChatSession(max_history_messages=0, system_prompt="sys")
    except ValueError:
        return
    raise AssertionError("expected ValueError for max_history_messages < 1")