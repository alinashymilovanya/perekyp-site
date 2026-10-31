"""Structured, secret-safe event log shared by the bot, the publisher and the logs API.

One JSON object per line:

    {"timestamp": 1793445900.27, "timestamp_iso": "2026-10-31T12:05:00.270Z",
     "level": "INFO", "event": "github_publish_succeeded", "status": "success",
     "component": "publisher", "cycle_id": "c-20261031T120500-1a2b",
     "listing_id": "4123456789", "details": {"commit": "62c7131f"}}

``timestamp`` stays a float so the older readers keep working; ``timestamp_iso``
is the UTC ISO-8601 value the admin page and the API show.  Every value passes
through :func:`sanitize_text`, so tokens, passwords, cookies and other secrets
never reach the log file — and control characters cannot forge a log line.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

STATUS_VALUES = ("started", "success", "failed", "skipped", "queued", "retried")
LEVEL_VALUES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

REDACTED = "[redacted]"
MAX_TEXT_LENGTH = 400
MAX_DETAILS_CHARS = 4_000
MAX_LIST_ITEMS = 20
MAX_NESTING = 3

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029]")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")
_KEY_VALUE_RE = re.compile(
    r"(?i)([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api_hash|cookie"
    r"|authorization|credential|private_key)[A-Za-z0-9_.-]*\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_URL_CREDENTIALS_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api_hash|cookie|authorization"
    r"|credential|private_key|session|proxy)"
)

_logger = logging.getLogger("iphone_monitor")
_logger.addHandler(logging.NullHandler())
_configured = False


def configure_logging(
    log_path: str | Path | None = "data/bot.log",
    *,
    force: bool = False,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
    console: bool = True,
) -> None:
    """Send JSON events to stdout and to a rotating file the owner can read.

    ``console=False`` keeps the file handler only; tests use it so a temporary
    log file does not also echo records into the test runner output.
    """
    global _configured
    if _configured and not force:
        return
    if force:
        _reset_handlers()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    formatter = logging.Formatter("%(message)s")
    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        _logger.addHandler(stream_handler)
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _logger.addHandler(file_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    _configured = True


def _reset_handlers() -> None:
    """Drop every handler so tests can point the log at a new directory."""
    global _configured
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        try:
            handler.close()
        except (OSError, ValueError):
            pass
    # Keep the library quiet when nothing is configured.
    _logger.addHandler(logging.NullHandler())
    _configured = False


def iso_timestamp(moment: float | None = None) -> str:
    """Render a Unix timestamp as UTC ISO-8601 with a trailing ``Z``."""
    stamp = time.time() if moment is None else float(moment)
    return (
        datetime.fromtimestamp(stamp, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def is_sensitive_key(name: str) -> bool:
    """A detail key that must never carry a value into the log."""
    return bool(_SENSITIVE_KEY_RE.search(str(name)))


def sanitize_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Strip control characters and redact secrets from one log-safe string."""
    text = value if isinstance(value, str) else str(value)
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _CONTROL_RE.sub("", text)
    text = _URL_CREDENTIALS_RE.sub(r"\1[redacted]@", text)
    text = _TELEGRAM_TOKEN_RE.sub(REDACTED, text)
    text = _GITHUB_TOKEN_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub("bearer " + REDACTED, text)
    text = _KEY_VALUE_RE.sub(lambda match: match.group(1) + REDACTED, text)
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def sanitize_value(value: Any, depth: int = 0) -> Any:
    """Recursively make one detail value JSON-safe and secret-free."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        if depth >= MAX_NESTING:
            return REDACTED
        cleaned = {}
        for key, item in list(value.items())[:MAX_LIST_ITEMS]:
            name = sanitize_text(key, limit=64) or "field"
            cleaned[name] = REDACTED if is_sensitive_key(name) else sanitize_value(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        if depth >= MAX_NESTING:
            return REDACTED
        return [sanitize_value(item, depth + 1) for item in list(value)[:MAX_LIST_ITEMS]]
    return sanitize_text(value)


def sanitize_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Clean every detail and keep the serialized record small."""
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        name = sanitize_text(key, limit=64) or "field"
        cleaned[name] = REDACTED if is_sensitive_key(name) else sanitize_value(value)
    return _cap_details(cleaned)


def _cap_details(details: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(details, ensure_ascii=False, default=str)
    if len(encoded) <= MAX_DETAILS_CHARS:
        return details
    reduced: dict[str, Any] = {}
    used = 2
    for key, value in details.items():
        item = json.dumps({key: value}, ensure_ascii=False, default=str)
        if used + len(item) > MAX_DETAILS_CHARS:
            reduced["_truncated"] = True
            break
        reduced[key] = value
        used += len(item)
    return reduced


def describe_exception(error: BaseException) -> str:
    """`Type: message` without a traceback — a traceback leaks call arguments."""
    message = sanitize_text(str(error), limit=200)
    name = type(error).__name__
    return f"{name}: {message}" if message else name


def log_event(
    event: str,
    *,
    status: str = "success",
    component: str | None = None,
    cycle_id: str | None = None,
    listing_id: str | int | None = None,
    level: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Write one structured event and return the record that was written."""
    normalized_status = str(status or "success").strip().lower()
    if normalized_status not in STATUS_VALUES:
        normalized_status = "success"
    normalized_level = str(level or ("WARNING" if normalized_status == "failed" else "INFO")).upper()
    if normalized_level not in LEVEL_VALUES:
        normalized_level = "INFO"
    moment = time.time()
    record: dict[str, Any] = {
        "timestamp": moment,
        "timestamp_iso": iso_timestamp(moment),
        "level": normalized_level,
        "event": sanitize_text(event, limit=80) or "unknown_event",
        "status": normalized_status,
        "component": sanitize_text(component, limit=40) if component else "bot",
        "details": sanitize_details(details),
    }
    if cycle_id:
        record["cycle_id"] = sanitize_text(cycle_id, limit=64)
    if listing_id is not None and str(listing_id) != "":
        record["listing_id"] = sanitize_text(listing_id, limit=64)
    _logger.log(
        getattr(logging, normalized_level, logging.INFO),
        json.dumps(record, ensure_ascii=False),
    )
    return record
