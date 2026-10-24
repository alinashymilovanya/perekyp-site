"""Retry state for a source that may temporarily be unavailable."""

from dataclasses import dataclass


@dataclass
class RetrySchedule:
    base_delay_seconds: int = 60
    max_delay_seconds: int = 1_800
    failure_count: int = 0
    next_attempt_at: float = 0.0
    outage_started_at: float | None = None
    alert_sent: bool = False

    def can_attempt(self, now: float) -> bool:
        return now >= self.next_attempt_at

    def record_failure(self, now: float) -> None:
        self.failure_count += 1
        self.outage_started_at = self.outage_started_at or now
        delay = min(self.base_delay_seconds * (2 ** (self.failure_count - 1)), self.max_delay_seconds)
        self.next_attempt_at = now + delay

    def record_success(self, now: float) -> None:
        self.failure_count = 0
        self.next_attempt_at = now
        self.outage_started_at = None
        self.alert_sent = False

    def needs_alert(self, now: float, after_seconds: int) -> bool:
        return (
            not self.alert_sent
            and self.outage_started_at is not None
            and now - self.outage_started_at >= after_seconds
        )

    def mark_alert_sent(self) -> None:
        self.alert_sent = True
