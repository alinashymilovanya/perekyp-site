"""Tiny file-based heartbeat used by the container health check."""

import json
from pathlib import Path


def write_status(
    path: str | Path,
    *,
    heartbeat_at: float,
    source_failure_count: int,
    monitoring_enabled: bool,
) -> None:
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "heartbeat_at": heartbeat_at,
        "source_failure_count": source_failure_count,
        "monitoring_enabled": monitoring_enabled,
    }
    temporary_path = status_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload), encoding="utf-8")
    temporary_path.replace(status_path)


def read_status(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_healthy(status: dict, *, now: float, max_age_seconds: int) -> bool:
    heartbeat_at = status.get("heartbeat_at")
    return isinstance(heartbeat_at, (int, float)) and now - heartbeat_at <= max_age_seconds
