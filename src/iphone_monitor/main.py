"""Long-polling entry point for the Telegram bot."""

import ftplib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from zoneinfo import ZoneInfo

from .avito_api_source import AvitoApiError, AvitoApiSource
from .avito_browser import BrowserActError
from .backup import backup_database, prune_backups
from .bot import handle_update
from .config import load_settings
from .daily import send_daily_summary_if_due
from .github_publish import (
    GitHubPublishError,
    fetch_deploy_run,
    publish_snapshot,
)
from .health import write_status
from .instance_lock import InstanceLock
from .detail_relay import DetailRelay, RemoteEnricher
from .item_detail import DetailEnricher, ItemDetailError, run_probe_once
from .json_api_source import ListingsSourceError
from .logs_api import start_admin_logs_api
from .monitor import Monitor, _send_photo, _send_text
from .runtime import RetrySchedule
from .site_export import build_site_data, upload_site_data, write_site_data
from .source_factory import create_listings_source
from .store import SeenStore
from .telemetry import configure_logging, describe_exception, log_event
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
            log_event("telegram_ready", status="success", component="telegram", transport="mtproto")
            return transport
        except TelegramApiError as exc:
            log_event(
                "telegram_connect_failed",
                status="failed",
                component="telegram",
                transport="mtproto",
                error=describe_exception(exc),
            )
    client = TelegramClient(settings.telegram_bot_token, proxy=settings.telegram_proxy)
    log_event("telegram_ready", status="success", component="telegram", transport="bot-api")
    return client


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
            log_event(
                "price_warmup_failed",
                status="failed",
                component="market",
                attempt=attempt,
                error=describe_exception(exc),
            )
            continue
        store.record_prices(listings)
        total += len(listings)
    if total:
        store.set_metadata("price_warmup_done", "1")
    log_event("price_warmup_finished", status="success", component="market", listings=total)
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
    log_event(
        "database_backup_created",
        status="success",
        component="store",
        path=str(path),
        removed=len(removed),
    )
    return path


SITE_EXPORT_METADATA_KEY = "site_export_at"
SITE_PUBLICATION_METADATA_KEY = "site_publication"
DEPLOY_CHECK_ENABLED = ("1", "true", "yes", "on")


def _deploy_check_enabled() -> bool:
    return os.environ.get("GITHUB_DEPLOY_CHECK", "").strip().lower() in DEPLOY_CHECK_ENABLED


def _record_publication(store, publication: dict) -> None:
    """Keep the publication stage readable by the dashboard and the admin log."""
    store.set_metadata(
        SITE_PUBLICATION_METADATA_KEY,
        json.dumps(publication, ensure_ascii=False),
    )


