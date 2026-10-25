"""Daily summary scheduling in the user's chosen timezone."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def _summary_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def daily_window(now: datetime, timezone_name: str) -> tuple[str, datetime]:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    return local_now.date().isoformat(), local_start.astimezone(timezone.utc)


def daily_summary_due(
    now: datetime,
    last_sent_date: str | None,
    timezone_name: str,
    summary_time: str,
) -> bool:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    return local_now.date().isoformat() != last_sent_date and local_now.time() >= _summary_time(summary_time)
