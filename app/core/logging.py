"""Structured logging configuration.

Call `setup_logging()` once at app startup; everywhere else, just use
`logger = logging.getLogger(__name__)`.
"""
import logging
import sys
from typing import Any

from app.core.config import settings


def setup_logging() -> None:
    """Configure root logger with a sensible formatter."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Tame noisy third-party loggers
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience helper."""
    return logging.getLogger(name)


class LoggerAdapter(logging.LoggerAdapter):
    """Adapter that prefixes every message with a context dict (request_id, etc.)."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if self.extra:
            prefix = " ".join(f"{k}={v}" for k, v in self.extra.items())
            msg = f"[{prefix}] {msg}"
        return msg, kwargs
