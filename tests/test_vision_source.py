import json
import unittest

from iphone_monitor.vision_source import VisionSource


class VisionSourceTests(unittest.TestCase):
    def test_parses_limited_cards_from_vision_runner(self):
        payload = json.dumps(
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
        calls = []

        def runner():
            calls.append(True)
            return payload

        source = VisionSource(
            node_path="node",
            playwright_path="playwright",
            api_token="secret",
            folder_id="folder",
            profile_id="profile",
            search_url="https://www.avito.ru/search",
            max_listings=20,
            runner=runner,
        )

        listings = source.fetch()

        self.assertEqual([listing.item_id for listing in listings], ["123"])
        self.assertEqual(calls, [True])