def publish_site_data(store, settings, *, now: float | None = None, force: bool = False):
    """Write the dashboard snapshot (and upload it when FTP access is configured).

    Three different things happen here and they must never be confused:
    the snapshot exists locally, the snapshot was handed to GitHub/FTP, and the
    site itself was actually deployed. Only the FTP upload writes the live
    directory directly; a GitHub commit merely triggers the deployment workflow.
    """
    moment = time.time() if now is None else now
    last_export = store.get_metadata(SITE_EXPORT_METADATA_KEY)
    if (
        not force
        and last_export
        and moment - float(last_export) < settings.site_export_interval_seconds
    ):
        return None
    payload = build_site_data(store, status_path=settings.status_path, now=moment)
    items = len(payload["items"])
    path = write_site_data(settings.site_export_path, payload)
    publication = {
        "at": datetime.now(timezone.utc).isoformat(),
        "path": str(path),
        "items": items,
        "stage": "snapshot",
        "method": "local",
        "deploy_confirmed": False,
        "commit": None,
        "detail": "снимок записан локально",
    }
    log_event(
        "site_snapshot_written",
        status="success",
        component="publish",
        path=str(path),
        items=items,
    )
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
            publication.update(
                stage="snapshot",
                method="github",
                detail=describe_exception(exc),
            )
            log_event(
                "site_snapshot_publish_failed",
                status="failed",
                component="publish",
                method="github",
                repo=settings.github_repo,
                error=describe_exception(exc),
            )
        else:
            commit = (result.get("commit") or {}).get("sha", "")
            publication.update(
                stage="awaiting_deploy",
                method="github",
                commit=commit[:8] or None,
                detail="коммит создан, деплой сайта не подтверждён",
            )
            log_event(
                "site_snapshot_committed",
                status="success",
                component="publish",
                repo=settings.github_repo,
                path=settings.github_site_path,
                items=items,
                commit=commit[:8],
                deploy_confirmed=False,
                stage="awaiting_deploy",
            )
            if commit and _deploy_check_enabled():
                _confirm_deploy(store, settings, publication, commit)
    if settings.site_ftp_host and settings.site_ftp_user and settings.site_ftp_password:
        try:
            upload_site_data(
                path,
                host=settings.site_ftp_host,
                username=settings.site_ftp_user,
                password=settings.site_ftp_password,
                remote_dir=settings.site_ftp_dir,
            )
        except (ValueError, *ftplib.all_errors) as exc:
            publication.update(
                stage="awaiting_upload",
                method="ftp",
                deploy_confirmed=False,
                detail=describe_exception(exc),
            )
            log_event(
                "site_snapshot_upload_failed",
                status="failed",
                component="publish",
                method="ftp",
                host=settings.site_ftp_host,
                error=describe_exception(exc),
            )
            raise
        publication.update(
            stage="published",
            method="ftp",
            deploy_confirmed=True,
            detail="снимок выгружен в каталог сайта по FTP",
        )
        log_event(
            "site_snapshot_uploaded",
            status="success",
            component="publish",
            method="ftp",
            host=settings.site_ftp_host,
            path=str(path),
            items=items,
            deploy_confirmed=True,
        )
    elif publication["method"] == "local" and not settings.github_token:
        publication.update(
            stage="local_only",
            detail="снимок только на диске: ни GitHub, ни FTP не настроены",
        )
        log_event(
            "site_snapshot_local_only",
            status="skipped",
            component="publish",
            path=str(path),
            items=items,
        )
    _record_publication(store, publication)
    store.set_metadata(SITE_EXPORT_METADATA_KEY, str(moment))
    log_event(
        "site_publication_state",
        status="success" if publication["deploy_confirmed"] else "queued",
        component="publish",
        stage=publication["stage"],
        method=publication["method"],
        deploy_confirmed=publication["deploy_confirmed"],
        commit=publication["commit"],
        items=items,
    )
    return publication


def _confirm_deploy(store, settings, publication: dict, commit: str) -> None:
    """Ask GitHub whether the deployment workflow actually succeeded."""
    try:
        state = fetch_deploy_run(
            token=settings.github_token,
            repo=settings.github_repo,
            branch=settings.github_branch,
            head_sha=commit,
        )
    except GitHubPublishError as exc:
        log_event(
            "site_deploy_status_unknown",
            status="failed",
            component="publish",
            commit=commit[:8],
            error=describe_exception(exc),
        )
        return
    if state["state"] == "success":
        publication.update(
            stage="published",
            deploy_confirmed=True,
            detail="деплой подтверждён GitHub Actions",
            run_number=state["run_number"],
            run_url=state["url"],
        )
        log_event(
            "site_deploy_confirmed",
            status="success",
            component="publish",
            commit=commit[:8],
            run_number=state["run_number"],
            run_url=state["url"],
        )
        return
    publication.update(
        stage="awaiting_deploy",
        deploy_confirmed=False,
        detail=f"деплой не подтверждён: {state['state']}",
        run_number=state["run_number"],
        run_url=state["url"],
    )
    log_event(
        "site_deploy_pending",
        status="retried" if state["state"] == "failed" else "queued",
        component="publish",
        commit=commit[:8],
        deploy_state=state["state"],
        conclusion=state["conclusion"],
        run_number=state["run_number"],
        run_url=state["url"],
    )


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


