import unittest

from iphone_monitor.analysis import analyze_listing
from iphone_monitor.avito_parser import parse_card


class AnalysisTests(unittest.TestCase):
    def test_estimates_market_and_margin_from_comparables(self):
        listing = parse_card(
            {
                "id": "i123",
                "title": "iPhone 13 128 ГБ",
                "url": "https://www.avito.ru/item/123",
                "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее\nАккумулятор 84%.",
                "images": [],
            }
        )

        result = analyze_listing(listing, [30_000, 32_000, 31_000])

        self.assertEqual(result.market_price_rub, 31_000)
        self.assertEqual(result.margin_rub, 9_100)
        self.assertIn("Аккумулятор 84%", result.negatives)

    def test_missing_market_data_does_not_invent_margin(self):
        listing = parse_card(
            {
                "id": "i123",
                "title": "iPhone 13",
                "url": "https://www.avito.ru/item/123",
                "text": "iPhone 13\n20 000 ₽",
                "images": [],
            }
        )

        result = analyze_listing(listing, [])

        self.assertIsNone(result.market_price_rub)
        self.assertIsNone(result.margin_rub)
