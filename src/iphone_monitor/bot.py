"""Command handling for the Telegram bot."""

HELP_TEXT = (
    "Команды:\n"
    "/start — подключить уведомления\n"
    "/stop — отключить уведомления\n"
    "/status — показать статус бота\n"
    "/pause — временно остановить мониторинг\n"
    "/resume — возобновить мониторинг"
)


def handle_update(update: dict, telegram, *, store=None) -> str | None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip().lower()
    if chat_id is None or not text.startswith("/"):
        return None

    chat_id = str(chat_id)
    command = text.split()[0].split("@", 1)[0]
    if command == "/start":
        if store is not None:
            store.add_subscriber(chat_id)
        telegram.send_message(
            chat_id,
            "Бот подключён. Уведомления о новых объявлениях будут приходить сюда.\n\n"
            + HELP_TEXT,
        )
    elif command == "/stop":
        if store is not None:
            store.remove_subscriber(chat_id)
        telegram.send_message(chat_id, "Уведомления отключены. Вернуть их можно командой /start.")
    elif command == "/status":
        paused = store is not None and store.get_metadata("monitor_paused") == "1"
        state = "приостановлен" if paused else "работает"
        subscribers = len(store.list_subscribers()) if store is not None else 1
        telegram.send_message(
            chat_id,
            f"Бот работает. Мониторинг {state}. Подписчиков: {subscribers}.",
        )
    elif command == "/pause":
        if store is not None:
            store.set_metadata("monitor_paused", "1")
        telegram.send_message(chat_id, "Мониторинг приостановлен. Новые объявления не отправляются.")
    elif command == "/resume":
        if store is not None:
            store.set_metadata("monitor_paused", "0")
        telegram.send_message(chat_id, "Мониторинг возобновлён.")
    else:
        telegram.send_message(chat_id, HELP_TEXT)
    return chat_id
