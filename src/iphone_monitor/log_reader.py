"""Read the bot log for the admin API: tail limits, rotation and corrupt lines.

The API never serves the whole file and never trusts a path from the request:
the reader always works with the configured ``LOG_PATH`` plus its rotated
siblings (``bot.log.1`` … ``bot.log.N``), reads only the tail of each file and
returns at most ``max_entries`` records.

Every line is re-sanitized on the way out, so even a legacy line written by an
older version (before the secret masking existed) cannot leak a token to the
admin page.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .telemetry import iso_timestamp, sanitize_details, sanitize_text

DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_ROTATED_FILES = 3
DEFAULT_MAX_ENTRIES = 2_000
MAX_RAW_LINE = 240
CORRUPT_EVENT = "log_line_unparsed"

_KNOWN_FIELDS = frozenset(
    {
        "timestamp",
        "timestamp_iso",
        "level",
        "event",
        "status",
        "component",
        "cycle_id",
        "listing_id",
        "details",
    }
)


def _entry_id(*parts: Any) -> str:
    digest = hashlib.sha1(
        "|".join(str(part) for part in parts).encode("utf-8", "replace")
    ).hexdigest()
    return digest[:12]


def _parse_timestamp(payload: Mapping[str, Any]) -> float | None:
    raw = payload.get("timestamp")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            pass
    iso = payload.get("timestamp_iso")
    if isinstance(iso, str) and iso:
        parsed = parse_moment(iso)
        if parsed is not None:
            return parsed
    return None


def parse_moment(value: str | float | int | None) -> float | None:
    """Accept an epoch number or an ISO-8601 string and return epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)  # NaN guard
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _corrupt_entry(text: str, *, source: str, position: int) -> dict[str, Any]:
    return {
        "id": _entry_id(source, position, text[:200]),
        "timestamp": None,
        "timestamp_iso": "",
        "level": "WARNING",
        "event": CORRUPT_EVENT,
        "status": "failed",
        "component": "logs_api",
        "details": {
            "source": sanitize_text(source, limit=80),
            "raw": sanitize_text(text, limit=MAX_RAW_LINE),
        },
    }


def parse_line(line: str, *, source: str = "", position: int = 0) -> dict[str, Any] | None:
    """Turn one log line into an API entry; a broken line becomes a marker."""
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return _corrupt_entry(text, source=source, position=position)

    event = payload.get("event")
    if not isinstance(event, str) or not event.strip():
        event = "unknown_event"
    timestamp = _parse_timestamp(payload)
    details = payload.get("details")
    if isinstance(details, Mapping):
        merged = dict(details)
        for key, value in payload.items():
            if key not in _KNOWN_FIELDS:
                merged.setdefault(key, value)
    else:
        merged = {key: value for key, value in payload.items() if key not in _KNOWN_FIELDS}

    iso = payload.get("timestamp_iso")
    raw_status = payload.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        status = raw_status.strip().lower()[:16]
    else:
        # Records written before the structured format existed have no status.
        # Deriving it from the event name keeps the feed readable instead of
        # showing "unknown" for every historical line.
        status = _infer_status(event)
        merged.setdefault("legacy_record", True)
    entry: dict[str, Any] = {
        "id": _entry_id(timestamp, event, json.dumps(merged, sort_keys=True, default=str)),
        "timestamp": timestamp,
        "timestamp_iso": iso if isinstance(iso, str) and iso else (iso_timestamp(timestamp) if timestamp else ""),
        "level": str(payload.get("level") or "INFO").upper()[:16],
        "event": sanitize_text(event, limit=80),
        "status": status,
        "component": sanitize_text(payload.get("component") or "bot", limit=40),
        "details": sanitize_details(merged),
    }
    cycle_id = payload.get("cycle_id")
    if cycle_id:
        entry["cycle_id"] = sanitize_text(cycle_id, limit=64)
    listing_id = payload.get("listing_id")
    if listing_id:
        entry["listing_id"] = sanitize_text(listing_id, limit=64)
    return entry


def _infer_status(event: str) -> str:
    """Best-effort status for records that predate the structured format."""
    name = event.lower()
    if name.endswith(("_failed", "_error", "_rejected", "_blocked")):
        return "failed"
    if name.endswith("_started"):
        return "started"
    if "skipped" in name or "disabled" in name or "missing" in name:
        return "skipped"
    if "delivered" in name:
        return "success"
    if "queued" in name or "pending" in name:
        return "queued"
    if "retried" in name:
        return "retried"
    return "success"


def _log_paths(log_path: str | Path, rotated_files: int) -> list[Path]:
    base = Path(log_path)
    paths = [base]
    for index in range(1, max(0, int(rotated_files)) + 1):
        paths.append(base.with_name(f"{base.name}.{index}"))
    return paths