def apply_seed_state(store, settings) -> dict | None:
    """Merge the carried-over state once, when the bot starts on a new machine."""
    if not getattr(settings, "seed_state_path", ""):
        return None
    if store.get_metadata("seed_state_applied") == "1":
        return None
    path = Path(settings.seed_state_path)
    if not path.is_file():
        log_event(
            "seed_state_missing",
            status="skipped",
            component="store",
            path=str(path),
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log_event(
            "seed_state_failed",
            status="failed",
            component="store",
            error=describe_exception(exc),
        )
        return None
    try:
        counts = store.import_state(payload)
    except (sqlite3.Error, KeyError, ValueError) as exc:
        log_event(
            "seed_state_failed",
            status="failed",
            component="store",
            error=describe_exception(exc),
        )
        return None
    store.set_metadata("seed_state_applied", "1")
    log_event("seed_state_applied", status="success", component="store", **counts)
    return counts


def new_cycle_id(moment: float | None = None) -> str:
    """Sortable identifier that ties every record of one poll cycle together."""
    stamp = datetime.fromtimestamp(
        time.time() if moment is None else moment, timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_path)
    # The log viewer is optional: without ADMIN_LOGS_PASSWORD it simply refuses
    # every request, and without ADMIN_LOGS_ENABLED no port is opened at all.
    start_admin_logs_api()
    lock = InstanceLock(Path(settings.database_path).parent / "bot.lock")
    if not lock.acquire():
        log_event(
            "bot_already_running",
            status="skipped",
            component="bot",
            lock_path=str(lock.path),
        )
        return
    telegram = _build_telegram(settings)
    source = create_listings_source(settings)
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = SeenStore(database_path)
    if settings.telegram_chat_id:
        store.add_subscriber(settings.telegram_chat_id)
    apply_seed_state(store, settings)
    relay = None
    enricher = None
    relay_ready = (
        settings.detail_enabled
        and settings.detail_relay_enabled
        and bool(settings.github_token)
        and "/" in settings.github_repo
    )
    if relay_ready:
        # Avito blocks pages for datacenter addresses, so the home PC does the
        # reading: the bot only publishes the ids it wants and waits briefly.
        relay = DetailRelay(
            store,
            token=settings.github_token,
            repo=settings.github_repo,
            branch=settings.github_branch,
        )
        enricher = RemoteEnricher(
            relay,
            store,
            wait_seconds=settings.detail_wait_seconds,
            poll_seconds=settings.detail_relay_poll_seconds,
        )
        log_event(
            "detail_relay_ready",
            status="success",
            component="detail",
            repo=settings.github_repo,
            wait_seconds=settings.detail_wait_seconds,
            cached_details=store.count_item_details(),
            pending_requests=store.count_detail_requests(),
        )
    elif settings.detail_enabled:
        enricher = DetailEnricher(
            store,
            max_per_cycle=settings.detail_max_per_cycle,
            pause_seconds=settings.detail_pause_seconds,
            cooldown_minutes=settings.detail_cooldown_minutes,
        )
        log_event(
            "detail_enricher_ready",
            status="success",
            component="detail",
            max_per_cycle=settings.detail_max_per_cycle,
            cached_details=store.count_item_details(),
        )
    if isinstance(source, AvitoApiSource):
        try:
            probe_result = run_probe_once(store, source)
            if probe_result is not None:
                log_event(
                    "detail_probe_finished",
                    status="success",
                    component="detail",
                    ok=probe_result.get("ok"),
                    cold_ok=not (probe_result.get("cold") or {}).get("error"),
                )
                publish_site_data(store, settings, force=True)
        except (AvitoApiError, ItemDetailError, OSError, sqlite3.Error) as exc:
            log_event(
                "detail_probe_failed",
                status="failed",
                component="detail",
                error=describe_exception(exc),
            )
        try:
            warm_up_price_history(source, store)
        except (OSError, sqlite3.Error) as exc:
            log_event(
                "price_warmup_failed",
                status="failed",
                component="market",
                error=describe_exception(exc),
            )
    offset = None
    retry_schedule = RetrySchedule(
        base_delay_seconds=settings.source_retry_base_seconds,
        max_delay_seconds=settings.source_retry_max_seconds,
    )
    telegram_failures = 0
    next_telegram_attempt = 0.0
    if source is None:
        log_event(
            "monitoring_disabled",
            status="skipped",
            component="monitor",
            reason="no_listings_source",
        )
    else:
        log_event(
            "source_ready",
            status="success",
            component="source",
            source=type(source).__name__,
            max_listings=settings.max_listings_per_poll,
            interval_seconds=settings.poll_interval_seconds,
        )
    log_event(
        "bot_started",
        status="started",
        component="bot",
        monitoring_enabled=source is not None,
        transport=settings.telegram_transport,
        subscribers=len(store.list_subscribers()) or int(bool(settings.telegram_chat_id)),
    )

    stop_reason = "exited"
    try:
        while True:
            if time.time() >= next_telegram_attempt:
                try:
                    for update in telegram.get_updates(
                        offset, timeout_seconds=TELEGRAM_POLL_TIMEOUT_SECONDS
                    ):
                        handle_update(
                            update,
                            telegram,
                            store=store,
                            publish=(
                                lambda: publish_site_data(
                                    store, settings, force=True
                                )
                            ),
                        )
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
                        status="retried",
                        component="telegram",
                        failure_count=telegram_failures,
                        retry_in_seconds=delay,
                    )
                except (KeyError, TypeError):
                    log_event(
                        "telegram_update_rejected",
                        status="skipped",
                        component="telegram",
                        reason="unexpected_update_shape",
                    )

            if time.time() >= next_telegram_attempt:
                try:
                    delivered = flush_pending_notifications(store, telegram)
                    if delivered:
                        log_event(
                            "pending_notifications_delivered",
                            status="success",
                            component="telegram",
                            count=delivered,
                        )
                except TelegramApiError:
                    log_event(
                        "pending_notifications_failed",
                        status="failed",
                        component="telegram",
                    )

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
                        log_event(
                            "daily_summary_sent",
                            status="success",
                            component="telegram",
                            recipients=len(chat_ids),
                        )
                except TelegramApiError:
                    log_event(
                        "daily_summary_failed",
                        status="failed",
                        component="telegram",
                    )

            try:
                maybe_run_backup(store, settings)
            except (OSError, sqlite3.Error) as exc:
                log_event(
                    "database_backup_failed",
                    status="failed",
                    component="store",
                    error=describe_exception(exc),
                )

            now = time.time()
            sent_now = 0
            monitoring_paused = store.get_metadata("monitor_paused") == "1"
            if (
                source is not None
                and chat_ids
                and not monitoring_paused
                and retry_schedule.can_attempt(now)
            ):
                cycle_id = new_cycle_id(now)
                try:
                    monitor = Monitor(
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
                        weak_deal_margin_rub=settings.weak_deal_margin_rub,
                        enricher=enricher,
                        cycle_id=cycle_id,
                    )
                    if relay is not None:
                        try:
                            relay.sync()
                        except (OSError, ValueError) as exc:
                            log_event(
                                "detail_relay_sync_failed",
                                status="failed",
                                component="detail",
                                cycle_id=cycle_id,
                                error=describe_exception(exc),
                            )
                        addendums = monitor.send_detail_addendums(limit=2)
                        if addendums:
                            log_event(
                                "detail_addendums_sent",
                                status="success",
                                component="telegram",
                                cycle_id=cycle_id,
                                count=addendums,
                            )
                    sent = monitor.process_once()
                    if relay is not None:
                        relay.publish_requests()
                    retry_schedule.record_success(now)
                    retry_schedule.next_attempt_at = now + settings.poll_interval_seconds
                    sent_now = sent
                    if store.get_metadata("last_source_error"):
                        store.set_metadata("last_source_error", "")
                except (
                    AvitoApiError,
                    BrowserActError,
                    ListingsSourceError,
                    ValueError,
                ) as exc:
                    retry_schedule.record_failure(now)
                    rate_limited = bool(getattr(exc, "rate_limited", False))
                    if rate_limited:
                        retry_schedule.next_attempt_at = now + max(
                            settings.poll_interval_seconds, 300
                        )
                    log_event(
                        "cycle_failed",
                        status="failed",
                        component="monitor",
                        cycle_id=cycle_id,
                        failure_count=retry_schedule.failure_count,
                        rate_limited=rate_limited,
                        retry_in_seconds=max(
                            0, int(retry_schedule.next_attempt_at - now)
                        ),
                        error=describe_exception(exc),
                    )
                    store.set_metadata(
                        "last_source_error",
                        json.dumps(
                            {
                                "at": datetime.now(timezone.utc).isoformat(),
                                "cycle_id": cycle_id,
                                "error_type": type(exc).__name__,
                                "error": describe_exception(exc),
                                "rate_limited": rate_limited,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    if retry_schedule.needs_alert(
                        now, settings.source_alert_after_seconds
                    ):
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
                                log_event(
                                    "alert_delivery_failed",
                                    status="failed",
                                    component="telegram",
                                    cycle_id=cycle_id,
                                )
                        retry_schedule.mark_alert_sent()

            write_status(
                settings.status_path,
                heartbeat_at=time.time(),
                source_failure_count=retry_schedule.failure_count,
                monitoring_enabled=source is not None,
            )

            try:
                publish_site_data(store, settings, force=sent_now > 0)
            except (ValueError, *ftplib.all_errors) as exc:
                log_event(
                    "site_data_publish_failed",
                    status="failed",
                    component="publish",
                    error=describe_exception(exc),
                )
    except KeyboardInterrupt:
        stop_reason = "interrupted"
    except BaseException as exc:
        stop_reason = "failed"
        log_event(
            "bot_failed",
            status="failed",
            component="bot",
            error=describe_exception(exc),
        )
        raise
    finally:
        log_event(
            "bot_stopped",
            status="success" if stop_reason == "interrupted" else "failed",
            component="bot",
            reason=stop_reason,
        )


if __name__ == "__main__":
    run()
