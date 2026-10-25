import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iphone_monitor.daily import daily_summary_due, daily_window, send_daily_summary_if_due
from iphone_monitor.store import SeenStore


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class DailyTests(unittest.TestCase):
    def test_summary_is_due_once_after_configured_astrakhan_time(self):
        now = datetime(2026, 10, 24, 16, 5, tzinfo=timezone.utc)  # 20:05 in Astrakhan

        self.assertTrue(daily_summary_due(now, None, "Europe/Astrakhan", "20:00"))
        self.assertFalse(daily_summary_due(now, "2026-10-24", "Europe/Astrakhan", "20:00"))

    def test_daily_window_starts_at_local_midnight(self):
        now = datetime(2026, 10, 24, 16, 5, tzinfo=timezone.utc)

        local_date, start_utc = daily_window(now, "Europe/Astrakhan")

        self.assertEqual(local_date, "2026-10-24")
        self.assertEqual(start_utc.isoformat(), "2026-10-23T20:00:00+00:00")

    def test_sends_one_daily_summary_after_the_configured_time(self):
        now = datetime(2026, 10, 24, 16, 5, tzinfo=timezone.utc)
        telegram = FakeTelegram()
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.record_notified(
                    item_id="123",
                    title="iPhone 13 128 ГБ",
                    price_rub=20_000,
                    market_price_rub=30_000,
                    margin_rub=8_100,
                    url="https://example.test/123",
                    notified_at="2026-10-24T12:00:00+00:00",
                )

                sent = send_daily_summary_if_due(
                    store,
                    telegram,
                    "123",
                    now=now,
                    timezone_name="Europe/Astrakhan",
                    summary_time="20:00",
                )

                self.assertTrue(sent)
                self.assertIn("Сводка за 2026-10-24", telegram.messages[0][1])
                self.assertIn("iPhone 13 128 ГБ", telegram.messages[0][1])
                self.assertIn("8 100 ₽", telegram.messages[0][1])
                self.assertEqual(store.get_metadata("daily_summary_date"), "2026-10-24")
                self.assertFalse(
                    send_daily_summary_if_due(
                        store,
                        telegram,
                        "123",
                        now=now,
                        timezone_name="Europe/Astrakhan",
                        summary_time="20:00",
                    )
                )
