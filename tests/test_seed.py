import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from iphone_monitor.main import apply_seed_state
from iphone_monitor.store import SeenStore


def _payload() -> dict:
    return {
        "seen_listings": [
            {"item_id": "1", "first_seen_at": "2026-10-30 18:00:00"},
            {"item_id": "2", "first_seen_at": None},
        ],
        "notified_listings": [
            {
                "item_id": "1",
                "notified_at": "2026-10-30 18:05:00",
                "title": "iPhone 14 Pro, 256 ГБ",
                "price_rub": 33_000,
                "market_price_rub": 38_000,
                "margin_rub": 3_100,
                "url": "https://www.avito.ru/x/1",
                "model": "iPhone 14 Pro",
                "storage_gb": 256,
            }
        ],
        "listing_prices": [
            {
                "item_id": "1",
                "model": "iPhone 14 Pro",
                "storage_gb": 256,
                "condition": None,
                "price_rub": 33_000,
                "seen_at": "2026-10-30 18:00:00",
            }
        ],
        "subscribers": [{"chat_id": "7219843760"}],
    }


class SeedStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.store = SeenStore(self.root / "seen.sqlite3")
        self.addCleanup(self.store.close)

    def test_import_fills_every_table(self) -> None:
        counts = self.store.import_state(_payload())

        self.assertEqual(counts, {"seen": 2, "notified": 1, "prices": 1, "subscribers": 1})
        self.assertTrue(self.store.has_seen("1"))
        self.assertTrue(self.store.has_seen("2"))
        self.assertEqual(self.store.count_notified_since("2000-01-01"), 1)
        self.assertEqual(self.store.list_subscribers(), ("7219843760",))
        self.assertEqual(self.store.comparable_prices("iPhone 14 Pro", 256, None), (33_000,))

    def test_import_is_idempotent(self) -> None:
        self.store.import_state(_payload())
        second = self.store.import_state(_payload())

        self.assertEqual(second, {"seen": 0, "notified": 0, "prices": 0, "subscribers": 0})

    def test_existing_rows_win_over_snapshot(self) -> None:
        self.store.record_notified(
            item_id="1",
            title="свежее",
            price_rub=1,
            market_price_rub=None,
            margin_rub=None,
            url="https://example.com/1",
            notified_at="2026-10-31 00:00:00",
        )
        self.store.import_state(_payload())

        rows = self.store.top_notified_since("2000-01-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "свежее")

    def test_seed_is_applied_once_and_marks_metadata(self) -> None:
        path = self.root / "seed.json"
        path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
        settings = SimpleNamespace(seed_state_path=str(path))

        first = apply_seed_state(self.store, settings)
        second = apply_seed_state(self.store, settings)

        self.assertEqual(first["notified"], 1)
        self.assertIsNone(second)
        self.assertEqual(self.store.get_metadata("seed_state_applied"), "1")

    def test_missing_file_is_not_fatal_and_keeps_flag_unset(self) -> None:
        settings = SimpleNamespace(seed_state_path=str(self.root / "нет.json"))

        self.assertIsNone(apply_seed_state(self.store, settings))
        self.assertIsNone(self.store.get_metadata("seed_state_applied"))

    def test_empty_path_disables_seeding(self) -> None:
        self.assertIsNone(apply_seed_state(self.store, SimpleNamespace(seed_state_path="")))
