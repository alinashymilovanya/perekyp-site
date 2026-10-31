import json
import subprocess
import unittest
from unittest.mock import patch

from iphone_monitor.avito_browser import BrowserActError, AvitoBrowserSource, parse_cards_json


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

    def test_fetch_opens_configured_browser_session(self):
        raw = json.dumps([], ensure_ascii=False)
        runner = FakeRunner(raw)
        source = AvitoBrowserSource(
            browser_act_path="browser-act",
            browser_id="chrome_local_1",
            session_name="avito-check",
            search_url="https://www.avito.ru/search",
            runner=runner,
        )

        source.fetch()

        self.assertIn(
            ("browser", "open", "chrome_local_1", "https://www.avito.ru/search"),
            runner.calls,
        )

    def test_fetch_limits_extraction_to_newest_cards(self):
        runner = FakeRunner(json.dumps([], ensure_ascii=False))
        source = AvitoBrowserSource(
            browser_act_path="browser-act",
            session_name="avito-check",
            max_listings=12,
            runner=runner,
        )

        source.fetch()

        eval_call = next(call for call in runner.calls if call[0] == "eval")
        self.assertIn("slice(0, 12)", eval_call[1])

    def test_command_timeout_becomes_recoverable_source_error(self):
        source = AvitoBrowserSource(
            browser_act_path="browser-act",
            session_name="avito-check",
        )

        with patch(
            "iphone_monitor.avito_browser.subprocess.run",
            side_effect=subprocess.TimeoutExpired("browser-act", 45),
        ):
            with self.assertRaises(BrowserActError):
                source._run_command(["state"])
