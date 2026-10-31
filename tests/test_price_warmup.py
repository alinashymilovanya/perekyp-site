import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.avito_api_source import AvitoApiSource
from iphone_monitor.avito_parser import ParsedListing
from iphone_monitor.main import warm_up_price_history
from iphone_monitor.store import SeenStore


def listing(item_id: str, title: str, price: int) -> ParsedListing:
    return ParsedListing(
        item_id=item_id,
        title=title,
        model="iPhone 15",
        storage_gb=128,
        price_rub=price,
        condition=None,
        battery_percent=None,
        location="Москва",
        description=title,
        url=f"https://www.avito.ru/{item_id}",
        images=(),
    )


class PriceWarmupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SeenStore(Path(self.tmp.name) / "seen.sqlite3")
        self.calls: list[str] = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _source(self, price: int = 55_000) -> AvitoApiSource:
        def fake_fetch(url: str) -> dict:
            self.calls.append(url)
            return {
                "items": [
                    {
                        "id": 1000 + len(self.calls),
                        "title": "iPhone 15, 128 ГБ, eSIM",
                        "priceDetailed": {"string": f"{price} ₽", "value": price},
                        "urlPath": "/moskva/telefony/iphone_15_128_gb_100",
                    }
                ]
            }

        return AvitoApiSource(query="iphone", fetch_json=fake_fetch)

    def test_warmup_records_prices_once(self):
        recorded = warm_up_price_history(self._source(), self.store)
        self.assertEqual(recorded, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("page=1", self.calls[0])
        self.assertEqual(self.store.comparable_prices("iPhone 15", 128, None).__len__(), 1)
        self.assertEqual(warm_up_price_history(self._source(), self.store), 0)
        self.assertEqual(len(self.calls), 1)

    def test_repeat_rounds_ask_the_same_page_again(self):
        recorded = warm_up_price_history(
            self._source(), self.store, rounds=2, pause_seconds=0
        )

        self.assertEqual(recorded, 2)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("page=1", self.calls[1])

    def test_keeps_marker_off_when_every_round_fails(self):
        def failing_fetch(url: str) -> dict:
            raise OSError("network down")

        recorded = warm_up_price_history(
            AvitoApiSource(fetch_json=failing_fetch), self.store, rounds=2, pause_seconds=0
        )
        self.assertEqual(recorded, 0)
        self.assertIsNone(self.store.get_metadata("price_warmup_done"))


if __name__ == "__main__":
    unittest.main()
