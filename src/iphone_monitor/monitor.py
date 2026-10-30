"""One polling cycle: deduplicate, analyze and notify new listings."""

from datetime import datetime, timedelta, timezone
from typing import Sequence

from .analysis import analyze_listing
from .avito_parser import ParsedListing
from .formatter import ListingView, format_listing_message
from .telegram_api import TelegramApiError
from .telemetry import log_event


def _send_photo(telegram, chat_id: str, photo: str, caption: str) -> bool:
    """Send the picture; a broken image link must not lose the listing."""
    try:
        telegram.send_photo(chat_id, photo, caption)
        return True
    except TelegramApiError:
        return False


def _send_text(telegram, chat_id: str, text: str) -> bool:
    try:
        telegram.send_message(chat_id, text)
        return True
    except TelegramApiError:
        return False


class Monitor:
    def __init__(
        self,
        source,
        store,
        telegram,
        chat_ids: str | Sequence[str],
        *,
        delivery_rub: int = 400,
        commission_rub: int = 0,
        minimum_margin_rub: int = 500,
        notify_min_margin_rub: int = 0,
        repair_reserve_used_rub: int = 1_500,
        repair_reserve_broken_rub: int = 10_000,
        duplicate_window_hours: int = 24,
        weak_deal_margin_rub: int = 1_500,
    ):
        self.source = source
        self.store = store
        self.telegram = telegram
        self.chat_ids = (chat_ids,) if isinstance(chat_ids, str) else tuple(chat_ids)
        self.delivery_rub = delivery_rub
        self.commission_rub = commission_rub
        self.minimum_margin_rub = minimum_margin_rub
        self.notify_min_margin_rub = notify_min_margin_rub
        self.repair_reserve_used_rub = repair_reserve_used_rub
        self.repair_reserve_broken_rub = repair_reserve_broken_rub
        self.duplicate_window_hours = duplicate_window_hours
        self.weak_deal_margin_rub = weak_deal_margin_rub

    @staticmethod
    def _batch_prices(listing: ParsedListing, listings: tuple[ParsedListing, ...]) -> list[int]:
        return [
            other.price_rub
            for other in listings
            if other.item_id != listing.item_id
            and other.model == listing.model
            and other.storage_gb == listing.storage_gb
            and other.condition == listing.condition
        ]

    def _comparable_prices(
        self, listing: ParsedListing, listings: tuple[ParsedListing, ...]
    ) -> list[int]:
        """Prefer the price history; fall back to the current page when it is thin."""
        history = self.store.comparable_prices(
            listing.model,
            listing.storage_gb,
            listing.condition,
            exclude_item_id=listing.item_id,
        )
        if len(history) >= 3:
            return list(history)
        return sorted({*history, *self._batch_prices(listing, listings)})

    def _send(self, listing: ParsedListing, message: str) -> None:
        photo = listing.images[0] if listing.images and len(message) <= 1_024 else None
        for chat_id in self.chat_ids:
            if photo and _send_photo(self.telegram, chat_id, photo, message):
                continue
            if _send_text(self.telegram, chat_id, message):
                continue
            # Telegram is unreachable: keep the notification for a later retry
            # instead of losing the listing forever.
            self.store.enqueue_notification(
                chat_id, "photo" if photo else "message", message, photo
            )

    def process_once(self) -> int:
        listings = tuple(self.source.fetch())
        self.store.record_prices(listings)
        if self.store.is_empty():
            self.store.mark_seen_many([listing.item_id for listing in listings])
            return 0

        sent = 0
        fresh = 0
        skipped_low_margin = 0
        best_margin = None
        for listing in listings:
            if self.store.has_seen(listing.item_id):
                continue
            fresh += 1
            analysis = analyze_listing(
                listing,
                self._comparable_prices(listing, listings),
                delivery_rub=self.delivery_rub,
                commission_rub=self.commission_rub,
                minimum_margin_rub=self.minimum_margin_rub,
                repair_reserve_used_rub=self.repair_reserve_used_rub,
                repair_reserve_broken_rub=self.repair_reserve_broken_rub,
            )
            if analysis.margin_rub is not None:
                if best_margin is None or analysis.margin_rub > best_margin:
                    best_margin = analysis.margin_rub
            if analysis.margin_rub is None or analysis.margin_rub < self.notify_min_margin_rub:
                skipped_low_margin += 1
                self.store.mark_seen(listing.item_id)
                continue
            duplicate_cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=self.duplicate_window_hours)
            ).isoformat()
            if self.store.count_similar_notified(
                model=listing.model,
                storage_gb=listing.storage_gb,
                price_rub=listing.price_rub,
                since=duplicate_cutoff,
            ):
                # A seller lists the same phone several times under different ids.
                self.store.mark_seen(listing.item_id)
                log_event(
                    "listing_duplicate_skipped",
                    item_id=listing.item_id,
                    title=listing.title,
                    price_rub=listing.price_rub,
                )
                continue
            if (
                analysis.market_price_rub
                and listing.price_rub < analysis.market_price_rub * 0.5
            ):
                # A phone at half the market price is a scam, a parts lot or a typo.
                self.store.mark_seen(listing.item_id)
                log_event(
                    "listing_suspicious_price",
                    item_id=listing.item_id,
                    price_rub=listing.price_rub,
                    market_price_rub=analysis.market_price_rub,
                )
                continue
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
                weak_threshold_rub=self.weak_deal_margin_rub,
            )
            self._send(listing, format_listing_message(view))
            self.store.mark_seen(listing.item_id)
            self.store.record_notified(
                item_id=listing.item_id,
                title=listing.title,
                price_rub=listing.price_rub,
                market_price_rub=analysis.market_price_rub,
                margin_rub=analysis.margin_rub,
                url=listing.url,
                notified_at=datetime.now(timezone.utc).isoformat(),
                model=listing.model,
                storage_gb=listing.storage_gb,
            )
            sent += 1
        log_event(
            "monitoring_cycle_stats",
            fetched=len(listings),
            new=fresh,
            skipped_low_margin=skipped_low_margin,
            notified=sent,
            best_margin_rub=best_margin,
        )
        return sent
