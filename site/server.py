"""Small read-only web server for the iPhone Monitor dashboard."""

import json
import mimetypes
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SITE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SITE_ROOT.parent
STATUS_PATH = PROJECT_ROOT / "data" / "runtime-status.json"
DATABASE_PATH = PROJECT_ROOT / "data" / "seen.sqlite3"
MAX_STATUS_AGE_SECONDS = 120


def read_dashboard_status(
    path: str | Path = STATUS_PATH,
    *,
    now: float | None = None,
    max_age_seconds: int = MAX_STATUS_AGE_SECONDS,
) -> dict:
    """Return safe, presentation-ready status data without changing bot files."""
    status_path = Path(path)
    current_time = time.time() if now is None else now
    if not status_path.is_file():
        return {
            "healthy": False,
            "monitoring_enabled": False,
            "source_failure_count": 0,
            "heartbeat_at": None,
            "heartbeat_iso": None,
            "error": "Файл статуса ещё не создан",
        }

    try:
        raw_status = json.loads(status_path.read_text(encoding="utf-8"))
        heartbeat_at = raw_status.get("heartbeat_at")
        heartbeat_at = float(heartbeat_at) if heartbeat_at is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "healthy": False,
            "monitoring_enabled": False,
            "source_failure_count": 0,
            "heartbeat_at": None,
            "heartbeat_iso": None,
            "error": "Файл статуса повреждён или недоступен",
        }

    healthy = heartbeat_at is not None and current_time - heartbeat_at <= max_age_seconds
    return {
        "healthy": healthy,
        "monitoring_enabled": bool(raw_status.get("monitoring_enabled")),
        "source_failure_count": int(raw_status.get("source_failure_count", 0)),
        "heartbeat_at": heartbeat_at,
        "heartbeat_iso": (
            datetime.fromtimestamp(heartbeat_at, tz=timezone.utc).isoformat()
            if heartbeat_at is not None
            else None
        ),
        "error": None if healthy else "Нет свежего сигнала от процесса бота",
    }


def list_notified_listings(path: str | Path = DATABASE_PATH, *, limit: int = 50) -> list[dict]:
    """Read notification history from SQLite in read-only mode."""
    database_path = Path(path)
    if not database_path.is_file():
        return []
    safe_limit = max(1, min(int(limit), 100))
    database_uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT item_id, notified_at, title, price_rub, market_price_rub, margin_rub, url "
            "FROM notified_listings ORDER BY notified_at DESC LIMIT ?",
            (safe_limit,),
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        cursor.close()
        connection.close()

    return [
        {
            "item_id": row[0],
            "notified_at": row[1],
            "title": row[2],
            "price_rub": row[3],
            "market_price_rub": row[4],
            "margin_rub": row[5],
            "url": row[6],
        }
        for row in rows
    ]


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the dashboard and a tiny read-only JSON API."""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        request = urlparse(self.path)
        if request.path == "/api/status":
            self._send_json(read_dashboard_status())
            return
        if request.path == "/api/listings":
            query = parse_qs(request.query)
            limit = query.get("limit", ["50"])[0]
            try:
                parsed_limit = int(limit)
            except ValueError:
                parsed_limit = 50
            self._send_json({"items": list_notified_listings(limit=parsed_limit)})
            return
        self._send_static(request.path)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, request_path: str) -> None:
        relative_path = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        file_path = (SITE_ROOT / relative_path).resolve()
        try:
            file_path.relative_to(SITE_ROOT)
        except ValueError:
            self.send_error(404)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
