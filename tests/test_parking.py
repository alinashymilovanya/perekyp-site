"""Одно объявление = одно сообщение: парковка до прихода описания."""

import json
import tempfile
import unittest
from pathlib import Path

from iphone_monitor.avito_parser import parse_card
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore

DETAIL_PAYLOAD = {
    "item_id": "2",
    "title": "iPhone 13 128 ГБ",
    "description": "Трещина на корпусе, замена дисплея, всё работает",
    "price_rub": 20_000,
    "condition": "Хорошее",
    "battery_percent": 91,
    "storage_gb": 128,
    "color": "Чёрный",
    "sim": "SIM + eSIM",
    "seller_name": "Иван",
    "address": "Москва",
    "published_at": "2026-11-07T10:00:00+00:00",
    "images": [],
    "params": [],
}


def listing(item_id: str, price: str):
    return parse_card(
        {
            "id": f"i{item_id}",
            "title": "iPhone 13 128 ГБ",
            "url": f"https://www.avito.ru/item/{item_id}",
            "text": f"iPhone 13 128 ГБ\n{price} ₽\nХорошее\nАккумулятор 90%.",
            "images": ["https://img.example/photo.jpg"],
        }
    )


class FakeSource:
    def __init__(self, listings):
        self.listings = listings

    def fetch(self):
        return tuple(self.listings)


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id, photo, caption):
        self.messages.append((chat_id, caption))


class ParkingTests(unittest.TestCase):
    def _prepare(self, park_wait_minutes: int, source):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SeenStore(Path(self._tmp.name) / "seen.sqlite3")
        self.store.mark_seen("warmup")
        # Рынок для «iPhone 13 128 ГБ / Хорошее»: три сопоставимых цены.
        market = [listing("101", "30 000"), listing("102", "32 000"), listing("103", "28 000")]
        self.store.record_prices(market)
        for item in market:
            self.store.mark_seen(item.item_id)
        self.telegram = FakeTelegram()
        return Monitor(
            source,
            self.store,
            self.telegram,
            "123",
            park_wait_minutes=park_wait_minutes,
        )

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_listing_waits_for_detail_instead_of_blank_message(self):
        monitor = self._prepare(15, FakeSource([listing("2", "20 000")]))

        sent = monitor.process_once()

        self.assertEqual(sent, 0)
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(self.store.count_parked(), 1)
        self.assertTrue(self.store.has_seen("2"))

    def test_parked_listing_sent_with_description_when_card_arrives(self):
        monitor = self._prepare(15, FakeSource([listing("2", "20 000")]))
        self.assertEqual(monitor.process_once(), 0)

        self.store._connection.execute(
            "INSERT OR REPLACE INTO item_details(item_id, fetched_at, payload) "
            "VALUES (?, CURRENT_TIMESTAMP, ?)",
            ("2", json.dumps(DETAIL_PAYLOAD)),
        )
        self.store._connection.commit()

        sent = monitor.process_once()

        self.assertEqual(sent, 1)
        self.assertEqual(self.store.count_parked(), 0)
        texts = [text for _, text in self.telegram.messages]
        joined = " | ".join(texts)
        self.assertEqual(len(texts), 1)
        self.assertIn("iPhone 13", joined)
        self.assertIn("Трещина на корпусе", joined)
        self.assertIn("Состояние: Хорошее", joined)

    def test_expired_listing_sent_without_detail_text(self):
        monitor = self._prepare(15, FakeSource([listing("2", "20 000")]))
        self.assertEqual(monitor.process_once(), 0)

        self.store.age_parked("2", 9_000)
        sent = monitor.process_once()

        self.assertEqual(sent, 1)
        self.assertEqual(self.store.count_parked(), 0)
        texts = [text for _, text in self.telegram.messages]
        self.assertEqual(len(texts), 1)
        self.assertIn("iPhone 13", texts[0])
        self.assertNotIn("Трещина", texts[0])


if __name__ == "__main__":
    unittest.main()
