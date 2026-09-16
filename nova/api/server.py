from __future__ import annotations

import sys

import uvicorn

from nova.api.app import create_app
from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging

logger = get_logger("api.server")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _require_safe_bind(settings) -> None:
    """Refuse to bind a non-loopback interface without an API token.

    `create_app` intentionally stays permissive (the onboarding flow uses the
    setup guard's per-route 403s), but the process that actually opens a socket
    to the network must never do so unauthenticated: that would expose /v1/*
    (sessions, tools, automation) to the whole LAN.
    """
    host = settings.api.host.strip().lower()
    if host not in _LOOPBACK_HOSTS and not settings.api.token:
        raise SystemExit(
            "Refusing to start: api.host is not loopback but no api.token is set. "
            "Bind to 127.0.0.1 or configure api.token to expose the API."
        )


def main() -> int:
    settings = load_settings()
    setup_logging(settings.logging)
    if settings.llm.provider == "ollama":
        from nova.setup.detect import ensure_ollama_running

        st = ensure_ollama_running()
        logger.info("ollama check: %s (started_now=%s)", st.message, st.started_now)
    _require_safe_bind(settings)
    app = create_app(settings)
    logger.info("listening on http://%s:%d", settings.api.host, settings.api.port)
    uvicorn.run(app, host=settings.api.host, port=settings.api.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())