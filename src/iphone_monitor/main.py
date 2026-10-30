"""Long-polling entry point for the Telegram bot."""

import ftplib
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from zoneinfo import ZoneInfo

from .avito_api_source import AvitoApiError, AvitoApiSource
from .avito_browser import BrowserActError
from .backup import backup_database, prune_backups
from .bot import handle_update
from .config import load_settings
from .daily import send_daily_summary_if_due
from .github_publish import GitHubPublishError, publish_snapshot
from .health import write_status
from .instance_lock import InstanceLock
from .json_api_source import ListingsSourceError
from .monitor import Monitor, _send_photo, _send_text
from .runtime import RetrySchedule
from .site_export import build_site_data, upload_site_data, write_site_data
from .source_factory import create_listings_source
from .store import SeenStore
from .telemetry import configure_logging, log_event
from .telegram_api import TelegramApiError, TelegramClient
from .telegram_mtproto import MtprotoTelegram

TELEGRAM_BACKOFF_MAX_SECONDS = 60
TELEGRAM_POLL_TIMEOUT_SECONDS = 5


def _build_telegram(settings):
    """Prefer the MTProto transport when the Bot API is blocked by the provider."""
    if settings.telegram_transport == "mtproto":
        transport = MtprotoTelegram(
            settings.telegram_bot_token,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            session_path=settings.telegram_session_path,
            proxy=settings.telegram_proxy,
        )
        try:
            transport.start()
            log_event("telegram_mtproto_connected")
            return transport
        except TelegramApiError as exc:
            log_event("telegram_mtproto_failed", error=str(exc))
    return TelegramClient(settings.telegram_bot_token, proxy=settings.telegram_proxy)


BACKUP_HOUR = 3
BACKUP_KEEP = 7
WARMUP_ROUNDS = 1
WARMUP_PAUSE_SECONDS = 300


def warm_up_price_history(
    source,
    store,
    *,
    rounds: int = WARMUP_ROUNDS,
    pause_seconds: int = WARMUP_PAUSE_SECONDS,
) -> int:
    """Fill the price table once so early deals are compared against a real market.

    Avito's feed ignores both the search phrase and the page number: every page
    request repeats the same newest listings. So the warmup makes one request
    instead of pretending to walk pages, and the history keeps growing from the
    regular polling loop.
    """
    if store.get_metadata("price_warmup_done") == "1":
        return 0
    total = 0
    for attempt in range(1, rounds + 1):
        if attempt > 1:
            time.sleep(pause_seconds)
        try:
            listings = source.fetch()
        except (AvitoApiError, OSError) as exc:
            log_event("price_warmup_failed", attempt=attempt, error=str(exc))
            continue
        store.record_prices(listings)
        total += len(listings)
    if total:
        store.set_metadata("price_warmup_done", "1")
    log_event("price_warmup_finished", listings=total)
    return total


