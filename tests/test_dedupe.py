"""The same seller's identical listing must not be announced twice."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iphone_monitor.avito_parser import ParsedListing
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id, photo, caption):
        self.messages.append((chat_id, caption))


class FakeSource:
    def __init__(self, listings):
        self._listings = tuple(listings)

    def fetch(self):
        return self._listings


def _listing(item_id, price, title="iPhone 17 Pro Max, 1 ТБ, eSIM"):
    return ParsedListing(
        item_id=item_id,
        title=title,
        model="iPhone 17 Pro Max",
        storage_gb=1024,
        price_rub=price,
        condition=None,
        battery_percent=None,
        location="Москва",
        description=title,
        url=f"https://www.avito.ru/{item_id}",
        images=(),
    )


class DedupeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SeenStore(Path(self._tmp.name) / "seen.sqlite3")
        # A first-ever poll is treated as warm-up and never notifies.
        self.store.mark_seen("warmup")
        now = datetime.now(timezone.utc).isoformat()
        self.store.record_prices(
            [
                _listing("a1", 130_000),
                _listing("a2", 132_000),
                _listing("a3", 134_000),
            ]
        )
        self.store.record_notified(
            item_id="prev",
            title="iPhone 17 Pro Max, 1 ТБ, SIM + eSIM",
            price_rub=120_000,
            market_price_rub=132_000,
            margin_rub=12_000,
            url="https://www.avito.ru/prev",
            notified_at=now,
            model="iPhone 17 Pro Max",
            storage_gb=1024,
        )

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_same_price_is_skipped(self):
        telegram = FakeTelegram()
        monitor = Monitor(
            FakeSource([_listing("b1", 120_000)]),
            self.store,
            telegram,
            "42",
            notify_min_margin_rub=1,
        )
        self.assertEqual(monitor.process_once(), 0)
        self.assertEqual(telegram.messages, [])

    def test_other_price_is_announced(self):
        telegram = FakeTelegram()
        monitor = Monitor(
            FakeSource([_listing("b2", 115_000)]),
            self.store,
            telegram,
            "42",
            notify_min_margin_rub=1,
        )
        self.assertEqual(monitor.process_once(), 1)
        self.assertEqual(len(telegram.messages), 1)

    def test_old_notification_allows_repeat(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        self.store.record_notified(
            item_id="old",
            title="iPhone 17 Pro Max, 1 ТБ, eSIM",
            price_rub=110_000,
            market_price_rub=132_000,
            margin_rub=22_000,
            url="https://www.avito.ru/old",
            notified_at=old,
            model="iPhone 17 Pro Max",
            storage_gb=1024,
        )
        telegram = FakeTelegram()
        monitor = Monitor(
            FakeSource([_listing("b3", 110_000)]),
            self.store,
            telegram,
            "42",
            notify_min_margin_rub=1,
        )
        self.assertEqual(monitor.process_once(), 1)


if __name__ == "__main__":
    unittest.main()
