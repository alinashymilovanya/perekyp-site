"""Heuristics for market price, margin and transparent risk notes."""

import re
from dataclasses import dataclass
from statistics import median

from .avito_parser import ParsedListing
from .domain import Listing, calculate_margin


@dataclass(frozen=True)
class ListingAnalysis:
    market_price_rub: int | None
    margin_rub: int | None
    repair_reserve_rub: int
    positives: tuple[str, ...]
    negatives: tuple[str, ...]


def _repair_reserve(condition: str | None) -> int:
    if condition == "Новый":
        return 0
    if condition == "Битый":
        return 10_000
    return 1_500


def _description_notes(listing: ParsedListing) -> tuple[list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    text = listing.description.lower()

    if listing.condition:
        positives.append(f"Состояние: {listing.condition}")
    if listing.storage_gb:
        positives.append(f"Память: {listing.storage_gb} ГБ")
    if listing.battery_percent is not None:
        if listing.battery_percent >= 85:
            positives.append(f"Аккумулятор {listing.battery_percent}%")
        else:
            negatives.append(f"Аккумулятор {listing.battery_percent}%")
    else:
        negatives.append("Аккумулятор не указан")

    if re.search(r"ремонт|менял|замен", text):
        negatives.append("В описании упомянуты ремонт или замена деталей")
    if re.search(r"трещ|разбит|скол", text):
        negatives.append("В описании упомянуты трещины, разбитие или сколы")
    if listing.condition == "Битый":
        negatives.append("Нужна отдельная оценка ремонта")
    return positives, negatives


def analyze_listing(
    listing: ParsedListing,
    comparable_prices: list[int],
    *,
    delivery_rub: int = 400,
    commission_rub: int = 0,
    minimum_margin_rub: int = 500,
) -> ListingAnalysis:
    market_price = int(median(comparable_prices)) if comparable_prices else None
    reserve = _repair_reserve(listing.condition)
    positives, negatives = _description_notes(listing)
    margin = None
    if market_price is not None:
        margin = calculate_margin(
            Listing(
                price_rub=listing.price_rub,
                market_price_rub=market_price,
                delivery_rub=delivery_rub,
                commission_rub=commission_rub,
                repair_reserve_rub=reserve,
            ),
            minimum_margin_rub=minimum_margin_rub,
        ).margin_rub
    return ListingAnalysis(
        market_price_rub=market_price,
        margin_rub=margin,
        repair_reserve_rub=reserve,
        positives=tuple(positives),
        negatives=tuple(negatives),
    )
