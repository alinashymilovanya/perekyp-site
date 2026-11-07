"""Pure domain calculations for listing profitability."""

from dataclasses import dataclass

# Сколько съедает рынок до того, как маржа станет деньгами:
# покупатель почти всегда торгуется у витринной цены, площадка берёт
# комиссию с продажи, а налог — это доход, который декларируется.
TRADE_DISCOUNT_PCT = 10.0
SELLER_COMMISSION_PCT = 5.0
SALES_TAX_PCT = 4.0


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


def _percent_share(amount: int, pct: float) -> int:
    """Целая доля, урезанная вниз: оценки расходов не должны себе льстить."""
    if amount <= 0 or pct <= 0:
        return 0
    return int(amount * pct // 100)


def calculate_margin(
    listing: Listing,
    minimum_margin_rub: int = 500,
    *,
    trade_discount_pct: float = TRADE_DISCOUNT_PCT,
    seller_commission_pct: float = SELLER_COMMISSION_PCT,
    tax_pct: float = SALES_TAX_PCT,
) -> MarginResult:
    """Estimate resale margin after predictable extra costs.

    Витринная «цена рынка» складывается из цен предложения, а не сделок,
    поэтому продать получится дешевле: сначала с неё снимается торг,
    затем комиссия площадки и налог. Только остаток участвует в марже.
    Плоская ``commission_rub`` остаётся для явных расходов сверх процентов.
    """
    sale_price = listing.market_price_rub - _percent_share(
        listing.market_price_rub, trade_discount_pct
    )
    commission_cost = _percent_share(sale_price, seller_commission_pct)
    tax_cost = _percent_share(sale_price, tax_pct)
    margin = (
        sale_price
        - listing.price_rub
        - listing.delivery_rub
        - listing.commission_rub
        - commission_cost
        - tax_cost
        - listing.repair_reserve_rub
    )
    return MarginResult(
        margin_rub=margin,
        is_promising=margin >= minimum_margin_rub,
    )
