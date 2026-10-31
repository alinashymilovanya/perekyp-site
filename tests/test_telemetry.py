import json
import logging
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor import telemetry
from iphone_monitor.telemetry import (
    REDACTED,
    STATUS_VALUES,
    configure_logging,
    describe_exception,
    is_sensitive_key,
    iso_timestamp,
    log_event,
    sanitize_details,
    sanitize_text,
)


class _Capture(logging.Handler):
    """Collects the JSON lines that log_event writes."""

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record):
        self.records.append(json.loads(record.getMessage()))


class TelemetryFormatTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("iphone_monitor")
        self.logger.setLevel(logging.INFO)
        self.capture = _Capture()
        self.logger.addHandler(self.capture)

    def tearDown(self):
        self.logger.removeHandler(self.capture)

    def _last(self) -> dict:
        self.assertTrue(self.capture.records, "log_event wrote nothing")
        return self.capture.records[-1]

    def test_record_has_every_required_field(self):
        log_event(
            "listing_notified",
            status="success",
            component="telegram",
            cycle_id="20260101T000000Z-abcdef",
            listing_id="8499243676",
            price_rub=58990,
        )
        record = self._last()
        for key in ("timestamp", "timestamp_iso", "level", "event", "status", "component", "details"):
            self.assertIn(key, record)
        self.assertEqual(record["event"], "listing_notified")
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["component"], "telegram")
        self.assertEqual(record["cycle_id"], "20260101T000000Z-abcdef")
        self.assertEqual(record["listing_id"], "8499243676")
        self.assertEqual(record["details"], {"price_rub": 58990})

    def test_legacy_timestamp_stays_a_number_and_matches_the_iso_field(self):
        before = datetime.now(timezone.utc).timestamp()
        log_event("bot_started")
        after = datetime.now(timezone.utc).timestamp()
        record = self._last()
        self.assertIsInstance(record["timestamp"], float)
        self.assertGreaterEqual(record["timestamp"], before)
        self.assertLessEqual(record["timestamp"], after + 0.01)
        self.assertTrue(record["timestamp_iso"].endswith("Z"))
        parsed = datetime.fromisoformat(record["timestamp_iso"].replace("Z", "+00:00"))
        self.assertAlmostEqual(parsed.timestamp(), record["timestamp"], places=1)

    def test_iso_timestamp_is_utc(self):
        self.assertEqual(iso_timestamp(0), "1970-01-01T00:00:00.000Z")
        self.assertTrue(iso_timestamp().endswith("Z"))

    def test_status_and_level_are_normalized(self):
        log_event("something", status="weird")
        record = self._last()
        self.assertIn(record["status"], STATUS_VALUES)
        self.assertEqual(record["status"], "success")
        log_event("failure", status="failed")
        self.assertEqual(self._last()["level"], "WARNING")

    def test_unknown_fields_become_details(self):
        log_event("cycle_finished", fetched=10, notified=2, best_margin_rub=None)
        record = self._last()
        self.assertEqual(
            record["details"], {"fetched": 10, "notified": 2, "best_margin_rub": None}
        )

    def test_empty_details_are_still_a_dict(self):
        log_event("bot_started")
        self.assertEqual(self._last()["details"], {})


class TelemetrySecretTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("iphone_monitor")
        self.logger.setLevel(logging.INFO)
        self.capture = _Capture()
        self.logger.addHandler(self.capture)

    def tearDown(self):
        self.logger.removeHandler(self.capture)

    def _last(self) -> dict:
        return self.capture.records[-1]

    def test_telegram_token_is_masked_inside_free_text(self):
        token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
        log_event("telegram_request_failed", error=f"HTTP 401 for bot {token}")
        text = json.dumps(self._last(), ensure_ascii=False)
        self.assertNotIn(token, text)
        self.assertIn(REDACTED, text)

    def test_github_tokens_are_masked(self):
        for token in ("ghp_" + "a" * 36, "github_pat_" + "b" * 40):
            log_event("github_publish_failed", error=f"bad token {token}")
            self.assertNotIn(token, json.dumps(self._last(), ensure_ascii=False))

    def test_bearer_and_key_value_secrets_are_masked(self):
        log_event(
            "http_error",
            error="Authorization: Bearer abcdef123456 password=hunter2 api_hash=deadbeef",
        )
        text = json.dumps(self._last(), ensure_ascii=False)
        self.assertNotIn("abcdef123456", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("deadbeef", text)

    def test_url_credentials_are_masked(self):
        log_event("ftp_failed", error="ftp://user:supersecret@ftp.example.com/htdocs")
        text = json.dumps(self._last(), ensure_ascii=False)
        self.assertNotIn("supersecret", text)
        self.assertIn("ftp.example.com", text)

    def test_sensitive_keys_are_redacted_whatever_the_value(self):
        log_event(
            "login_failed",
            password="hunter2",
            session_token="abc",
            cookie="sid=1",
            api_hash="0f0f",
            ordinary="kept",
        )
        details = self._last()["details"]
        for key in ("password", "session_token", "cookie", "api_hash"):
            self.assertEqual(details[key], REDACTED, key)
        self.assertEqual(details["ordinary"], "kept")
        self.assertTrue(is_sensitive_key("ADMIN_LOGS_PASSWORD"))
        self.assertTrue(is_sensitive_key("authorization"))
        self.assertFalse(is_sensitive_key("price_rub"))

    def test_nested_secrets_are_masked(self):
        log_event("weird", payload={"inner": {"token": "abc123", "ok": 1}})
        details = self._last()["details"]
        self.assertEqual(details["payload"]["inner"]["token"], REDACTED)
        self.assertEqual(details["payload"]["inner"]["ok"], 1)

    def test_newlines_cannot_forge_a_second_record(self):
        log_event("listing_skipped", reason="bad\n{\"event\": \"site_deploy_confirmed\"}")
        record = self._last()
        self.assertNotIn("\n", record["details"]["reason"])
        self.assertEqual(record["event"], "listing_skipped")

    def test_sanitize_text_strips_control_characters(self):
        cleaned = sanitize_text("a\x00b\rc\nd\te\x1b[31mred")
        for char in ("\x00", "\r", "\n", "\t", "\x1b"):
            self.assertNotIn(char, cleaned)

    def test_long_values_are_truncated(self):
        cleaned = sanitize_text("x" * 5_000)
        self.assertLessEqual(len(cleaned), 401)
        self.assertTrue(cleaned.endswith("…"))
        many = {f"k{index}": "y" * 400 for index in range(30)}
        details = sanitize_details(many)
        self.assertTrue(details.get("_truncated"))
        self.assertLess(len(json.dumps(details)), 4_100)

    def test_describe_exception_has_no_traceback_and_masks_secrets(self):
        try:
            raise RuntimeError("boom token=secretvalue")
        except RuntimeError as error:
            described = describe_exception(error)
        self.assertTrue(described.startswith("RuntimeError:"))
        self.assertNotIn("secretvalue", described)
        self.assertNotIn("Traceback", described)


class TelemetryHandlerTests(unittest.TestCase):
    def test_configure_logging_is_idempotent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.log"
            configure_logging(path, force=True, console=False)
            try:
                handlers = logging.getLogger("iphone_monitor").handlers
                first = len(handlers)
                configure_logging(path, console=False)
                self.assertEqual(len(logging.getLogger("iphone_monitor").handlers), first)
                # The file handler must write the same shape the reader expects.
                log_event("handler_probe", status="success")
                for handler in handlers:
                    handler.flush()
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                self.assertTrue(lines)
                record = json.loads(lines[-1])
                self.assertEqual(record["event"], "handler_probe")
                self.assertIn("timestamp_iso", record)
            finally:
                telemetry._reset_handlers()


if __name__ == "__main__":
    unittest.main()
