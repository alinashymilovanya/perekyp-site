import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from server import list_notified_listings, read_dashboard_status


class DashboardDataTests(unittest.TestCase):
    def test_reads_fresh_bot_status(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "runtime-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "heartbeat_at": 1_000,
                        "source_failure_count": 0,
                        "monitoring_enabled": True,
                    }
                ),
                encoding="utf-8",
            )

            status = read_dashboard_status(status_path, now=1_030)

            self.assertTrue(status["healthy"])
            self.assertTrue(status["monitoring_enabled"])

    def test_reads_recent_listings_from_bot_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "seen.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "CREATE TABLE notified_listings ("
                    "item_id TEXT PRIMARY KEY, notified_at TEXT NOT NULL, title TEXT NOT NULL, "
                    "price_rub INTEGER NOT NULL, market_price_rub INTEGER, margin_rub INTEGER, "
                    "url TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO notified_listings VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("1", "2026-10-26T10:00:00+00:00", "iPhone 15", 40_000, 50_000, 9_600, "https://example.test/1"),
                )
                connection.commit()
            finally:
                connection.close()

            listings = list_notified_listings(database_path, limit=5)

            self.assertEqual(listings[0]["title"], "iPhone 15")
            self.assertEqual(listings[0]["margin_rub"], 9_600)


if __name__ == "__main__":
    unittest.main()
