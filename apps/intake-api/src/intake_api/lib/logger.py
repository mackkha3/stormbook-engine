"""Structured JSON logger.

Writes one JSON event per line. INFO/DEBUG → stdout, WARN/ERROR → stderr.
Railway captures both streams; that's our v1 log sink.

TODO: switch to Logtail or Axiom past 2 pilots — pipe stderr through a
shipper or add a logging handler that POSTs batches.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "event": record.getMessage(),
        }
        # Anything passed via `extra={"key": ...}` lands on the record as an attribute.
        # Pull through any non-standard keys.
        reserved = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "taskName",
        }
        for key, value in record.__dict__.items():
            if key in reserved or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _StderrFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


class _StdoutFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


_configured = False


def configure() -> None:
    """Configure the root logger. Idempotent."""
    global _configured
    if _configured:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # Wipe any handlers attached by uvicorn / pytest so our format wins.
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = _JsonFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(_StdoutFilter())
    root.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(_StderrFilter())
    root.addHandler(stderr_handler)

    # Keep uvicorn's access log readable but routed through us.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    _configured = True


def get(name: str = "intake_api") -> logging.Logger:
    configure()
    return logging.getLogger(name)
