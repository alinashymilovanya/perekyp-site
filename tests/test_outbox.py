import tempfile
import unittest
from pathlib import Path

from iphone_monitor.avito_parser import parse_card
from iphone_monitor.main import flush_pending_notifications
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore
from iphone_monitor.telegram_api import TelegramApiError


def listing(item_id: str, price: str, title: str = "iPhone 13, 128 ГБ") -> object:
    return parse_card(
        {
            "id": item_id,
            "title": title,
            "url": f"https://www.avito.ru/item/{item_id}",
            "text": f"{title}\n{price} ₽\nХорошее\nАккумулятор 90%.",
            "images": ["https://img.example/photo.jpg"],
        }
    )


class FakeSource:
    def __init__(self, listings):
        self.listings = listings

    def fetch(self):
        return tuple(self.listings)


class BrokenTelegram:
    def __init__(self):
        self.attempts = 0

    def send_message(self, chat_id, text):
        self.attempts += 1
        raise TelegramApiError("Telegram request failed")

    def send_photo(self, chat_id, photo, caption):
        self.attempts += 1
        raise TelegramApiError("Telegram request failed")


class WorkingTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, "message", text))

    def send_photo(self, chat_id, photo, caption):
        self.sent.append((chat_id, photo, caption))


class OutboxTests(unittest.TestCase):
    def test_failed_notification_is_queued_instead_of_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000"), listing("102", "32 000"), listing("103", "28 000")])
                for item_id in ("101", "102", "103"):
                    store.mark_seen(item_id)
                telegram = BrokenTelegram()
                monitor = Monitor(FakeSource([listing("109", "20 000")]), store, telegram, "123")

                monitor.process_once()

                # photo first, then a plain-text fallback
                self.assertEqual(telegram.attempts, 2)
                self.assertEqual(store.count_pending_notifications(), 1)
                pending = store.pending_notifications()
                self.assertEqual(pending[0][1], "123")
                self.assertIn("20 000", pending[0][3])

    def test_queued_notification_is_delivered_later(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.enqueue_notification("123", "photo", "iPhone 13 — 20 000 ₽", "https://img.example/a.jpg")
                telegram = WorkingTelegram()

                delivered = flush_pending_notifications(store, telegram)

                self.assertEqual(delivered, 1)
                self.assertEqual(store.count_pending_notifications(), 0)
                self.assertEqual(telegram.sent, [("123", "https://img.example/a.jpg", "iPhone 13 — 20 000 ₽")])

    def test_flush_stops_on_the_first_failure_and_keeps_the_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.enqueue_notification("123", "message", "one")
                store.enqueue_notification("123", "message", "two")

                delivered = flush_pending_notifications(store, BrokenTelegram())

                self.assertEqual(delivered, 0)
                self.assertEqual(store.count_pending_notifications(), 2)


if __name__ == "__main__":
    unittest.main()
