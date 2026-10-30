"""Publishes a small JSON snapshot so the public dashboard shows real data."""

import ftplib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_FRESH_SECONDS = 900


def _metadata_json(store, key: str):
    """Read one JSON metadata row; a missing or broken row must not break export."""
    getter = getattr(store, "get_metadata", None)
    if getter is None:
        return None
    try:
        raw = getter(key)
    except Exception:  # noqa: BLE001 - export must survive a broken store
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _store_count(store, method: str) -> int:
    function = getattr(store, method, None)
    if function is None:
        return 0
    try:
        return int(function())
    except Exception:  # noqa: BLE001 - diagnostics are best-effort
        return 0


def build_site_data(
    store,
    *,
    status_path: str | Path = "data/runtime-status.json",
    limit: int = 50,
    now: float | None = None,
) -> dict:
    """Combine the runtime heartbeat and recent notifications into one payload."""
    moment = now if now is not None else datetime.now(timezone.utc).timestamp()
    status: dict = {}
    path = Path(status_path)
    if path.is_file():
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = {}
    heartbeat_at = status.get("heartbeat_at")
    healthy = bool(heartbeat_at) and moment - float(heartbeat_at) < HEARTBEAT_FRESH_SECONDS
    heartbeat_iso = (
        datetime.fromtimestamp(float(heartbeat_at), timezone.utc).isoformat()
        if heartbeat_at
        else None
    )
    return {
        "status": {
            "healthy": healthy,
            "live": True,
            "monitoring_enabled": bool(status.get("monitoring_enabled")),
            "source_failure_count": int(status.get("source_failure_count") or 0),
            "heartbeat_at": heartbeat_at,
            "heartbeat_iso": heartbeat_iso,
            "error": None if status else "Бот пока не передавал состояние",
            "host": socket.gethostname(),
            "diag": {
                "last_cycle": _metadata_json(store, "last_cycle"),
                "last_source_error": _metadata_json(store, "last_source_error"),
                "seen_total": _store_count(store, "count_seen"),
                "pending_notifications": _store_count(
                    store, "count_pending_notifications"
                ),
                "subscribers": _store_count(store, "count_subscribers"),
            },
        },
        "items": list(store.recent_notifications(limit)),
    }


def write_site_data(path: str | Path, payload: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def upload_site_data(
    path: str | Path,
    *,
    host: str,
    username: str,
    password: str,
    remote_dir: str = "/htdocs",
    timeout: int = 30,
) -> None:
    """Push the snapshot to a static host that only speaks FTP (InfinityFree)."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    connection = ftplib.FTP(timeout=timeout)
    try:
        connection.connect(host, 21)
        connection.login(username, password)
        if remote_dir:
            connection.cwd(remote_dir)
        with target.open("rb") as handle:
            connection.storbinary(f"STOR {target.name}", handle)
    finally:
        try:
            connection.quit()
        except ftplib.all_errors:
            connection.close()
