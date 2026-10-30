"""Telegram message formatting without network or side effects."""

from dataclasses import dataclass


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


def _rubles(value: int | None) -> str:
    return "не рассчитана" if value is None else f"{value:,}".replace(",", " ") + " ₽"


def _is_weak(listing: ListingView) -> bool:
    return (
        listing.weak_threshold_rub is not None
        and listing.margin_rub is not None
        and listing.margin_rub < listing.weak_threshold_rub
    )


def format_listing_message(listing: ListingView) -> str:
    positives = "\n".join(f"+ {item}" for item in listing.positives) or "+ нет данных"
    negatives = "\n".join(f"− {item}" for item in listing.negatives) or "− нет данных"
    weakness = ""
    if _is_weak(listing):
        weakness = (
            f"⚠️ Слабая сделка: маржа меньше {_rubles(listing.weak_threshold_rub)}, "
            "её может съесть дорога и торг\n"
        )
    return (
        f"📱 {listing.title}\n\n"
        f"Цена: {_rubles(listing.price_rub)}\n"
        f"Рынок: {_rubles(listing.market_price_rub)}\n"
        f"Потенциальная маржа: {_rubles(listing.margin_rub)}\n"
        f"{weakness}"
        f"Состояние: {listing.condition}\n"
        f"Аккумулятор: {listing.battery}\n\n"
        f"Плюсы:\n{positives}\n\n"
        f"Минусы:\n{negatives}\n\n"
        f"🔗 {listing.url}"
    )
