import unittest

from iphone_monitor.runtime import RetrySchedule


class RetryScheduleTests(unittest.TestCase):
    def test_increases_delay_after_consecutive_failures(self):
        schedule = RetrySchedule(base_delay_seconds=60, max_delay_seconds=300)

        schedule.record_failure(100)
        first_next_attempt = schedule.next_attempt_at
        schedule.record_failure(200)

        self.assertEqual(first_next_attempt, 160)
        self.assertEqual(schedule.next_attempt_at, 320)
        self.assertEqual(schedule.failure_count, 2)

    def test_success_resets_failure_state(self):
        schedule = RetrySchedule(base_delay_seconds=60, max_delay_seconds=300)
        schedule.record_failure(100)

        schedule.record_success(200)

        self.assertEqual(schedule.failure_count, 0)
        self.assertTrue(schedule.can_attempt(200))