def _tail_bytes(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if max_bytes and size > max_bytes:
            handle.seek(size - max_bytes)
            data = handle.read()
            newline = data.find(b"\n")
            data = data[newline + 1 :] if newline >= 0 else b""
            return data, True
        return handle.read(), False


def read_log_entries(
    log_path: str | Path,
    *,
    rotated_files: int = DEFAULT_ROTATED_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    """Read the newest entries across the log and its rotated backups."""
    entries: list[dict[str, Any]] = []
    files: list[str] = []
    bytes_read = 0
    truncated = False
    for rank, path in enumerate(_log_paths(log_path, rotated_files)):
        try:
            if not path.is_file():
                continue
            data, tail_cut = _tail_bytes(path, max_bytes)
        except OSError:
            continue
        files.append(path.name)
        bytes_read += len(data)
        truncated = truncated or tail_cut
        for position, line in enumerate(data.decode("utf-8", "replace").split("\n")):
            entry = parse_line(line, source=path.name, position=position)
            if entry is None:
                continue
            entry["_rank"] = rank
            entry["_position"] = position
            entries.append(entry)
    entries.sort(key=lambda item: _sort_key(item), reverse=True)
    if max_entries and len(entries) > max_entries:
        entries = entries[:max_entries]
        truncated = True
    for entry in entries:
        entry.pop("_rank", None)
        entry.pop("_position", None)
    return {
        "entries": entries,
        "files": files,
        "bytes_read": bytes_read,
        "truncated": truncated,
    }


def _sort_key(entry: Mapping[str, Any]) -> tuple[int, float, int, int]:
    timestamp = entry.get("timestamp")
    # Entries without a timestamp (a corrupt line) go to the bottom of the feed.
    return (
        0 if timestamp is None else 1,
        float(timestamp) if timestamp is not None else 0.0,
        -int(entry.get("_rank", 0)),
        int(entry.get("_position", 0)),
    )


@dataclass(frozen=True)
class LogFilters:
    """Everything the admin page may narrow the feed by."""

    status: tuple[str, ...] = ()
    event: tuple[str, ...] = ()
    level: tuple[str, ...] = ()
    component: tuple[str, ...] = ()
    cycle_id: str = ""
    listing_id: str = ""
    since: float | None = None
    until: float | None = None
    search: str = ""


def parse_values(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """Accept ``a&b`` style repeats and ``a,b`` style lists."""
    if value is None:
        return ()
    items: list[str] = []
    source = [value] if isinstance(value, str) else list(value)
    for item in source:
        items.extend(part.strip() for part in str(item).split(",") if part.strip())
    return tuple(items)


def matches(entry: Mapping[str, Any], filters: LogFilters) -> bool:
    if filters.status and entry.get("status") not in filters.status:
        return False
    if filters.event:
        event = str(entry.get("event") or "")
        if not any(event == wanted or event.startswith(wanted + "_") for wanted in filters.event):
            return False
    if filters.level and str(entry.get("level") or "").upper() not in {
        level.upper() for level in filters.level
    }:
        return False
    if filters.component and entry.get("component") not in filters.component:
        return False
    if filters.cycle_id and entry.get("cycle_id") != filters.cycle_id:
        return False
    if filters.listing_id and str(entry.get("listing_id") or "") != filters.listing_id:
        return False
    timestamp = entry.get("timestamp")
    if filters.since is not None and (timestamp is None or float(timestamp) < filters.since):
        return False
    if filters.until is not None and (timestamp is None or float(timestamp) > filters.until):
        return False
    if filters.search:
        needle = filters.search.lower()
        haystack = " ".join(
            [
                str(entry.get("event") or ""),
                str(entry.get("status") or ""),
                str(entry.get("component") or ""),
                str(entry.get("listing_id") or ""),
                str(entry.get("cycle_id") or ""),
                json.dumps(entry.get("details") or {}, ensure_ascii=False, default=str),
            ]
        ).lower()
        if needle not in haystack:
            return False
    return True


def select_entries(
    entries: Iterable[Mapping[str, Any]],
    filters: LogFilters | None = None,
    *,
    before: float | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter, page (newest first) and cap the feed in one place."""
    active = filters or LogFilters()
    safe_limit = max(1, int(limit))
    selected: list[dict[str, Any]] = []
    for entry in entries:
        if not matches(entry, active):
            continue
        if before is not None:
            timestamp = entry.get("timestamp")
            if timestamp is None or float(timestamp) >= before:
                continue
        selected.append(dict(entry))
        if len(selected) >= safe_limit:
            break
    return selected


def build_facets(entries: Iterable[Mapping[str, Any]], *, limit: int = 40) -> dict[str, list[str]]:
    """Distinct values that already exist in the window, for the filter selects."""
    events: set[str] = set()
    statuses: set[str] = set()
    components: set[str] = set()
    levels: set[str] = set()
    for entry in entries:
        events.add(str(entry.get("event") or ""))
        statuses.add(str(entry.get("status") or ""))
        components.add(str(entry.get("component") or ""))
        levels.add(str(entry.get("level") or ""))
    return {
        "events": sorted(event for event in events if event)[:limit],
        "statuses": sorted(status for status in statuses if status),
        "components": sorted(component for component in components if component)[:limit],
        "levels": sorted(level for level in levels if level),
    }
