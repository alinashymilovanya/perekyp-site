import unittest

from iphone_monitor.domain import Listing, calculate_margin


class MarginTests(unittest.TestCase):
    def test_margin_includes_delivery_and_repair_reserve(self):
        listing = Listing(
            price_rub=40_000,
            market_price_rub=52_000,
            delivery_rub=400,
            commission_rub=0,
            repair_reserve_rub=1_500,
        )

        result = calculate_margin(listing)

        self.assertEqual(result.margin_rub, 10_100)
        self.assertTrue(result.is_promising)

    def test_margin_is_not_promising_below_threshold(self):
        listing = Listing(
            price_rub=50_000,
            market_price_rub=50_800,
            delivery_rub=400,
            commission_rub=0,
            repair_reserve_rub=0,
        )

        result = calculate_margin(listing, minimum_margin_rub=500)

        self.assertEqual(result.margin_rub, 400)
        self.assertFalse(result.is_promising)
