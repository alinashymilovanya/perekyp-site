"""Long-polling entry point for the Telegram bot."""

import time
from pathlib import Path

from .avito_browser import DEFAULT_SEARCH_URL, AvitoBrowserSource, BrowserActError
from .bot import handle_update
from .config import load_settings, save_chat_id
from .monitor import Monitor
from .store import SeenStore
from .telegram_api import TelegramApiError, TelegramClient


def run() -> None:
    settings = load_settings()
    telegram = TelegramClient(settings.telegram_bot_token)
    source = AvitoBrowserSource(
        browser_act_path=settings.browser_act_path,
        session_name=settings.avito_session_name,
        search_url=settings.avito_search_url or DEFAULT_SEARCH_URL,
    )
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = SeenStore(database_path)
    offset = None
    last_monitor_at = 0.0
    chat_id = settings.telegram_chat_id
    while True:
        try:
            for update in telegram.get_updates(offset, timeout_seconds=5):
                offset = update["update_id"] + 1
                update_chat_id = handle_update(update, telegram)
                if update_chat_id:
                    chat_id = update_chat_id
                    save_chat_id(".env", update_chat_id)
            if chat_id and time.monotonic() - last_monitor_at >= settings.poll_interval_seconds:
                try:
                    Monitor(source, store, telegram, chat_id).process_once()
                    last_monitor_at = time.monotonic()
                except BrowserActError:
                    last_monitor_at = time.monotonic()
        except TelegramApiError:
            time.sleep(5)


if __name__ == "__main__":
    run()
