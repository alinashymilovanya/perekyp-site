import json
import unittest

from iphone_monitor.avito_browser import AvitoBrowserSource, parse_cards_json


class FakeRunner:
    def __init__(self, eval_result):
        self.calls = []
        self.eval_result = eval_result

    def __call__(self, args):
        self.calls.append(tuple(args))
        if args and args[0] == "eval":
            return self.eval_result
        if args and args[0] == "state":
            return "[36]<a>\n    Сортировка\n[288]<button>\n    По дате"
        return "ok"


class AvitoBrowserTests(unittest.TestCase):
    def test_parses_cards_json(self):
        raw = json.dumps(
            [
                {
                    "id": "i123",
                    "title": "iPhone 13 128 ГБ",
                    "url": "https://www.avito.ru/item/123",
                    "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее",
                    "images": [],
                }
            ],
            ensure_ascii=False,
        )

        listings = parse_cards_json(raw)

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].item_id, "123")

    def test_fetch_initializes_sorted_search_and_extracts_cards(self):
        raw = json.dumps(
            [
                {
                    "id": "i123",
                    "title": "iPhone 13 128 ГБ",
                    "url": "https://www.avito.ru/item/123",
                    "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее",
                    "images": [],
                }
            ],
            ensure_ascii=False,
        )
        runner = FakeRunner(raw)
        source = AvitoBrowserSource(
            browser_act_path="browser-act",
            session_name="avito-check",
            search_url="https://www.avito.ru/search",
            runner=runner,
        )

        listings = source.fetch()

        self.assertEqual(listings[0].item_id, "123")
        self.assertIn(("navigate", "https://www.avito.ru/search"), runner.calls)
        self.assertIn(("click", "36"), runner.calls)
        self.assertIn(("click", "288"), runner.calls)
