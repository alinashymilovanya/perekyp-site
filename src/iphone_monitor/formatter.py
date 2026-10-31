"""Telegram message formatting without network or side effects."""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_CAPTION_CHARS = 1_024
DESCRIPTION_LIMIT = 300
ADDENDUM_DESCRIPTION_LIMIT = 600
ADDENDUM_MAX_CHARS = 3_900
SUMMARY_TIMEZONE = "Europe/Astrakhan"
TRUNCATION_MARK = "…"


@dataclass(frozen=True)
class ListingView:
    title: str
    price_rub: int
    market_price_rub: int | None
    margin_rub: int | None
    url: str
    condition: str = "не указано"
    battery: str = "не указана"
    positives: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    weak_threshold_rub: int | None = None
    description: str = ""
    location: str = ""
    seller: str = ""
    published_at: str = ""
    extras: tuple[str, ...] = ()


def _rubles(value: int | None) -> str:
    return "не рассчитана" if value is None else f"{value:,}".replace(",", " ") + " ₽"


def _is_weak(listing: ListingView) -> bool:
    return (
        listing.weak_threshold_rub is not None
        and listing.margin_rub is not None
        and listing.margin_rub < listing.weak_threshold_rub
    )


def _shorten(text: str, limit: int) -> str:
    """Collapse the whitespace sellers love and cut on a word boundary."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rstrip()
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut + TRUNCATION_MARK


def _published(value: str) -> str:
    """'2026-10-30T14:01:00+00:00' -> '30.10 14:01' in the reader's timezone."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(SUMMARY_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    local = moment.astimezone(zone)
    today = datetime.now(zone).date()
    stamp = local.strftime("%H:%M")
    if local.date() == today:
        return f"сегодня {stamp}"
    return local.strftime("%d.%m.%Y ") + stamp


def _render(listing: ListingView, description_limit: int) -> str:
    positives = "\n".join(f"+ {item}" for item in listing.positives) or "+ нет данных"
    negatives = "\n".join(f"− {item}" for item in listing.negatives) or "− нет данных"
    parts = [
        f"📱 {listing.title}",
        "",
        f"Цена: {_rubles(listing.price_rub)}",
        f"Рынок: {_rubles(listing.market_price_rub)}",
        f"Потенциальная маржа: {_rubles(listing.margin_rub)}",
    ]
    if _is_weak(listing):
        parts.append(
            f"⚠️ Слабая сделка: маржа меньше {_rubles(listing.weak_threshold_rub)}, "
            "её может съесть дорога и торг"
        )
    parts.append("")
    parts.append(f"Состояние: {listing.condition}")
    parts.append(f"Аккумулятор: {listing.battery}")
    if listing.extras:
        parts.append(" · ".join(listing.extras))
    if listing.location:
        parts.append(f"📍 {listing.location}")
    if listing.seller:
        parts.append(f"👤 {listing.seller}")
    published = _published(listing.published_at)
    if published:
        parts.append(f"🕒 Опубликовано: {published}")
    parts.append("")
    parts.append(f"Плюсы:\n{positives}")
    parts.append("")
    parts.append(f"Минусы:\n{negatives}")
    description = _shorten(listing.description, description_limit)
    if description:
        parts.append("")
        parts.append(f"Описание: {description}")
    parts.append("")
    parts.append(f"🔗 {listing.url}")
    return "\n".join(parts)


def format_detail_addendum(
    *,
    title: str,
    url: str,
    condition: str | None = None,
    battery_percent: int | None = None,
    description: str = "",
    extras: tuple[str, ...] = (),
    location: str = "",
    published_at: str = "",
) -> str:
    """A second message with the page facts that arrived after the first one."""
    parts = ["🔎 Подробности объявления", f"📱 {title}"]
    lines = []
    if condition:
        lines.append(f"Состояние: {condition}")
    if battery_percent is not None:
        lines.append(f"Аккумулятор: {battery_percent}%")
    if extras:
        lines.append(" · ".join(extras))
    if location:
        lines.append(f"📍 {location}")
    published = _published(published_at)
    if published:
        lines.append(f"🕒 Опубликовано: {published}")
    if lines:
        parts.append("")
        parts.extend(lines)
    text = _shorten(description, ADDENDUM_DESCRIPTION_LIMIT)
    if text:
        parts.append("")
        parts.append(f"Описание: {text}")
    parts.append("")
    parts.append(f"🔗 {url}")
    message = "\n".join(parts)
    if len(message) > ADDENDUM_MAX_CHARS:
        message = message[: ADDENDUM_MAX_CHARS - 1] + TRUNCATION_MARK
    return message


def format_listing_message(listing: ListingView) -> str:
    """Build the notification; a photo caption must stay under 1 024 chars."""
    message = _render(listing, DESCRIPTION_LIMIT)
    if len(message) <= MAX_CAPTION_CHARS:
        return message
    for limit in (200, 120, 60, 0):
        message = _render(listing, limit)
        if len(message) <= MAX_CAPTION_CHARS:
            return message
    return message[: MAX_CAPTION_CHARS - 1] + TRUNCATION_MARK
