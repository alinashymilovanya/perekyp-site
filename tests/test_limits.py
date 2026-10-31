"""Тарифы: лимиты, задержка доставки и учёт сигналов в SQLite."""

import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor import plans  # noqa: E402
from iphone_monitor.avito_parser import ParsedListing  # noqa: E402
from iphone_monitor.monitor import Monitor  # noqa: E402
from iphone_monitor.store import SeenStore  # noqa: E402


class _Capture(logging.Handler):
    """Собирает записи лога, чтобы тест мог проверить событие и статус."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        try:
            self.records.append(json.loads(record.getMessage()))
        except ValueError:
            pass

    def events(self, name=None):
        return [item for item in self.records if name is None or item["event"] == name]

    def last(self, name):
        matches = self.events(name)
        return matches[-1] if matches else None


class FakeTelegram:
    def __init__(self):
        self.photos = []
        self.messages = []

    def send_photo(self, chat_id, photo, caption):
        self.photos.append((chat_id, photo, caption))

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


def _listing(item_id: str = "42") -> ParsedListing:
    return ParsedListing(
        item_id=item_id,
        title="iPhone 15 Pro 256 ГБ",
        model="15 Pro",
        storage_gb=256,
        price_rub=62_000,
        condition="Отличное",
        battery_percent=92,
        location="Москва",
        description="",
        url=f"https://www.avito.ru/item/{item_id}",
        images=(),
    )


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.store = SeenStore(Path(self._tempdir.name) / "seen.sqlite3")
        self.telegram = FakeTelegram()
        self._capture = _Capture()
        self._logger = logging.getLogger("iphone_monitor")
        self._logger.addHandler(self._capture)
        self._logger.setLevel(logging.INFO)

    def tearDown(self):
        self._logger.removeHandler(self._capture)
        self.store.close()
        self._tempdir.cleanup()

    def monitor(self, chat_id="111", **kwargs) -> Monitor:
        return Monitor(
            source=None,
            store=self.store,
            telegram=self.telegram,
            chat_ids=chat_id,
            delivery_rub=400,
            **kwargs,
        )


class AccountTests(_StoreCase):
    def test_a_fresh_account_is_free(self):
        account = self.store.account("999")
        self.assertEqual(account.plan, "free")
        self.assertIsNone(account.plan_until)

    def test_ensure_account_uses_the_configured_default(self):
        self.assertEqual(self.store.ensure_account("111", "pro").plan, "pro")
        # Повторный вызов не перезаписывает уже выданный тариф.
        self.assertEqual(self.store.ensure_account("111", "free").plan, "pro")

    def test_set_plan_records_the_expiry_date(self):
        until = plans.trial_until()
        account = self.store.set_plan("111", "pro", plan_until=until, trial_started_at=until)
        self.assertEqual(account.plan, "pro")
        self.assertEqual(account.plan_until, until)
        self.assertEqual(account.trial_started_at, until)

    def test_usage_counter_is_per_day(self):
        today = plans.day_key()
        tomorrow = plans.day_key(plans.now_utc() + timedelta(days=1))
        self.assertEqual(self.store.consume_signal("111", today), 1)
        self.assertEqual(self.store.consume_signal("111", today), 2)
        self.assertEqual(self.store.signals_used("111", tomorrow), 0)


class DeliveryTests(_StoreCase):
    def test_free_plan_waits_in_the_outbox_instead_of_sending(self):
        self.store.set_plan("111", "free")
        result = self.monitor()._send(_listing(), "текст")

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["delayed"], 1)
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(self.store.count_pending_notifications(), 1)
        event = self._capture.last("notification_delayed")
        self.assertEqual(event["status"], "queued")
        self.assertEqual(event["details"]["plan"], "free")
        self.assertEqual(event["details"]["delay_seconds"], plans.FREE_DELAY_SECONDS)

    def test_delayed_signal_is_not_due_before_its_moment(self):
        self.store.set_plan("111", "free")
        self.monitor()._send(_listing(), "текст")

        now = plans.iso_in(plans.now_utc(), 0)
        later = plans.iso_in(plans.now_utc(), plans.FREE_DELAY_SECONDS + 60)
        self.assertEqual(self.store.pending_notifications(due_at=now), ())
        self.assertEqual(len(self.store.pending_notifications(due_at=later)), 1)

    def test_free_limit_suppresses_the_fourth_signal(self):
        self.store.set_plan("111", "free")
        monitor = self.monitor()
        for index in range(plans.FREE.signals_per_day):
            result = monitor._send(_listing(str(index)), "текст")
            self.assertEqual(result["delayed"], 1)
            self.assertEqual(result["suppressed"], 0)

        result = monitor._send(_listing("late"), "текст")
        self.assertEqual(result["suppressed"], 1)
        self.assertEqual(result["delayed"], 0)
        self.assertEqual(self.store.count_pending_notifications(), plans.FREE.signals_per_day)
        event = self._capture.last("notification_suppressed")
        self.assertEqual(event["status"], "skipped")
        self.assertEqual(event["details"]["reason"], "limit_reached")
        self.assertEqual(event["details"]["limit"], plans.FREE.signals_per_day)

    def test_paid_plan_is_unlimited_and_sends_immediately(self):
        monitor = self.monitor(default_plan="pro")
        for index in range(6):
            monitor._send(_listing(str(index)), "текст")

        self.assertEqual(len(self.telegram.messages), 6)
        self.assertEqual(self.store.count_pending_notifications(), 0)
        self.assertEqual(self._capture.events("notification_delayed"), [])
        self.assertEqual(self._capture.events("notification_suppressed"), [])

    def test_expired_subscription_is_limited_as_free(self):
        expired = plans.iso_in(plans.now_utc(), -60)
        self.store.set_plan("111", "pro", plan_until=expired)
        monitor = self.monitor()

        for index in range(plans.FREE.signals_per_day):
            self.assertEqual(monitor._send(_listing(str(index)), "текст")["delayed"], 1)
        self.assertEqual(monitor._send(_listing("late"), "текст")["suppressed"], 1)


class DealTests(_StoreCase):
    def test_deals_summary_counts_only_sold_phones(self):
        self.store.add_deal(
            "111",
            listing_id="42",
            model="15 Pro",
            storage_gb=256,
            bought_price_rub=62_000,
            sold_price_rub=74_500,
            expenses_rub=2_000,
        )
        self.store.add_deal(
            "111", listing_id="43", model="14 Pro", bought_price_rub=41_500
        )

        summary = self.store.deals_summary("111")
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["sold"], 1)
        self.assertEqual(summary["profit_rub"], 10_500)
        self.assertEqual(summary["revenue_rub"], 74_500)
        self.assertEqual(summary["avg_profit_rub"], 10_500)

        deals = self.store.list_deals("111")
        self.assertEqual(deals[0]["status"], "in_progress")
        self.assertIsNone(deals[0]["profit_rub"])
        self.assertEqual(deals[1]["profit_rub"], 10_500)


if __name__ == "__main__":
    unittest.main()
