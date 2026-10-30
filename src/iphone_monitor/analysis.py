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


def _repair_reserve(
    condition: str | None,
    *,
    used_reserve_rub: int = 1_500,
    broken_reserve_rub: int = 10_000,
) -> int:
    if condition == "Новый":
        return 0
    if condition == "Битый":
        return broken_reserve_rub
    return used_reserve_rub


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


def _robust_market(prices: list[int]) -> int | None:
    """Median of comparable prices, with implausible outliers trimmed away.

    Two observations are accepted only when they agree within 15%: on a fresh
    installation that is the difference between "no market at all" and a rough
    but honest estimate. A lone price is never used as a market.
    """
    if len(prices) == 2:
        low, high = sorted(prices)
        if low <= 0 or high > low * 1.15:
            return None
        return int(median((low, high)))
    if len(prices) < 3:
        return None
    ordered = sorted(prices)
    center = median(ordered)
    kept = [price for price in ordered if center * 0.5 <= price <= center * 2]
    if len(kept) >= 3:
        return int(median(kept))
    return int(center)


def analyze_listing(
    listing: ParsedListing,
    comparable_prices: list[int],
    *,
    delivery_rub: int = 400,
    commission_rub: int = 0,
    minimum_margin_rub: int = 500,
    repair_reserve_used_rub: int = 1_500,
    repair_reserve_broken_rub: int = 10_000,
) -> ListingAnalysis:
    market_price = _robust_market(list(comparable_prices))
    reserve = _repair_reserve(
        listing.condition,
        used_reserve_rub=repair_reserve_used_rub,
        broken_reserve_rub=repair_reserve_broken_rub,
    )
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
