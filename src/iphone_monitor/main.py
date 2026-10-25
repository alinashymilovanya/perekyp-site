"""Long-polling entry point for the Telegram bot."""

import time
from datetime import datetime, timezone
from pathlib import Path

from .avito_browser import BrowserActError
from .bot import handle_update
from .config import load_settings
from .daily import send_daily_summary_if_due
from .health import write_status
from .json_api_source import ListingsSourceError
from .monitor import Monitor
from .runtime import RetrySchedule
from .source_factory import create_listings_source
from .store import SeenStore
from .telemetry import configure_logging, log_event
from .telegram_api import TelegramApiError, TelegramClient


def run() -> None:
    configure_logging()
    settings = load_settings()
    telegram = TelegramClient(settings.telegram_bot_token)
    source = create_listings_source(settings)
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = SeenStore(database_path)
    offset = None
    chat_id = settings.telegram_chat_id or store.get_metadata("telegram_chat_id")
    retry_schedule = RetrySchedule(
        base_delay_seconds=settings.source_retry_base_seconds,
        max_delay_seconds=settings.source_retry_max_seconds,
    )
    if source is None:
        log_event("monitoring_disabled")
    while True:
        try:
            for update in telegram.get_updates(offset, timeout_seconds=5):
                offset = update["update_id"] + 1
                update_chat_id = handle_update(update, telegram)
                if update_chat_id:
                    chat_id = update_chat_id
                    store.set_metadata("telegram_chat_id", update_chat_id)
            if chat_id and send_daily_summary_if_due(
                store,
                telegram,
                chat_id,
                now=datetime.now(timezone.utc),
                timezone_name=settings.summary_timezone,
                summary_time=settings.daily_summary_time,
            ):
                log_event("daily_summary_sent")
            now = time.time()
            if source is not None and chat_id and retry_schedule.can_attempt(now):
                try:
                    sent = Monitor(
                        source,
                        store,
                        telegram,
                        chat_id,
                        delivery_rub=settings.delivery_rub,
                        commission_rub=settings.commission_rub,
                        minimum_margin_rub=settings.minimum_margin_rub,
                    ).process_once()
                    retry_schedule.record_success(now)
                    retry_schedule.next_attempt_at = now + settings.poll_interval_seconds
                    log_event("monitoring_poll_succeeded", notified_count=sent)
                except (BrowserActError, ListingsSourceError, ValueError) as exc:
                    retry_schedule.record_failure(now)
                    log_event(
                        "monitoring_poll_failed",
                        failure_count=retry_schedule.failure_count,
                        error_type=type(exc).__name__,
                    )
                    if retry_schedule.needs_alert(now, settings.source_alert_after_seconds):
                        telegram.send_message(
                            chat_id,
                            "⚠️ Мониторинг объявлений временно недоступен. "
                            "Бот продолжит пробовать автоматически.",
                        )
                        retry_schedule.mark_alert_sent()
        except TelegramApiError:
            log_event("telegram_request_failed")
            time.sleep(5)
        finally:
            write_status(
                settings.status_path,
                heartbeat_at=time.time(),
                source_failure_count=retry_schedule.failure_count,
                monitoring_enabled=source is not None,
            )


if __name__ == "__main__":
    run()
