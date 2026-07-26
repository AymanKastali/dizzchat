"""The JSON log formatter attaches the current connection id as a correlation field."""

import json
import logging

from dizzchat.logging import JsonFormatter, connection_id_var


def _formatted(record: logging.LogRecord) -> dict[str, object]:
    payload: dict[str, object] = json.loads(JsonFormatter().format(record))
    return payload


def test_connection_id_is_attached_when_set() -> None:
    record = logging.makeLogRecord({"msg": "serving socket"})
    token = connection_id_var.set("abc123")
    try:
        payload = _formatted(record)
    finally:
        connection_id_var.reset(token)

    assert payload["connection_id"] == "abc123"


def test_connection_id_is_absent_when_unset() -> None:
    payload = _formatted(logging.makeLogRecord({"msg": "no connection context"}))

    assert "connection_id" not in payload
