import tempfile
import unittest
from pathlib import Path

from iphone_monitor.avito_parser import parse_card
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore


def listing(item_id: str, price: str, title: str = "iPhone 13, 128 ГБ", condition: str = "Хорошее") -> object:
    return parse_card(
        {
            "id": item_id,
            "title": title,
            "url": f"https://www.avito.ru/item/{item_id}",
            "text": f"{title}\n{price} ₽\n{condition}\nАккумулятор 90%.",
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


class PriceHistoryStoreTests(unittest.TestCase):
    def test_recorded_prices_are_returned_as_comparables(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000"), listing("102", "32 000")])

                prices = store.comparable_prices("iPhone 13", 128, "Хорошее")

                self.assertEqual(sorted(prices), [30000, 32000])

    def test_comparables_can_exclude_the_listing_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000")])

                prices = store.comparable_prices("iPhone 13", 128, "Хорошее", exclude_item_id="101")

                self.assertEqual(prices, ())

    def test_repeated_recording_updates_the_price(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000")])
                store.record_prices([listing("101", "27 000")])

                self.assertEqual(store.comparable_prices("iPhone 13", 128, "Хорошее"), (27000,))

    def test_other_storage_is_not_mixed_in(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000")])
                store.record_prices([listing("102", "15 000", title="iPhone 13, 64 ГБ")])

                self.assertEqual(store.comparable_prices("iPhone 13", 128, "Хорошее"), (30000,))

    def test_other_condition_is_not_mixed_in(self):
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices([listing("101", "30 000")])
                store.record_prices([listing("102", "20 000", condition="Битый")])

                self.assertEqual(store.comparable_prices("iPhone 13", 128, "Хорошее"), (30000,))


class MonitorHistoryTests(unittest.TestCase):
    def test_market_price_survives_small_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_prices(
                    [listing("101", "30 000"), listing("102", "32 000"), listing("103", "28 000")]
                )
                for item_id in ("101", "102", "103"):
                    store.mark_seen(item_id)
                monitor = Monitor(FakeSource([listing("109", "20 000")]), store, telegram, "123")

                sent = monitor.process_once()

                self.assertEqual(sent, 1)
                self.assertIn("30 000", telegram.messages[0][1])
                self.assertIn("8 100", telegram.messages[0][1])

    def test_single_listing_page_does_not_invent_a_market_price(self):
        with tempfile.TemporaryDirectory() as directory:
            telegram = FakeTelegram()
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.mark_seen("1")
                monitor = Monitor(FakeSource([listing("109", "20 000")]), store, telegram, "123")

                sent = monitor.process_once()

                self.assertEqual(sent, 0)
                self.assertEqual(telegram.messages, [])


if __name__ == "__main__":
    unittest.main()
