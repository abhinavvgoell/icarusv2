"""Structured, correlation-aware application logging."""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone

request_id: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key): payload[key] = getattr(record, key)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("arbiter")
    if logger.handlers: return logger
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(os.getenv("APP_LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger
