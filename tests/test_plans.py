"""Каталог тарифов: цены, квоты, промокоды и сроки подписки."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor import plans  # noqa: E402

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


class CatalogTests(unittest.TestCase):
    def test_every_plan_is_reachable_by_code(self):
        for code in plans.PLAN_CHOICES:
            chosen = plans.plan(code)
            self.assertEqual(chosen.code, code)
            self.assertTrue(chosen.title)
            self.assertGreaterEqual(chosen.price_month_rub, 0)

    def test_free_is_limited_and_slow(self):
        self.assertEqual(plans.FREE.signals_per_day, 3)
        self.assertFalse(plans.FREE.unlimited)
        self.assertEqual(plans.FREE.queue_seconds, plans.FREE_DELAY_SECONDS)
        self.assertEqual(plans.FREE.max_filters, 1)
        self.assertFalse(plans.FREE.tracker)

    def test_paid_plans_are_unlimited_and_track_deals(self):
        for code in ("pro", "instant", "business"):
            chosen = plans.plan(code)
            self.assertTrue(chosen.unlimited)
            self.assertEqual(chosen.queue_seconds, 0)
            self.assertTrue(chosen.tracker)
            self.assertGreater(chosen.price_month_rub, plans.FREE.price_month_rub)

    def test_prices_grow_with_speed(self):
        self.assertLess(plans.PRO.price_month_rub, plans.INSTANT.price_month_rub)
        self.assertLess(plans.INSTANT.price_month_rub, plans.BUSINESS.price_month_rub)

    def test_yearly_price_is_cheaper_than_twelve_months(self):
        for code in ("pro", "instant", "business"):
            chosen = plans.plan(code)
            self.assertLess(chosen.price_year_rub, chosen.price_month_rub * 12)

    def test_unknown_code_is_free_and_never_raises(self):
        self.assertEqual(plans.plan("platinum").code, "free")
        self.assertEqual(plans.plan(None).code, "free")
        self.assertEqual(plans.plan("  PRO ").code, "pro")

    def test_legacy_plan_keeps_the_old_price(self):
        self.assertEqual(plans.LEGACY.price_month_rub, 399)
        self.assertTrue(plans.LEGACY.unlimited)

    def test_describe_is_one_short_line(self):
        self.assertEqual(plans.describe("free"), "Free · 3 в сутки · 30 мин")
        self.assertEqual(plans.describe("pro"), "Pro · без лимита · 90 сек")


class PromoTests(unittest.TestCase):
    def test_promo_halves_the_first_month(self):
        result = plans.discounted_price("pro", period="month", promo_code="first")
        self.assertEqual(result["price"], 345)
        self.assertEqual(result["discount"], 345)
        self.assertEqual(result["promo"], "FIRST")

    def test_promo_works_for_the_year_and_never_for_free(self):
        self.assertEqual(
            plans.discounted_price("instant", period="year", promo_code="FIRST")["price"],
            7_450,
        )
        self.assertEqual(plans.discounted_price("free", promo_code="FIRST")["price"], 0)

    def test_unknown_promo_keeps_the_full_price(self):
        result = plans.discounted_price("pro", promo_code="NOPE")
        self.assertEqual(result["price"], 690)
        self.assertEqual(result["discount"], 0)
        self.assertIsNone(result["promo"])


class SubscriptionTests(unittest.TestCase):
    def test_a_future_subscription_keeps_its_plan(self):
        until = plans.iso_in(NOW, 86_400)
        self.assertEqual(plans.effective_plan("pro", until, now=NOW).code, "pro")

    def test_an_expired_subscription_falls_back_to_free(self):
        until = plans.iso_in(NOW, -60)
        self.assertEqual(plans.effective_plan("instant", until, now=NOW).code, "free")

    def test_missing_or_broken_expiry_keeps_the_plan(self):
        self.assertEqual(plans.effective_plan("pro", None, now=NOW).code, "pro")
        self.assertEqual(plans.effective_plan("pro", "not-a-date", now=NOW).code, "pro")

    def test_trial_lasts_seven_days(self):
        self.assertEqual(plans.TRIAL_DAYS, 7)
        start = plans.day_key(NOW)
        self.assertEqual(start, "2026-10-31")
        deadline = plans.trial_until(NOW)
        self.assertEqual(deadline, "2026-11-07T12:00:00Z")


class QuotaTests(unittest.TestCase):
    def test_free_has_three_signals_a_day(self):
        self.assertEqual(plans.quota("free", 0).remaining, 3)
        self.assertFalse(plans.quota("free", 2).exhausted)
        self.assertTrue(plans.quota("free", 3).exhausted)
        self.assertTrue(plans.quota("free", 99).exhausted)

    def test_paid_has_no_ceiling(self):
        room = plans.quota("pro", 5_000)
        self.assertIsNone(room.limit)
        self.assertIsNone(room.remaining)
        self.assertFalse(room.exhausted)

    def test_quota_details_are_json_friendly(self):
        self.assertEqual(
            plans.quota("free", 2).details(),
            {"plan": "free", "limit": 3, "used": 2, "remaining": 1},
        )

    def test_day_bounds_are_utc_midnight(self):
        start, end = plans.day_bounds(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(), "2026-10-31T00:00:00+00:00")
        self.assertEqual(end - start, timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