def maybe_run_backup(store, settings, *, now: datetime | None = None) -> Path | None:
    """Take one SQLite snapshot per day after the night hour, keeping the last few."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(settings.summary_timezone)
    )
    today = local_now.strftime("%Y-%m-%d")
    if store.get_metadata("last_backup_date") == today or local_now.hour < BACKUP_HOUR:
        return None
    database_path = Path(settings.database_path)
    if not database_path.is_file():
        return None
    backup_directory = database_path.parent / "backups"
    path = backup_database(database_path, backup_directory)
    removed = prune_backups(backup_directory, keep=BACKUP_KEEP)
    store.set_metadata("last_backup_date", today)
    log_event("database_backup_created", path=str(path), removed=len(removed))
    return path


SITE_EXPORT_METADATA_KEY = "site_export_at"


def publish_site_data(store, settings, *, now: float | None = None, force: bool = False):
    """Write the dashboard snapshot (and upload it when FTP access is configured)."""
    moment = time.time() if now is None else now
    last_export = store.get_metadata(SITE_EXPORT_METADATA_KEY)
    if (
        not force
        and last_export
        and moment - float(last_export) < settings.site_export_interval_seconds
    ):
        return None
    payload = build_site_data(store, status_path=settings.status_path, now=moment)
    path = write_site_data(settings.site_export_path, payload)
    if settings.github_token and settings.github_repo:
        try:
            result = publish_snapshot(
                token=settings.github_token,
                repo=settings.github_repo,
                path=settings.github_site_path,
                content=path.read_text(encoding="utf-8"),
                message=f"site: snapshot {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
                branch=settings.github_branch,
            )
        except GitHubPublishError as exc:
            log_event("github_publish_failed", error=str(exc))
        else:
            log_event(
                "github_publish_succeeded",
                repo=settings.github_repo,
                path=settings.github_site_path,
                items=len(payload["items"]),
                commit=(result.get("commit") or {}).get("sha", "")[:8],
            )
    if settings.site_ftp_host and settings.site_ftp_user and settings.site_ftp_password:
        upload_site_data(
            path,
            host=settings.site_ftp_host,
            username=settings.site_ftp_user,
            password=settings.site_ftp_password,
            remote_dir=settings.site_ftp_dir,
        )
        log_event("site_data_uploaded", path=str(path), items=len(payload["items"]))
    else:
        log_event("site_data_written", path=str(path), items=len(payload["items"]))
    store.set_metadata(SITE_EXPORT_METADATA_KEY, str(moment))
    return path


def flush_pending_notifications(store, telegram, limit: int = 10) -> int:
    """Deliver notifications that were queued while Telegram was unreachable."""
    delivered = 0
    for notification_id, chat_id, kind, text, photo in store.pending_notifications(limit):
        sent = False
        if kind == "photo" and photo:
            sent = _send_photo(telegram, chat_id, photo, text)
        if not sent:
            sent = _send_text(telegram, chat_id, text)
        if not sent:
            break
        store.delete_notification(notification_id)
        delivered += 1
    return delivered


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_path)
    lock = InstanceLock(Path(settings.database_path).parent / "bot.lock")
    if not lock.acquire():
        log_event("bot_already_running", lock_path=str(lock.path))
        return
    telegram = _build_telegram(settings)
    source = create_listings_source(settings)
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = SeenStore(database_path)
    if settings.telegram_chat_id:
        store.add_subscriber(settings.telegram_chat_id)
    if isinstance(source, AvitoApiSource):
        try:
            warm_up_price_history(source, store)
        except (OSError, sqlite3.Error) as exc:
            log_event("price_warmup_failed", error=str(exc))
    offset = None
    retry_schedule = RetrySchedule(
        base_delay_seconds=settings.source_retry_base_seconds,
        max_delay_seconds=settings.source_retry_max_seconds,
    )
    telegram_failures = 0
    next_telegram_attempt = 0.0
    if source is None:
        log_event("monitoring_disabled")
    log_event("bot_started", monitoring_enabled=source is not None)

    while True:
        if time.time() >= next_telegram_attempt:
            try:
                for update in telegram.get_updates(
                    offset, timeout_seconds=TELEGRAM_POLL_TIMEOUT_SECONDS
                ):
                    handle_update(update, telegram, store=store)
                    offset = update["update_id"] + 1
                telegram_failures = 0
            except TelegramApiError:
                telegram_failures += 1
                delay = min(
                    5 * (2 ** (telegram_failures - 1)),
                    TELEGRAM_BACKOFF_MAX_SECONDS,
                )
                next_telegram_attempt = time.time() + delay
                log_event(
                    "telegram_request_failed",
                    failure_count=telegram_failures,
                    retry_in_seconds=delay,
                )
            except (KeyError, TypeError):
                log_event("telegram_update_rejected")

        if time.time() >= next_telegram_attempt:
            try:
                delivered = flush_pending_notifications(store, telegram)
                if delivered:
                    log_event("pending_notifications_delivered", count=delivered)
            except TelegramApiError:
                log_event("pending_notifications_failed")

        chat_ids = store.list_subscribers()
        if not chat_ids and settings.telegram_chat_id:
            chat_ids = (settings.telegram_chat_id,)

        if chat_ids and time.time() >= next_telegram_attempt:
            try:
                if send_daily_summary_if_due(
                    store,
                    telegram,
                    chat_ids,
                    now=datetime.now(timezone.utc),
                    timezone_name=settings.summary_timezone,
                    summary_time=settings.daily_summary_time,
                ):
                    log_event("daily_summary_sent", recipients=len(chat_ids))
            except TelegramApiError:
                log_event("daily_summary_failed")

        try:
            maybe_run_backup(store, settings)
        except (OSError, sqlite3.Error) as exc:
            log_event("database_backup_failed", error=str(exc))

        now = time.time()
        sent_now = 0
        monitoring_paused = store.get_metadata("monitor_paused") == "1"
        if source is not None and chat_ids and not monitoring_paused and retry_schedule.can_attempt(now):
            try:
                sent = Monitor(
                    source,
                    store,
                    telegram,
                    chat_ids,
                    delivery_rub=settings.delivery_rub,
                    commission_rub=settings.commission_rub,
                    minimum_margin_rub=settings.minimum_margin_rub,
                    notify_min_margin_rub=settings.notify_min_margin_rub,
                    repair_reserve_used_rub=settings.repair_reserve_used_rub,
                    repair_reserve_broken_rub=settings.repair_reserve_broken_rub,
                ).process_once()
                retry_schedule.record_success(now)
                retry_schedule.next_attempt_at = now + settings.poll_interval_seconds
                sent_now = sent
                log_event("monitoring_poll_succeeded", notified_count=sent)
            except (AvitoApiError, BrowserActError, ListingsSourceError, ValueError) as exc:
                retry_schedule.record_failure(now)
                if getattr(exc, "rate_limited", False):
                    retry_schedule.next_attempt_at = now + max(
                        settings.poll_interval_seconds, 300
                    )
                log_event(
                    "monitoring_poll_failed",
                    failure_count=retry_schedule.failure_count,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if retry_schedule.needs_alert(now, settings.source_alert_after_seconds):
                    if getattr(exc, "blocked", False):
                        alert_text = (
                            "⚠️ Avito блокирует IP-адрес, с которого работает бот. "
                            "Выключите VPN или включите российский прокси — "
                            "бот сам продолжит проверку."
                        )
                    else:
                        alert_text = (
                            "⚠️ Мониторинг объявлений временно недоступен. "
                            "Бот продолжит пробовать автоматически."
                        )
                    for chat_id in chat_ids:
                        try:
                            telegram.send_message(chat_id, alert_text)
                        except TelegramApiError:
                            log_event("alert_delivery_failed")
                    retry_schedule.mark_alert_sent()

        write_status(
            settings.status_path,
            heartbeat_at=time.time(),
            source_failure_count=retry_schedule.failure_count,
            monitoring_enabled=source is not None,
        )

        try:
            publish_site_data(store, settings, force=sent_now > 0)
        except (OSError, ValueError, ftplib.all_errors) as exc:
            log_event("site_data_publish_failed", error=str(exc))


if __name__ == "__main__":
    run()
