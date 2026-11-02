"""Structured JSON logging. The observability layer (Layer 8) will add tracing on top of this."""

import json
import logging
import sys
from datetime import UTC, datetime

# Fields passed via `logger.info("msg", extra={...})` that should appear in the JSON line.
_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "finish_reason",
    "error_code",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Keep uvicorn's access log quiet; our middleware logs each request instead.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
