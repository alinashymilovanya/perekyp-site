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
        self.assertNotIn("Слабая сделка", message)

    def test_marks_small_margin_as_weak(self):
        listing = ListingView(
            title="iPhone 12 64 ГБ",
            price_rub=20_000,
            market_price_rub=22_000,
            margin_rub=700,
            url="https://www.avito.ru/item/456",
            weak_threshold_rub=1_500,
        )

        message = format_listing_message(listing)

        self.assertIn("⚠️ Слабая сделка: маржа меньше 1 500 ₽", message)

    def test_strong_margin_has_no_warning(self):
        listing = ListingView(
            title="iPhone 12 64 ГБ",
            price_rub=15_000,
            market_price_rub=22_000,
            margin_rub=5_000,
            url="https://www.avito.ru/item/789",
            weak_threshold_rub=1_500,
        )

        self.assertNotIn("Слабая сделка", format_listing_message(listing))
