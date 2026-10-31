"""End-to-end tests for the protected log API (real HTTP, no mocks)."""

import http.client
import json
import logging
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.logs_api import (
    LOGIN_PATH,
    LOGS_PATH,
    LOGOUT_PATH,
    HEALTH_PATH,
    LogsApi,
    LogsApiConfig,
    create_server,
)

logging.getLogger("iphone_monitor").addHandler(logging.NullHandler())

PASSWORD = "correct horse battery staple"
SECRET = "0123456789abcdef0123456789abcdef"


def _config(log_path: Path, **overrides) -> LogsApiConfig:
    base = dict(
        password=PASSWORD,
        session_secret=SECRET,
        enabled=True,
        log_path=str(log_path),
        session_ttl_seconds=3_600,
        max_login_attempts=5,
        login_window_seconds=300,
        lockout_seconds=900,
        cookie_mode=False,
    )
    base.update(overrides)
    return LogsApiConfig(**base)


def _line(index: int, **extra) -> str:
    record = {
        "timestamp": 1_700_000_000.0 + index,
        "timestamp_iso": f"2023-11-14T22:13:{index % 60:02d}.000Z",
        "level": "INFO",
        "event": f"event_{index}",
        "status": "success",
        "component": "monitor",
        "details": {"index": index},
    }
    record.update(extra)
    return json.dumps(record, ensure_ascii=False)


