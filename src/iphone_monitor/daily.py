"""Daily summary scheduling in the user's chosen timezone."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .store import NotifiedListing


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


def _rubles(value: int | None) -> str:
    return "не рассчитана" if value is None else f"{value:,}".replace(",", " ") + " ₽"


def format_daily_summary(
    local_date: str,
    notified_count: int,
    listings: tuple[NotifiedListing, ...],
) -> str:
    message = [
        f"📊 Сводка за {local_date}",
        f"Уведомлений за день: {notified_count}",
    ]
    if not listings:
        message.append("\nНовых объявлений за этот день не было.")
        return "\n".join(message)

    message.append("\nЛучшие по потенциальной марже:")
    for position, listing in enumerate(listings, start=1):
        title = " ".join(listing.title.split())[:120]
        message.extend(
            (
                f"\n{position}. {title}",
                f"Цена: {_rubles(listing.price_rub)} · Рынок: {_rubles(listing.market_price_rub)}",
                f"Маржа: {_rubles(listing.margin_rub)}",
                listing.url,
            )
        )
    return "\n".join(message)


def send_daily_summary_if_due(
    store,
    telegram,
    chat_id: str,
    *,
    now: datetime,
    timezone_name: str,
    summary_time: str,
) -> bool:
    last_sent_date = store.get_metadata("daily_summary_date")
    if not daily_summary_due(now, last_sent_date, timezone_name, summary_time):
        return False

    local_date, start_at = daily_window(now, timezone_name)
    message = format_daily_summary(
        local_date,
        store.count_notified_since(start_at.isoformat()),
        store.top_notified_since(start_at.isoformat()),
    )
    telegram.send_message(chat_id, message)
    store.set_metadata("daily_summary_date", local_date)
    return True
