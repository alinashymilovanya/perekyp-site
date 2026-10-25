"""One polling cycle: deduplicate, analyze and notify new listings."""

from datetime import datetime, timezone

from .analysis import analyze_listing
from .avito_parser import ParsedListing
from .formatter import ListingView, format_listing_message


class Monitor:
    def __init__(
        self,
        source,
        store,
        telegram,
        chat_id: str,
        *,
        delivery_rub: int = 400,
        commission_rub: int = 0,
        minimum_margin_rub: int = 500,
    ):
        self.source = source
        self.store = store
        self.telegram = telegram
        self.chat_id = chat_id
        self.delivery_rub = delivery_rub
        self.commission_rub = commission_rub
        self.minimum_margin_rub = minimum_margin_rub

    @staticmethod
    def _comparable_prices(listing: ParsedListing, listings: tuple[ParsedListing, ...]) -> list[int]:
        return [
            other.price_rub
            for other in listings
            if other.item_id != listing.item_id
            and other.model == listing.model
            and other.storage_gb == listing.storage_gb
            and other.condition == listing.condition
        ]

    def process_once(self) -> int:
        listings = tuple(self.source.fetch())
        if self.store.is_empty():
            for listing in listings:
                self.store.mark_seen(listing.item_id)
            return 0

        sent = 0
        for listing in listings:
            if self.store.has_seen(listing.item_id):
                continue
            analysis = analyze_listing(
                listing,
                self._comparable_prices(listing, listings),
                delivery_rub=self.delivery_rub,
                commission_rub=self.commission_rub,
                minimum_margin_rub=self.minimum_margin_rub,
            )
            view = ListingView(
                title=listing.title,
                price_rub=listing.price_rub,
                market_price_rub=analysis.market_price_rub,
                margin_rub=analysis.margin_rub,
                url=listing.url,
                condition=listing.condition or "не указано",
                battery=(
                    f"{listing.battery_percent}%"
                    if listing.battery_percent is not None
                    else "не указана"
                ),
                positives=analysis.positives,
                negatives=analysis.negatives,
            )
            message = format_listing_message(view)
            if listing.images:
                self.telegram.send_photo(self.chat_id, listing.images[0], message[:1000])
            self.telegram.send_message(self.chat_id, message)
            self.store.mark_seen(listing.item_id)
            self.store.record_notified(
                item_id=listing.item_id,
                title=listing.title,
                price_rub=listing.price_rub,
                market_price_rub=analysis.market_price_rub,
                margin_rub=analysis.margin_rub,
                url=listing.url,
                notified_at=datetime.now(timezone.utc).isoformat(),
            )
            sent += 1
        return sent
