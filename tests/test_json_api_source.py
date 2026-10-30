import unittest

from iphone_monitor.json_api_source import JsonApiSource


class JsonApiSourceTests(unittest.TestCase):
    def test_reads_provider_items_in_the_normalized_card_format(self):
        calls = []

        def fetch_json(url, token):
            calls.append((url, token))
            return {
                "items": [
                    {
                        "id": "i123",
                        "title": "iPhone 13 128 ГБ",
                        "url": "https://provider.example/items/123",
                        "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее",
                        "images": [],
                    }
                ]
            }

        source = JsonApiSource(
            "https://provider.example/iphone-feed",
            "provider-token",
            fetch_json=fetch_json,
        )

        listings = source.fetch()

        self.assertEqual(listings[0].item_id, "123")
        self.assertEqual(calls, [("https://provider.example/iphone-feed", "provider-token")])

    def test_limits_items_after_provider_response(self):
        def fetch_json(url, token):
            return {
                "items": [
                    {
                        "id": f"i{index}",
                        "title": "iPhone 13 128 ГБ",
                        "url": f"https://provider.example/items/{index}",
                        "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее",
                        "images": [],
                    }
                    for index in range(3)
                ]
            }

        listings = JsonApiSource(
            "https://provider.example/iphone-feed",
            "provider-token",
            max_listings=2,
            fetch_json=fetch_json,
        ).fetch()

        self.assertEqual([listing.item_id for listing in listings], ["0", "1"])

    def test_rejects_non_https_provider_url(self):
        with self.assertRaises(ValueError):
            JsonApiSource("http://provider.example/feed", "token")
