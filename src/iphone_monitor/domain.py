"""Pure domain calculations for listing profitability."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    price_rub: int
    market_price_rub: int
    delivery_rub: int = 400
    commission_rub: int = 0
    repair_reserve_rub: int = 0


@dataclass(frozen=True)
class MarginResult:
    margin_rub: int
    is_promising: bool


def calculate_margin(
    listing: Listing,
    minimum_margin_rub: int = 500,
) -> MarginResult:
    """Estimate resale margin after predictable extra costs."""
    margin = (
        listing.market_price_rub
        - listing.price_rub
        - listing.delivery_rub
        - listing.commission_rub
        - listing.repair_reserve_rub
    )
    return MarginResult(
        margin_rub=margin,
        is_promising=margin >= minimum_margin_rub,
    )
