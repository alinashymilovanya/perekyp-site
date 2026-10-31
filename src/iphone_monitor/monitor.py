"""One polling cycle: deduplicate, analyze and notify new listings."""

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Sequence

from . import plans
from .analysis import analyze_listing
from .avito_parser import ParsedListing
from .formatter import ListingView, format_detail_addendum, format_listing_message
from .item_detail import looks_like_block_page
from .telegram_api import TelegramApiError
from .telemetry import describe_exception, log_event


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
        enricher=None,
        cycle_id: str | None = None,
        default_plan: str = "pro",
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
        # Reads the listing page for condition, battery and description.
        # Only promising listings are read: each page costs one request.
        self.enricher = enricher
        # Ties every record of this cycle together in the admin log.
        self.cycle_id = cycle_id
        # Тариф, который получает чат без аккаунта: действующие подписчики
        # не должны внезапно оказаться на Free после обновления кода.
        self.default_plan = default_plan

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

    def _analyze(self, listing: ParsedListing, prices: Sequence[int]):
        return analyze_listing(
            listing,
            list(prices),
            delivery_rub=self.delivery_rub,
            commission_rub=self.commission_rub,
            minimum_margin_rub=self.minimum_margin_rub,
            repair_reserve_used_rub=self.repair_reserve_used_rub,
            repair_reserve_broken_rub=self.repair_reserve_broken_rub,
        )

    def _enrich(self, listing: ParsedListing) -> ParsedListing:
        """Ask the listing page for the facts the search feed does not carry."""
        if self.enricher is None:
            return listing
        try:
            return self.enricher(listing)
        except Exception as error:  # a page must never kill the polling cycle
            log_event(
                "detail_enrich_failed",
                status="failed",
                component="detail",
                cycle_id=self.cycle_id,
                listing_id=listing.item_id,
                error=describe_exception(error),
            )
            return listing

    def _send(self, listing: ParsedListing, message: str) -> dict[str, int]:
        """Deliver one signal according to the person's tariff.

        Free has three signals a day and a delay of up to thirty minutes, Pro
        and above are unlimited and fast. The limit is enforced here, on the
        server, and not in the cabinet: otherwise it is a drawing, not a
        product.
        """
        photo = listing.images[0] if listing.images and len(message) <= 1_024 else None
        sent = 0
        queued = 0
        delayed = 0
        suppressed = 0
        plan_code = ""
        plan_limit: int | None = None
        plan_used = 0
        for chat_id in self.chat_ids:
            account = self.store.ensure_account(chat_id, self.default_plan)
            current = plans.effective_plan(account.plan, account.plan_until)
            today = plans.day_key()
            used = self.store.signals_used(chat_id, today)
            room = plans.quota(current.code, used)
            if room.exhausted:
                suppressed += 1
                plan_code, plan_limit, plan_used = current.code, room.limit, used
                log_event(
                    "notification_suppressed",
                    status="skipped",
                    component="monitor",
                    cycle_id=self.cycle_id,
                    listing_id=listing.item_id,
                    reason="limit_reached",
                    plan=current.code,
                    limit=room.limit,
                    used=used,
                    chat_id=chat_id,
                )
                continue
            used_after = self.store.consume_signal(chat_id, today)
            if current.queue_seconds > 0:
                # Free does not lose the signal: it waits in the outbox until
                # its delivery moment instead of interrupting the hot minute.
                deliver_at = plans.iso_in(plans.now_utc(), current.queue_seconds)
                self.store.enqueue_notification(
                    chat_id,
                    "photo" if photo else "message",
                    message,
                    photo,
                    deliver_at=deliver_at,
                )
                delayed += 1
                log_event(
                    "notification_delayed",
                    status="queued",
                    component="monitor",
                    cycle_id=self.cycle_id,
                    listing_id=listing.item_id,
                    plan=current.code,
                    delay_seconds=current.delay_seconds,
                    deliver_at=deliver_at,
                    used=used_after,
                    chat_id=chat_id,
                )
                continue
            if photo and _send_photo(self.telegram, chat_id, photo, message):
                sent += 1
                continue
            if _send_text(self.telegram, chat_id, message):
                sent += 1
                continue
            # Telegram is unreachable: keep the notification for a later retry
            # instead of losing the listing forever.
            self.store.enqueue_notification(
                chat_id, "photo" if photo else "message", message, photo
            )
            queued += 1
            log_event(
                "telegram_delivery_queued",
                status="queued",
                component="telegram",
                cycle_id=self.cycle_id,
                listing_id=listing.item_id,
            )
        if sent:
            log_event(
                "telegram_delivery_sent",
                status="success",
                component="telegram",
                cycle_id=self.cycle_id,
                listing_id=listing.item_id,
                messages=sent,
                with_photo=bool(photo),
            )
        return {
            "sent": sent,
            "queued": queued,
            "delayed": delayed,
            "suppressed": suppressed,
            "plan": plan_code,
            "limit": plan_limit,
            "used": plan_used,
        }

    def _log_listing(self, event: str, listing: ParsedListing, status: str, **details) -> None:
        log_event(
            event,
            status=status,
            component="monitor",
            cycle_id=self.cycle_id,
            listing_id=listing.item_id,
            **details,
        )

    def send_detail_addendums(self, limit: int = 3) -> int:
        """Deliver page facts that arrived after their listing was notified."""
        sent = 0
        for item_id, url, title in self.store.detail_requests(limit):
            detail = self.store.load_item_detail(item_id)
            if detail is None:
                # The home agent has not read this page yet: keep the request.
                continue
            if looks_like_block_page(detail.title, detail.description):
                # A cached answer from the IP-check page is not data: wait for
                # a real card instead of sending the block text to people.
                log_event(
                    "detail_addendum_skipped_block",
                    status="skipped",
                    component="detail",
                    cycle_id=self.cycle_id,
                    listing_id=item_id,
                )
                continue
            extras = []
            if detail.color:
                extras.append(f"Цвет: {detail.color}")
            if detail.sim:
                extras.append(f"SIM: {detail.sim}")
            message = format_detail_addendum(
                title=detail.title or title or item_id,
                url=url,
                condition=detail.condition,
                battery_percent=detail.battery_percent,
                description=detail.description,
                extras=tuple(extras),
                location=detail.address,
                published_at=detail.published_at,
            )
            for chat_id in self.chat_ids:
                if _send_text(self.telegram, chat_id, message):
                    continue
                self.store.enqueue_notification(chat_id, "message", message, None)
            self.store.drop_detail_request(item_id)
            sent += 1
            log_event(
                "detail_addendum_sent",
                status="success",
                component="telegram",
                cycle_id=self.cycle_id,
                listing_id=item_id,
                condition=detail.condition,
                battery=detail.battery_percent,
            )
        return sent

    def process_once(self) -> int:
        started_at = time.monotonic()
        log_event("cycle_started", status="started", component="monitor", cycle_id=self.cycle_id)
        listings = tuple(self.source.fetch())
        self.store.record_prices(listings)
        if self.store.is_empty():
            self.store.mark_seen_many([listing.item_id for listing in listings])
            log_event(
                "cycle_finished",
                status="success",
                component="monitor",
                cycle_id=self.cycle_id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                fetched=len(listings),
                seeded=len(listings),
                notified=0,
                reason="first_run_history_seeded",
            )
            return 0

        sent = 0
        fresh = 0
        skipped_low_margin = 0
        skipped_after_detail = 0
        skipped_duplicate = 0
        skipped_suspicious = 0
        skipped_limit = 0
        enriched_count = 0
        best_margin = None
        start_cycle = getattr(self.enricher, "start_cycle", None)
        if callable(start_cycle):
            start_cycle()
        for listing in listings:
            if self.store.has_seen(listing.item_id):
                continue
            fresh += 1
            prices = self._comparable_prices(listing, listings)
            analysis = self._analyze(listing, prices)
            if analysis.margin_rub is None or analysis.margin_rub < self.notify_min_margin_rub:
                skipped_low_margin += 1
                self.store.mark_seen(listing.item_id)
                self._log_listing(
                    "listing_skipped",
                    listing,
                    "skipped",
                    reason="low_margin",
                    price_rub=listing.price_rub,
                    market_price_rub=analysis.market_price_rub,
                    margin_rub=analysis.margin_rub,
                    min_margin_rub=self.notify_min_margin_rub,
                )
                continue
            enriched = self._enrich(listing)
            if enriched is not listing:
                enriched_count += 1
                listing = enriched
                # The page corrects the condition, not the market: keep the
                # comparable prices collected for it before the request.
                analysis = self._analyze(listing, prices)
                if (
                    analysis.margin_rub is None
                    or analysis.margin_rub < self.notify_min_margin_rub
                ):
                    skipped_after_detail += 1
                    self.store.mark_seen(listing.item_id)
                    self._log_listing(
                        "listing_skipped",
                        listing,
                        "skipped",
                        reason="after_detail",
                        price_rub=listing.price_rub,
                        margin_rub=analysis.margin_rub,
                        market_price_rub=analysis.market_price_rub,
                        condition=listing.condition,
                        min_margin_rub=self.notify_min_margin_rub,
                    )
                    continue
            if analysis.margin_rub is not None:
                if best_margin is None or analysis.margin_rub > best_margin:
                    best_margin = analysis.margin_rub
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
                skipped_duplicate += 1
                log_event(
                    "listing_stored",
                    status="skipped",
                    component="store",
                    cycle_id=self.cycle_id,
                    listing_id=listing.item_id,
                    result="duplicate",
                    price_rub=listing.price_rub,
                    duplicate_window_hours=self.duplicate_window_hours,
                )
                self._log_listing(
                    "listing_skipped",
                    listing,
                    "skipped",
                    reason="duplicate",
                    price_rub=listing.price_rub,
                    margin_rub=analysis.margin_rub,
                )
                continue
            if (
                analysis.market_price_rub
                and listing.price_rub < analysis.market_price_rub * 0.5
            ):
                # A phone at half the market price is a scam, a parts lot or a typo.
                self.store.mark_seen(listing.item_id)
                skipped_suspicious += 1
                self._log_listing(
                    "listing_skipped",
                    listing,
                    "skipped",
                    reason="suspicious_price",
                    price_rub=listing.price_rub,
                    market_price_rub=analysis.market_price_rub,
                    margin_rub=analysis.margin_rub,
                )
                continue
            detail_for = getattr(self.enricher, "detail_for", None)
            detail = detail_for(listing.item_id) if callable(detail_for) else None
            extras = []
            if detail is not None:
                if detail.color:
                    extras.append(f"Цвет: {detail.color}")
                if detail.sim:
                    extras.append(f"SIM: {detail.sim}")
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
                description=listing.description if detail is not None else "",
                location=listing.location,
                seller=detail.seller_name if detail is not None else "",
                published_at=detail.published_at if detail is not None else "",
                extras=tuple(extras),
            )
            delivery = self._send(listing, format_listing_message(view))
            if not delivery["sent"] and not delivery["delayed"] and not delivery["queued"]:
                # The daily limit of the tariff is used up: the signal is not
                # an error, it is simply not this person's today.
                self.store.mark_seen(listing.item_id)
                skipped_limit += 1
                self._log_listing(
                    "listing_skipped",
                    listing,
                    "skipped",
                    reason="limit_reached",
                    plan=delivery.get("plan"),
                    limit=delivery.get("limit"),
                    used=delivery.get("used"),
                    price_rub=listing.price_rub,
                    market_price_rub=analysis.market_price_rub,
                    margin_rub=analysis.margin_rub,
                )
                continue
            stored_status = "success"
            stored_error = ""
            try:
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
            except sqlite3.Error as error:
                stored_status = "failed"
                stored_error = describe_exception(error)
            log_event(
                "listing_stored",
                status=stored_status,
                component="store",
                cycle_id=self.cycle_id,
                listing_id=listing.item_id,
                result="added" if stored_status == "success" else "error",
                price_rub=listing.price_rub,
                **({"error": stored_error} if stored_error else {}),
            )
            log_event(
                "listing_notified",
                status="success" if delivery["sent"] else "queued",
                component="telegram",
                cycle_id=self.cycle_id,
                listing_id=listing.item_id,
                price_rub=listing.price_rub,
                market_price_rub=analysis.market_price_rub,
                margin_rub=analysis.margin_rub,
                condition=listing.condition,
                chats=len(self.chat_ids),
                sent=delivery["sent"],
                queued=delivery["queued"],
                delayed=delivery["delayed"],
            )
            sent += 1
        log_event(
            "cycle_finished",
            status="success",
            component="monitor",
            cycle_id=self.cycle_id,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            fetched=len(listings),
            new=fresh,
            skipped_low_margin=skipped_low_margin,
            skipped_after_detail=skipped_after_detail,
            skipped_duplicate=skipped_duplicate,
            skipped_suspicious=skipped_suspicious,
            skipped_limit=skipped_limit,
            enriched=enriched_count,
            notified=sent,
            best_margin_rub=best_margin,
        )
        # Keep the last cycle visible from outside the container: the site
        # snapshot is the only channel we can read from a datacenter deploy.
        self.store.set_metadata(
            "last_cycle",
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "cycle_id": self.cycle_id,
                    "fetched": len(listings),
                    "new": fresh,
                    "skipped_low_margin": skipped_low_margin,
                    "skipped_after_detail": skipped_after_detail,
                    "skipped_duplicate": skipped_duplicate,
                    "skipped_suspicious": skipped_suspicious,
                    "skipped_limit": skipped_limit,
                    "enriched": enriched_count,
                    "notified": sent,
                    "best_margin_rub": best_margin,
                },
                ensure_ascii=False,
            ),
        )
        return sent
