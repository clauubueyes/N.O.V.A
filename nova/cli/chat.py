from __future__ import annotations

import sys

from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging
from nova.core.session import ChatSession
from nova.llm.base import NOVAProviderError
from nova.llm.registry import create_provider

BANNER = """\
+--------------------------------------------------------------+
| N.O.V.A. - Neural Operations & Virtual Assistant              |
| Phase 1 - N.O.V.A. Core | local LLM via Ollama               |
+--------------------------------------------------------------+"""

HELP = """\
Commands:
  /exit             quit (also Ctrl+C or Ctrl+Z)
  /clear            clear conversation context (keeps system prompt)
  /models           list available Ollama models
  /model <name>     switch model for current session
  /help             show this help"""


def main() -> int:
    settings = load_settings()
    setup_logging(settings.logging)
    logger = get_logger("cli.chat")

    provider = create_provider(settings.llm)
    session = ChatSession(
        max_history_messages=settings.session.max_history_messages,
        system_prompt=settings.session.system_prompt,
    )
    current_model = settings.llm.default_model

    print(BANNER)
    print(f"Model: {current_model}. Type /help for commands.")
    logger.info("session started, model=%s", current_model)

    try:
        while True:
            try:
                line = input("You   > ").strip()
            except EOFError:
                print("Bye.")
                break
            except KeyboardInterrupt:
                print("\nBye.")
                break
            if not line:
                continue
            if line.startswith("/"):
                parts = line[1:].strip().split()
                command = parts[0].lower() if parts else ""
                if command in ("exit", "quit"):
                    print("Bye.")
                    break
                elif command == "clear":
                    session.clear()
                    print("[context cleared]")
                elif command == "models":
                    try:
                        models = provider.list_models()
                    except NOVAProviderError as exc:
                        print(f"[nova error] {exc}")
                        continue
                    if not models:
                        print("No models. Run: ollama pull <model>")
                        continue
                    for model_info in models:
                        size_mb = model_info.size / (1024**2)
                        print(f"  - {model_info.name:<28} {size_mb:,.0f} MB")
                elif command == "model":
                    if len(parts) >= 2:
                        current_model = parts[1]
                        print(f"[model -> {current_model}]")
                    else:
                        print(f"Current model: {current_model}")
                elif command == "help":
                    print(HELP)
                else:
                    print(f"Unknown command: {command}. Type /help.")
                continue

            session.add_user(line)
            logger.info("user: %s", line)
            try:
                response = provider.chat(session.build_request(model=current_model))
            except NOVAProviderError as exc:
                print(f"[nova error] {exc}")
                logger.warning("nova error: %s", exc)
                continue
            answer = response.message.content
            session.add_assistant(answer)
            print(f"N.O.V.A> {answer}")
            logger.info("assistant (model=%s): %s", response.model, answer)
    finally:
        provider.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())