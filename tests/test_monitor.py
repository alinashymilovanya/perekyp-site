import tempfile
import unittest
from pathlib import Path

from iphone_monitor.avito_parser import parse_card
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore


def listing(item_id: str, price: str) -> object:
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
        self.photos = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id, photo, caption):
        self.photos.append((chat_id, photo, caption))


class MonitorTests(unittest.TestCase):
    def test_first_scan_seeds_without_flooding_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                monitor = Monitor(FakeSource([listing("1", "20 000")]), store, telegram, "123")

                sent = monitor.process_once()

                self.assertEqual(sent, 0)
                self.assertEqual(telegram.messages, [])

    def test_new_listing_sends_photo_and_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                monitor = Monitor(
                    FakeSource([listing("1", "20 000"), listing("2", "25 000")]),
                    store,
                    telegram,
                    "123",
                )

                sent = monitor.process_once()

                self.assertEqual(sent, 1)
                self.assertEqual(telegram.photos[0][0], "123")
                self.assertIn("iPhone 13", telegram.messages[0][1])
