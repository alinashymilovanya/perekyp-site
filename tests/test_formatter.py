import unittest

from iphone_monitor.formatter import ListingView, format_listing_message


class FormatterTests(unittest.TestCase):
    def test_formats_listing_with_margin_and_link(self):
        listing = ListingView(
            title="iPhone 13 128 ГБ",
            price_rub=40_000,
            market_price_rub=52_000,
            margin_rub=10_100,
            url="https://www.avito.ru/item/123",
            condition="б/у",
            battery="87%",
            positives=["комплект", "без трещин"],
            negatives=["есть следы использования"],
        )

        message = format_listing_message(listing)

        self.assertIn("iPhone 13 128 ГБ", message)
        self.assertIn("Потенциальная маржа: 10 100 ₽", message)
        self.assertIn("https://www.avito.ru/item/123", message)
        self.assertIn("+ комплект", message)
        self.assertIn("− есть следы использования", message)
