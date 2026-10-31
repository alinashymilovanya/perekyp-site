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

    def test_profitable_new_listing_sends_photo_and_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                monitor = Monitor(
                    FakeSource(
                        [
                            listing("1", "25 000"),
                            listing("3", "24 000"),
                            listing("4", "26 000"),
                            listing("2", "20 000"),
                        ]
                    ),
                    store,
                    telegram,
                    "123",
                )

                sent = monitor.process_once()

                self.assertEqual(sent, 1)
                self.assertEqual(telegram.photos[0][0], "123")
                self.assertIn("iPhone 13", telegram.photos[0][2])
                self.assertEqual(telegram.messages, [])
                notified = store.top_notified_since("2000-01-01T00:00:00+00:00")
                self.assertEqual(notified[0].item_id, "2")
                self.assertEqual(notified[0].price_rub, 20_000)
                self.assertEqual(notified[0].margin_rub, 3_100)

    def test_new_listing_without_an_image_sends_one_text_message(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            without_image = parse_card(
                {
                    "id": "i2",
                    "title": "iPhone 13 128 ГБ",
                    "url": "https://www.avito.ru/item/2",
                    "text": "iPhone 13 128 ГБ\n15 000 ₽\nХорошее",
                    "images": [],
                }
            )
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                sent = Monitor(
                    FakeSource(
                        [
                            listing("1", "20 000"),
                            listing("3", "22 000"),
                            listing("4", "21 000"),
                            without_image,
                        ]
                    ),
                    store,
                    telegram,
                    "123",
                ).process_once()

                self.assertEqual(sent, 1)
                self.assertEqual(telegram.photos, [])
                self.assertEqual(len(telegram.messages), 1)

    def test_unprofitable_listing_is_skipped_but_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                sent = Monitor(
                    FakeSource(
                        [
                            listing("1", "20 000"),
                            listing("3", "22 000"),
                            listing("4", "21 000"),
                            listing("2", "25 000"),
                        ]
                    ),
                    store,
                    telegram,
                    "123",
                ).process_once()

                self.assertEqual(sent, 0)
                self.assertEqual(telegram.messages, [])
                self.assertTrue(store.has_seen("2"))

    def test_every_subscriber_receives_the_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                sent = Monitor(
                    FakeSource(
                        [
                            listing("1", "25 000"),
                            listing("3", "24 000"),
                            listing("4", "26 000"),
                            listing("2", "20 000"),
                        ]
                    ),
                    store,
                    telegram,
                    ["111", "222"],
                ).process_once()

                self.assertEqual(sent, 1)
                self.assertEqual([photo[0] for photo in telegram.photos], ["111", "222"])

    def test_configured_repair_reserve_changes_the_margin(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                sent = Monitor(
                    FakeSource(
                        [
                            listing("1", "25 000"),
                            listing("3", "24 000"),
                            listing("4", "26 000"),
                            listing("2", "20 000"),
                        ]
                    ),
                    store,
                    telegram,
                    "123",
                    repair_reserve_used_rub=6_000,
                ).process_once()

                self.assertEqual(sent, 0)
                self.assertEqual(telegram.messages, [])
