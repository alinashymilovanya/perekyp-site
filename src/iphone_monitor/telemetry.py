"""Minimal structured logs for a single long-running process."""

import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = logging.getLogger("iphone_monitor")
_configured = False


def configure_logging(log_path: str | Path | None = "data/bot.log") -> None:
    """Send JSON events to stdout and to a rotating file the owner can read."""
    global _configured
    if _configured:
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    formatter = logging.Formatter("%(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    _logger.addHandler(stream_handler)
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _logger.addHandler(file_handler)
    _logger.setLevel(logging.INFO)
    _configured = True


def log_event(event: str, **fields) -> None:
    _logger.info(json.dumps({"event": event, "timestamp": time.time(), **fields}, ensure_ascii=False))
