import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.log_reader import (
    CORRUPT_EVENT,
    LogFilters,
    build_facets,
    matches,
    parse_line,
    parse_moment,
    parse_values,
    read_log_entries,
    select_entries,
)
from iphone_monitor.telemetry import log_event


def _record(**overrides):
    base = {
        "timestamp": 1_700_000_000.0,
        "timestamp_iso": "2023-11-14T22:13:20.000Z",
        "level": "INFO",
        "event": "listing_notified",
        "status": "success",
        "component": "telegram",
        "details": {"price_rub": 58990},
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


class ParseLineTests(unittest.TestCase):
    def test_structured_record_is_read_field_by_field(self):
        entry = parse_line(_record(cycle_id="c1", listing_id="42"))
        self.assertEqual(entry["event"], "listing_notified")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["component"], "telegram")
        self.assertEqual(entry["cycle_id"], "c1")
        self.assertEqual(entry["listing_id"], "42")
        self.assertEqual(entry["details"], {"price_rub": 58990})
        self.assertAlmostEqual(entry["timestamp"], 1_700_000_000.0)
        self.assertEqual(entry["timestamp_iso"], "2023-11-14T22:13:20.000Z")

    def test_legacy_flat_record_keeps_its_fields_as_details(self):
        legacy = json.dumps(
            {
                "event": "github_publish_succeeded",
                "timestamp": 1_793_445_900.27,
                "repo": "owner/repo",
                "items": 43,
            }
        )
        entry = parse_line(legacy)
        self.assertEqual(entry["event"], "github_publish_succeeded")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["details"]["repo"], "owner/repo")
        self.assertEqual(entry["details"]["items"], 43)
        self.assertTrue(entry["details"]["legacy_record"])
        self.assertTrue(entry["timestamp_iso"].endswith("Z"))

    def test_legacy_failure_event_gets_a_failed_status(self):
        entry = parse_line(json.dumps({"event": "telegram_request_failed", "timestamp": 1}))
        self.assertEqual(entry["status"], "failed")
        entry = parse_line(json.dumps({"event": "pending_notifications_delivered", "timestamp": 1}))
        self.assertEqual(entry["status"], "success")

    def test_broken_line_becomes_a_marker_without_crashing(self):
        entry = parse_line('{"event": "bot_started", "timestamp": 1')
        self.assertEqual(entry["event"], CORRUPT_EVENT)
        self.assertEqual(entry["status"], "failed")
        self.assertFalse(entry["timestamp_iso"])
        self.assertIn("bot_started", entry["details"]["raw"])

    def test_non_json_garbage_is_marked(self):
        entry = parse_line("Traceback (most recent call last): secret=value")
        self.assertEqual(entry["event"], CORRUPT_EVENT)
        self.assertNotIn("secret=value", json.dumps(entry["details"]))

    def test_blank_line_is_skipped(self):
        self.assertIsNone(parse_line("   "))
        self.assertIsNone(parse_line(""))

    def test_secrets_in_a_legacy_line_are_masked_on_read(self):
        token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
        entry = parse_line(json.dumps({"event": "telegram_failed", "timestamp": 1, "error": token}))
        self.assertNotIn(token, json.dumps(entry, ensure_ascii=False))


class ParseMomentTests(unittest.TestCase):
    def test_epoch_and_iso_are_accepted(self):
        self.assertEqual(parse_moment(1_700_000_000), 1_700_000_000.0)
        self.assertEqual(parse_moment("1700000000"), 1_700_000_000.0)
        self.assertAlmostEqual(
            parse_moment("2023-11-14T22:13:20Z"), 1_700_000_000.0, places=0
        )

    def test_naive_iso_is_treated_as_utc(self):
        self.assertAlmostEqual(
            parse_moment("2023-11-14T22:13:20"), 1_700_000_000.0, places=0
        )

    def test_garbage_is_rejected(self):
        for value in ("", None, "not-a-date", True, "2023-13-45T99:99:99Z"):
            self.assertIsNone(parse_moment(value), value)

    def test_parse_values_splits_commas_and_repeats(self):
        self.assertEqual(parse_values("failed,skipped"), ("failed", "skipped"))
        self.assertEqual(parse_values(["a", "b,c"]), ("a", "b", "c"))
        self.assertEqual(parse_values(None), ())
        self.assertEqual(parse_values(" , "), ())


class ReadLogEntriesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "bot.log"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: Path, lines) -> None:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_reads_every_rotation_newest_first(self):
        self._write(
            self.path.with_name("bot.log.1"),
            [json.dumps({"event": "old", "timestamp": 100.0})],
        )
        self._write(
            self.path,
            [
                json.dumps({"event": "middle", "timestamp": 200.0}),
                json.dumps({"event": "newest", "timestamp": 300.0}),
            ],
        )
        result = read_log_entries(self.path)
        self.assertEqual([entry["event"] for entry in result["entries"]], ["newest", "middle", "old"])
        self.assertEqual(result["files"], ["bot.log", "bot.log.1"])
        self.assertFalse(result["truncated"])

    def test_corrupt_lines_do_not_stop_the_read(self):
        self._write(
            self.path,
            [
                json.dumps({"event": "ok", "timestamp": 2.0}),
                "{broken",
                "",
                json.dumps({"event": "ok2", "timestamp": 1.0}),
            ],
        )
        result = read_log_entries(self.path)
        events = [entry["event"] for entry in result["entries"]]
        # The broken line has no timestamp, so it sorts after the readable ones.
        self.assertEqual(events, ["ok", "ok2", CORRUPT_EVENT])

    def test_size_cap_reads_only_the_tail_and_flags_truncation(self):
        lines = [json.dumps({"event": f"event-{index}", "timestamp": float(index)}) for index in range(50)]
        self._write(self.path, lines)
        result = read_log_entries(self.path, max_bytes=400)
        self.assertTrue(result["truncated"])
        self.assertLess(len(result["entries"]), len(lines))
        # The newest line survives, the oldest ones were cut away.
        self.assertEqual(result["entries"][0]["event"], "event-49")

    def test_entry_cap_is_respected(self):
        self._write(
            self.path,
            [json.dumps({"event": f"event-{index}", "timestamp": float(index)}) for index in range(10)],
        )
        result = read_log_entries(self.path, max_entries=3)
        self.assertEqual(len(result["entries"]), 3)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["entries"][0]["event"], "event-9")

    def test_missing_file_returns_an_empty_window(self):
        result = read_log_entries(self.dir / "absent.log")
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["files"], [])
        self.assertEqual(result["bytes_read"], 0)

    def test_records_written_by_telemetry_round_trip(self):
        from iphone_monitor import telemetry

        try:
            telemetry.configure_logging(self.path, force=True, console=False)
            log_event("cycle_finished", status="success", component="monitor", cycle_id="c-1", fetched=5)
            for handler in logging.getLogger("iphone_monitor").handlers:
                handler.flush()
            result = read_log_entries(self.path)
            entry = result["entries"][-1]
            self.assertEqual(entry["event"], "cycle_finished")
            self.assertEqual(entry["cycle_id"], "c-1")
            self.assertEqual(entry["details"]["fetched"], 5)
        finally:
            # Windows keeps the log file locked until the handler is closed,
            # which would break the temporary-directory cleanup.
            telemetry._reset_handlers()


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {
                "event": "listing_notified",
                "status": "success",
                "level": "INFO",
                "component": "telegram",
                "timestamp": 300.0,
                "details": {"price_rub": 58990},
            },
            {
                "event": "listing_skipped",
                "status": "skipped",
                "level": "INFO",
                "component": "monitor",
                "timestamp": 200.0,
                "listing_id": "42",
                "details": {"reason": "low_margin"},
            },
            {
                "event": "telegram_request_failed",
                "status": "failed",
                "level": "WARNING",
                "component": "bot",
                "timestamp": 100.0,
                "details": {"error": "timeout"},
            },
        ]

    def test_status_filter(self):
        selected = select_entries(self.entries, LogFilters(status=("failed",)), limit=10)
        self.assertEqual([entry["event"] for entry in selected], ["telegram_request_failed"])

    def test_event_prefix_filter(self):
        selected = select_entries(self.entries, LogFilters(event=("telegram",)), limit=10)
        self.assertEqual([entry["event"] for entry in selected], ["telegram_request_failed"])

    def test_time_range_filter(self):
        selected = select_entries(self.entries, LogFilters(since=150.0, until=250.0), limit=10)
        self.assertEqual([entry["event"] for entry in selected], ["listing_skipped"])

    def test_listing_and_search_filter(self):
        self.assertEqual(
            [entry["event"] for entry in select_entries(self.entries, LogFilters(listing_id="42"))],
            ["listing_skipped"],
        )
        self.assertEqual(
            [entry["event"] for entry in select_entries(self.entries, LogFilters(search="low_margin"))],
            ["listing_skipped"],
        )

    def test_cursor_pagination_never_repeats_a_record(self):
        first = select_entries(self.entries, limit=2)
        self.assertEqual(len(first), 2)
        cursor = first[-1]["timestamp"]
        second = select_entries(self.entries, before=cursor, limit=2)
        self.assertEqual([entry["event"] for entry in second], ["telegram_request_failed"])

    def test_limit_is_applied(self):
        self.assertEqual(len(select_entries(self.entries, limit=1)), 1)

    def test_matches_ignores_unknown_entry_fields(self):
        self.assertTrue(matches({"event": "x", "status": "success"}, LogFilters()))

    def test_facets_list_what_the_window_contains(self):
        facets = build_facets(self.entries)
        self.assertEqual(facets["statuses"], ["failed", "skipped", "success"])
        self.assertIn("telegram_notified" if False else "listing_notified", facets["events"])
        self.assertEqual(facets["components"], ["bot", "monitor", "telegram"])


if __name__ == "__main__":
    unittest.main()
