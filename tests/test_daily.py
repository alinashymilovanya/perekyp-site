import unittest
from datetime import datetime, timezone

from iphone_monitor.daily import daily_summary_due, daily_window


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
