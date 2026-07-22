"""Structured application logging configuration."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

APPLICATION_LOGGER: Final = "jwdm"
_STRUCTURED_FIELDS: Final = (
    "operation_id",
    "candidate_id",
    "state",
    "source",
    "destination",
    "category",
    "outcome",
    "count",
)


class JsonFormatter(logging.Formatter):
    """Render one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        for field in _STRUCTURED_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def default_log_directory() -> Path:
    """Return JWDM's per-user log directory without creating it."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "JWDM" / "logs"
    return Path(tempfile.gettempdir()) / "JWDM" / "logs"


def configure_logging(log_directory: Path | None = None) -> Path:
    """Configure the application logger and return the active log file path.

    Calling this function repeatedly replaces only JWDM-owned handlers, which keeps
    test runs and embedded launches deterministic without changing root logging.
    """

    directory = log_directory if log_directory is not None else default_log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "jwdm.log.jsonl"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger(APPLICATION_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing_handler in list(logger.handlers):
        existing_handler.close()
        logger.removeHandler(existing_handler)
    logger.addHandler(handler)

    return log_path
