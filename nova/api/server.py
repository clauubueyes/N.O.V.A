from __future__ import annotations

import sys

import uvicorn

from nova.api.app import create_app
from nova.core.config import load_settings
from nova.core.logging import get_logger, setup_logging

logger = get_logger("api.server")


def main() -> int:
    settings = load_settings()
    setup_logging(settings.logging)
    if settings.llm.provider == "ollama":
        from nova.setup.detect import ensure_ollama_running

        st = ensure_ollama_running()
        logger.info("ollama check: %s (started_now=%s)", st.message, st.started_now)
    app = create_app(settings)
    logger.info("listening on http://%s:%d", settings.api.host, settings.api.port)
    uvicorn.run(app, host=settings.api.host, port=settings.api.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())