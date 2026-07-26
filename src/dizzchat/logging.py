"""Structured JSON logging for the application.

A single stdout handler emits one JSON object per log record, so container logs are
machine-parseable. Any ``extra=`` fields passed to a logger are merged into the JSON payload,
and the current ``connection_id`` (set per WebSocket connection, see ``connection_id_var``) is
attached automatically so every log line within a connection's task shares a correlation id.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Correlation id for the current task. The WebSocket handler sets this per connection so all log
# records emitted while serving that socket carry the same id, without threading it through calls.
connection_id_var: ContextVar[str | None] = ContextVar("connection_id", default=None)

# Standard LogRecord attributes; anything else on a record is treated as an extra field.
_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        connection_id = connection_id_var.get()
        if connection_id is not None:
            payload.setdefault("connection_id", connection_id)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger, replacing any existing handlers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
