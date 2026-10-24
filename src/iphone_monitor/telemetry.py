"""Minimal structured logs for a single long-running process."""

import json
import logging
import time

_logger = logging.getLogger("iphone_monitor")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_event(event: str, **fields) -> None:
    _logger.info(json.dumps({"event": event, "timestamp": time.time(), **fields}))
