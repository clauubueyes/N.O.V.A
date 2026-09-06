from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from nova.core.config import LoggingSettings

_LOGGER_NAME = "nova"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(settings: LoggingSettings) -> None:
    root = logging.getLogger(_LOGGER_NAME)
    if root.handlers:
        return
    root.setLevel(settings.level.upper())
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if settings.file:
        path = Path(settings.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")