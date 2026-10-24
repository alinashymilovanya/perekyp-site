"""Command handling for the Telegram bot."""

HELP_TEXT = (
    "Команды:\n"
    "/start — подключить уведомления\n"
    "/status — показать статус бота\n"
    "/pause — временно остановить уведомления\n"
    "/resume — возобновить уведомления"
)


def handle_update(update: dict, telegram) -> str | None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip().lower()
    if chat_id is None or not text.startswith("/"):
        return None

    chat_id = str(chat_id)
    if text == "/start":
        telegram.send_message(
            chat_id,
            "Бот подключён. Telegram-уведомления готовы к настройке мониторинга.\n\n"
            + HELP_TEXT,
        )
    elif text == "/status":
        telegram.send_message(chat_id, "Бот работает. Мониторинг Авито ещё подключается.")
    elif text in {"/pause", "/resume"}:
        telegram.send_message(chat_id, "Эта команда будет активна после подключения источника объявлений.")
    else:
        telegram.send_message(chat_id, HELP_TEXT)
    return chat_id