class _ServerMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.log_path = self.dir / "bot.log"
        self.log_path.write_text(
            "\n".join(_line(index) for index in range(6)) + "\n", encoding="utf-8"
        )
        self._servers = []

    def tearDown(self):
        for server, thread in self._servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self._tmp.cleanup()

    def serve(self, config: LogsApiConfig, *, app: LogsApi | None = None):
        server = create_server(config, port=0, host="127.0.0.1", app=app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        return server.server_address[1]

    def request(self, port: int, method: str, path: str, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        payload = None
        merged = dict(headers or {})
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=payload, headers=merged)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        result = {
            "status": response.status,
            "headers": {name.lower(): value for name, value in response.getheaders()},
            "raw": raw,
        }
        connection.close()
        try:
            result["json"] = json.loads(raw) if raw else {}
        except ValueError:
            result["json"] = {}
        return result

    def login(self, port: int, password: str = PASSWORD):
        return self.request(port, "POST", LOGIN_PATH, body={"password": password})

    def feed(self, port: int, token: str, query: str = ""):
        path = LOGS_PATH + (("?" + query) if query else "")
        return self.request(port, "GET", path, headers={"Authorization": f"Bearer {token}"})


class FailClosedTests(_ServerMixin):
    def test_missing_secrets_close_everything(self):
        port = self.serve(_config(self.log_path, password="", session_secret="short"))
        for method, path in (("GET", LOGS_PATH), ("POST", LOGIN_PATH), ("POST", LOGOUT_PATH)):
            response = self.request(port, method, path, body={} if method == "POST" else None)
            self.assertEqual(response["status"], 503, path)
            self.assertEqual(response["json"]["error"], "not_configured")
        health = self.request(port, "GET", HEALTH_PATH)
        self.assertEqual(health["status"], 200)
        self.assertFalse(health["json"]["configured"])

    def test_health_reports_configuration_without_secrets(self):
        port = self.serve(_config(self.log_path))
        health = self.request(port, "GET", HEALTH_PATH)
        self.assertEqual(health["status"], 200)
        self.assertTrue(health["json"]["configured"])
        self.assertTrue(health["json"]["auth_required"])
        self.assertNotIn(PASSWORD, health["raw"])
        self.assertNotIn(SECRET, health["raw"])


class AuthenticationTests(_ServerMixin):
    def test_logs_are_not_served_without_a_session(self):
        port = self.serve(_config(self.log_path))
        for headers in ({}, {"Authorization": "Bearer forged.token"}, {"Authorization": "Basic abc"}):
            response = self.request(port, "GET", LOGS_PATH, headers=headers)
            self.assertEqual(response["status"], 401)
            self.assertNotIn("event_0", response["raw"])
            self.assertNotIn("entries", response["json"])
            self.assertIn("www-authenticate", response["headers"])

    def test_wrong_password_is_rejected(self):
        port = self.serve(_config(self.log_path))
        response = self.login(port, "wrong")
        self.assertEqual(response["status"], 401)
        self.assertEqual(response["json"]["error"], "invalid_credentials")
        self.assertNotIn(PASSWORD, response["raw"])

    def test_empty_password_is_rejected(self):
        port = self.serve(_config(self.log_path))
        response = self.request(port, "POST", LOGIN_PATH, body={})
        self.assertEqual(response["status"], 401)

    def test_successful_login_returns_a_working_token(self):
        port = self.serve(_config(self.log_path))
        response = self.login(port)
        self.assertEqual(response["status"], 200)
        token = response["json"]["token"]
        self.assertTrue(token)
        self.assertNotIn(PASSWORD, response["raw"])
        self.assertIn("expires_at", response["json"])
        feed = self.feed(port, token)
        self.assertEqual(feed["status"], 200)
        self.assertEqual(feed["json"]["count"], 6)
        self.assertEqual(feed["json"]["entries"][0]["event"], "event_5")
        self.assertEqual(feed["json"]["entries"][-1]["event"], "event_0")

    def test_no_cookie_is_set_without_cookie_mode(self):
        port = self.serve(_config(self.log_path))
        response = self.login(port)
        self.assertNotIn("set-cookie", response["headers"])

    def test_cookie_mode_sets_hardened_cookie(self):
        port = self.serve(_config(self.log_path, cookie_mode=True))
        response = self.login(port)
        cookie = response["headers"].get("set-cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/api/admin/logs", cookie)

    def test_logout_revokes_the_token(self):
        port = self.serve(_config(self.log_path))
        token = self.login(port)["json"]["token"]
        self.assertEqual(self.feed(port, token)["status"], 200)
        logout = self.request(
            port, "POST", LOGOUT_PATH, headers={"Authorization": f"Bearer {token}"}, body={}
        )
        self.assertEqual(logout["status"], 200)
        self.assertEqual(self.feed(port, token)["status"], 401)

    def test_session_expires(self):
        now = [1_700_000_000.0]
        app = LogsApi(_config(self.log_path, session_ttl_seconds=60), clock=lambda: now[0])
        port = self.serve(app.config, app=app)
        token = self.login(port)["json"]["token"]
        self.assertEqual(self.feed(port, token)["status"], 200)
        now[0] += 61
        self.assertEqual(self.feed(port, token)["status"], 401)


class RateLimitTests(_ServerMixin):
    def test_repeated_failures_lock_the_client_out(self):
        port = self.serve(_config(self.log_path, max_login_attempts=3, lockout_seconds=600))
        for _ in range(3):
            response = self.login(port, "wrong")
            self.assertIn(response["status"], (401, 429))
        blocked = self.login(port, "wrong")
        self.assertEqual(blocked["status"], 429)
        self.assertEqual(blocked["json"]["error"], "too_many_attempts")
        self.assertIn("retry-after", blocked["headers"])
        self.assertGreater(blocked["json"]["retry_in"], 0)
        # Even the right password is refused while the lockout lasts.
        self.assertEqual(self.login(port)["status"], 429)

    def test_successful_login_clears_the_failure_counter(self):
        port = self.serve(_config(self.log_path, max_login_attempts=3))
        self.assertEqual(self.login(port, "wrong")["status"], 401)
        self.assertEqual(self.login(port)["status"], 200)
        self.assertEqual(self.login(port, "wrong")["status"], 401)
        self.assertEqual(self.login(port)["status"], 200)


class FeedTests(_ServerMixin):
    def _token(self, port: int) -> str:
        return self.login(port)["json"]["token"]

    def test_arbitrary_paths_are_ignored(self):
        secret = self.dir / "secret.txt"
        secret.write_text("TOP-SECRET-CONTENT", encoding="utf-8")
        port = self.serve(_config(self.log_path))
        token = self._token(port)
        for attempt in (str(secret), "../../secret.txt", str(self.log_path)):
            response = self.feed(port, token, query=f"path={attempt}")
            self.assertEqual(response["status"], 200)
            self.assertNotIn("TOP-SECRET-CONTENT", response["raw"])
            self.assertNotIn("secret.txt", json.dumps(response["json"]["window"]))

    def test_limit_is_capped_by_the_server(self):
        port = self.serve(_config(self.log_path, max_limit=2))
        token = self._token(port)
        response = self.feed(port, token, query="limit=500")
        self.assertEqual(response["json"]["limit"], 2)
        self.assertLessEqual(len(response["json"]["entries"]), 2)
        self.assertTrue(response["json"]["has_more"])

    def test_default_limit_applies(self):
        port = self.serve(_config(self.log_path, default_limit=3))
        token = self._token(port)
        response = self.feed(port, token)
        self.assertEqual(len(response["json"]["entries"]), 3)
        self.assertTrue(response["json"]["has_more"])

    def test_pagination_cursor_returns_new_records_only(self):
        port = self.serve(_config(self.log_path, max_limit=10))
        token = self._token(port)
        first = self.feed(port, token, query="limit=2")
        self.assertEqual([entry["event"] for entry in first["json"]["entries"]], ["event_5", "event_4"])
        cursor = first["json"]["next_before"]
        self.assertIsNotNone(cursor)
        second = self.feed(port, token, query=f"limit=2&before={cursor}")
        self.assertEqual([entry["event"] for entry in second["json"]["entries"]], ["event_3", "event_2"])

    def test_status_and_event_filters(self):
        self.log_path.write_text(
            "\n".join(
                [
                    _line(1, event="listing_skipped", status="skipped"),
                    _line(2, event="listing_notified", status="success"),
                    _line(3, event="telegram_delivery_failed", status="failed"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        port = self.serve(_config(self.log_path))
        token = self._token(port)
        failed = self.feed(port, token, query="status=failed")
        self.assertEqual([entry["event"] for entry in failed["json"]["entries"]], ["telegram_delivery_failed"])
        prefix = self.feed(port, token, query="event=listing")
        self.assertEqual(len(prefix["json"]["entries"]), 2)
        self.assertEqual(failed["json"]["facets"]["statuses"], ["failed", "skipped", "success"])

    def test_time_range_and_search_filters(self):
        port = self.serve(_config(self.log_path))
        token = self._token(port)
        window = self.feed(port, token, query="since=1700000002&until=1700000004")
        self.assertEqual(
            [entry["event"] for entry in window["json"]["entries"]],
            ["event_4", "event_3", "event_2"],
        )
        found = self.feed(port, token, query="q=event_3")
        self.assertEqual([entry["event"] for entry in found["json"]["entries"]], ["event_3"])

    def test_secrets_are_masked_in_the_response(self):
        token_value = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
        self.log_path.write_text(
            _line(1, event="telegram_failed", details={"error": token_value}) + "\n",
            encoding="utf-8",
        )
        port = self.serve(_config(self.log_path))
        token = self._token(port)
        response = self.feed(port, token)
        self.assertNotIn(token_value, response["raw"])
        self.assertIn("[redacted]", response["raw"])

    def test_corrupt_line_is_reported_not_fatal(self):
        self.log_path.write_text(_line(1) + "\n{broken\n", encoding="utf-8")
        port = self.serve(_config(self.log_path))
        token = self._token(port)
        response = self.feed(port, token)
        self.assertEqual(response["status"], 200)
        events = [entry["event"] for entry in response["json"]["entries"]]
        self.assertIn("log_line_unparsed", events)


class HeaderTests(_ServerMixin):
    def test_responses_are_uncacheable_and_not_indexable(self):
        port = self.serve(_config(self.log_path))
        login = self.login(port)
        token = login["json"]["token"]
        for response in (login, self.feed(port, token)):
            self.assertIn("no-store", response["headers"]["cache-control"])
            self.assertIn("noindex", response["headers"]["x-robots-tag"])
            self.assertEqual(response["headers"]["x-frame-options"], "DENY")
            self.assertEqual(response["headers"]["referrer-policy"], "no-referrer")
            self.assertEqual(response["headers"]["vary"], "Origin")
            self.assertNotIn("access-control-allow-origin", response["headers"])

    def test_cors_echoes_only_the_configured_origin(self):
        port = self.serve(_config(self.log_path, cors_origin="https://perekyp.example"))
        allowed = self.request(
            port, "GET", HEALTH_PATH, headers={"Origin": "https://perekyp.example"}
        )
        self.assertEqual(
            allowed["headers"]["access-control-allow-origin"], "https://perekyp.example"
        )
        other = self.request(port, "GET", HEALTH_PATH, headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", other["headers"])
        preflight = self.request(
            port, "OPTIONS", LOGIN_PATH, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(preflight["status"], 403)
        preflight_ok = self.request(
            port, "OPTIONS", LOGIN_PATH, headers={"Origin": "https://perekyp.example"}
        )
        self.assertEqual(preflight_ok["status"], 204)

    def test_unknown_route_is_404(self):
        port = self.serve(_config(self.log_path))
        self.assertEqual(self.request(port, "GET", "/api/admin/other")["status"], 404)
        self.assertEqual(self.request(port, "GET", "/")["status"], 404)


if __name__ == "__main__":
    unittest.main()
