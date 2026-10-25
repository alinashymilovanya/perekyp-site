import tempfile
import unittest
from pathlib import Path

from iphone_monitor.store import SeenStore


class SeenStoreTests(unittest.TestCase):
    def test_tracks_seen_listing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SeenStore(Path(directory) / "seen.sqlite3")

            self.assertFalse(store.has_seen("123"))
            store.mark_seen("123")
            self.assertTrue(store.has_seen("123"))

            store.close()

    def test_stores_notified_listing_for_daily_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_notified(
                    item_id="123",
                    title="iPhone 13",
                    price_rub=20_000,
                    market_price_rub=30_000,
                    margin_rub=8_100,
                    url="https://example.test/123",
                    notified_at="2026-10-24T12:00:00+00:00",
                )

                top = store.top_notified_since("2026-10-24T00:00:00+00:00")

                self.assertEqual(top[0].item_id, "123")
                self.assertEqual(top[0].margin_rub, 8_100)

    def test_persists_monitor_metadata_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen.sqlite3"
            with SeenStore(path) as store:
                store.set_metadata("telegram_chat_id", "123")

            with SeenStore(path) as reopened:
                self.assertEqual(reopened.get_metadata("telegram_chat_id"), "123")
