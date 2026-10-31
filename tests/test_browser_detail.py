import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

TASKS = Path(__file__).resolve().parents[1] / "tasks"
if str(TASKS) not in sys.path:
    sys.path.insert(0, str(TASKS))

import detail_agent  # noqa: E402
from iphone_monitor.browser_detail import (  # noqa: E402
    BrowserDetailReader,
    _published_at,
    parse_browser_payload,
)
from iphone_monitor.item_detail import ItemDetailError  # noqa: E402

RAW = json.dumps(
    {
        "title": "iPhone 18 Pro, 256 ГБ, SIM + eSIM",
        "params": [
            ["Состояние", "Отличное"],
            ["Экран", "Без дефектов"],
            ["Корпус", "Без дефектов"],
            ["Состояние аккумулятора", "100 %"],
            ["Комплект", "Коробка, Провод зарядки"],
        ],
        "specs": [
            ["Производитель", "Apple"],
            ["Модель", "iPhone 18 Pro"],
            ["Встроенная память", "256 ГБ"],
            ["SIM-карты", "SIM + eSIM"],
            ["Цвет", "Красный"],
            ["Страна происхождения", "Гонконг"],
        ],
        "description": "iPhone 18 Pro 256 — Burgundy.\nУстройство без ремонтов.",
        "price": "113 990 ₽",
        "date": "· сегодня в 15:14",
        "item_id": "№ 8398543094",
        "seller": "i-Apple",
        "page": "iPhone 18 Pro ...",
    },
    ensure_ascii=False,
)


class ParsePayloadTests(unittest.TestCase):
    def test_maps_every_field(self):
        now = datetime(2026, 10, 31, 16, 0)
        detail = parse_browser_payload(RAW, "8398543094", now=now)
        self.assertEqual(detail.item_id, "8398543094")
        self.assertEqual(detail.title, "iPhone 18 Pro, 256 ГБ, SIM + eSIM")
        self.assertEqual(detail.condition, "Отличное")
        self.assertEqual(detail.battery_percent, 100)
        self.assertEqual(detail.storage_gb, 256)
        self.assertEqual(detail.color, "Красный")
        self.assertEqual(detail.sim, "SIM + eSIM")
        self.assertEqual(detail.seller_name, "i-Apple")
        self.assertEqual(detail.price_rub, 113990)
        self.assertEqual(detail.published_at, "2026-10-31T15:14:00")
        self.assertIn("без ремонтов", detail.description)

    def test_new_phone_condition(self):
        raw = json.dumps({"title": "iPhone 17", "params": [["Состояние", "Новое"]]})
        self.assertEqual(parse_browser_payload(raw, "1").condition, "Новый")

    def test_missing_condition_is_none(self):
        raw = json.dumps({"title": "iPhone 17", "params": [["Экран", "Без дефектов"]]})
        self.assertIsNone(parse_browser_payload(raw, "1").condition)

    def test_broken_json_raises(self):
        with self.assertRaises(ItemDetailError):
            parse_browser_payload("<html>Доступ ограничен</html>", "1")

    def test_block_page_is_not_a_card(self):
        raw = json.dumps(
            {
                "title": "Страница не загрузилась из-за проблем с IP",
                "params": [],
                "specs": [],
                "description": "",
                "price": "",
                "date": "",
                "item_id": "",
                "seller": "",
                "page": "Страница не загрузилась из-за проблем с IP",
            }
        )
        with self.assertRaises(ItemDetailError) as caught:
            parse_browser_payload(raw, "1")
        self.assertTrue(caught.exception.blocked)

    def test_block_page_with_nbsp_title_is_caught(self):
        raw = json.dumps(
            {"title": "Страница не загрузилась из-за проблем с IP", "params": []}
        )
        with self.assertRaises(ItemDetailError) as caught:
            parse_browser_payload(raw, "1")
        self.assertTrue(caught.exception.blocked)

    def test_empty_card_raises(self):
        raw = json.dumps({"title": "", "params": [], "description": "", "page": "Доступ ограничен"})
        with self.assertRaises(ItemDetailError):
            parse_browser_payload(raw, "1")

    def test_yesterday_and_month_dates(self):
        now = datetime(2026, 10, 31, 12, 0)
        self.assertEqual(_published_at("· вчера в 09:05", now=now), "2026-10-30T09:05:00")
        self.assertEqual(_published_at("29 октября в 21:40", now=now), "2026-10-29T21:40:00")
        self.assertEqual(_published_at("без даты", now=now), "")


class ReaderTests(unittest.TestCase):
    def listing(self):
        return SimpleNamespace(item_id="8398543094", url="https://www.avito.ru/moskva/x_8398543094")

    def reader(self, *, payload=RAW, state=""):
        calls = []

        def runner(args):
            calls.append(list(args))
            if args[0] == "eval":
                return payload
            if args[0] == "state":
                return state
            return ""

        reader = BrowserDetailReader(
            "browser-act.exe", "avito-check", "chrome_1", runner=runner, sleep=lambda s: None
        )
        return reader, calls

    def test_reads_the_card_in_the_browser(self):
        reader, calls = self.reader()
        detail = reader(self.listing())
        self.assertEqual(detail.condition, "Отличное")
        self.assertEqual(calls[0], ["browser", "open", "chrome_1", self.listing().url])
        self.assertEqual(calls[1], ["wait", "stable", "--timeout", "30000"])
        self.assertEqual(calls[2][0], "eval")

    def test_block_page_is_reported(self):
        reader, _ = self.reader(
            payload=json.dumps({"title": "", "params": [], "description": "", "page": ""}),
            state="Доступ ограничен: проблема с IP",
        )
        with self.assertRaises(ItemDetailError) as caught:
            reader(self.listing())
        self.assertTrue(caught.exception.blocked)

    def test_listing_without_url_fails(self):
        reader, _ = self.reader()
        with self.assertRaises(ItemDetailError):
            reader(SimpleNamespace(item_id="1", url=""))


class BuildReaderTests(unittest.TestCase):
    def test_uses_chrome_when_the_binary_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".exe") as handle:
            settings = SimpleNamespace(
                browser_act_path=handle.name,
                avito_session_name="avito-check",
                avito_browser_id="chrome_1",
            )
            self.assertIsInstance(detail_agent.build_reader(settings), BrowserDetailReader)

    def test_falls_back_to_http(self):
        settings = SimpleNamespace(
            browser_act_path="", avito_session_name="", avito_browser_id=""
        )
        self.assertIs(detail_agent.build_reader(settings), detail_agent.load_detail)


if __name__ == "__main__":
    unittest.main()
