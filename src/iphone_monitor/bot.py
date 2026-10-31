"""Command handling for the Telegram bot."""

from .telemetry import describe_exception, log_event

HELP_TEXT = (
    "Команды:\n"
    "/start — подключить уведомления\n"
    "/stop — отключить уведомления\n"
    "/status — показать статус бота\n"
    "/pause — временно остановить мониторинг\n"
    "/resume — возобновить мониторинг"
)

# Commands that change the polling state must also refresh the public
# snapshot, otherwise the dashboard keeps showing the old state for an hour.
STATE_CHANGING_COMMANDS = frozenset({"/pause", "/resume"})


def handle_update(update: dict, telegram, *, store=None, publish=None) -> str | None:
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
        log_event("telegram_command", status="success", component="bot", command=command)
    elif command == "/stop":
        if store is not None:
            store.remove_subscriber(chat_id)
        telegram.send_message(chat_id, "Уведомления отключены. Вернуть их можно командой /start.")
        log_event("telegram_command", status="success", component="bot", command=command)
    elif command == "/status":
        paused = store is not None and store.get_metadata("monitor_paused") == "1"
        state = "приостановлен" if paused else "работает"
        subscribers = len(store.list_subscribers()) if store is not None else 1
        telegram.send_message(
            chat_id,
            f"Бот работает. Мониторинг {state}. Подписчиков: {subscribers}.",
        )
        log_event(
            "telegram_command",
            status="success",
            component="bot",
            command=command,
            paused=paused,
            subscribers=subscribers,
        )
    elif command == "/pause":
        if store is not None:
            store.set_metadata("monitor_paused", "1")
        telegram.send_message(chat_id, "Мониторинг приостановлен. Новые объявления не отправляются.")
        log_event("monitoring_paused", status="success", component="bot")
    elif command == "/resume":
        if store is not None:
            store.set_metadata("monitor_paused", "0")
        telegram.send_message(chat_id, "Мониторинг возобновлён.")
        log_event("monitoring_resumed", status="success", component="bot")
    else:
        telegram.send_message(chat_id, HELP_TEXT)
        log_event(
            "telegram_command",
            status="skipped",
            component="bot",
            command=command,
            reason="unknown_command",
        )
        return chat_id

    if command in STATE_CHANGING_COMMANDS and publish is not None:
        try:
            publish()
        except Exception as error:  # a snapshot must never break the command flow
            log_event(
                "site_snapshot_publish_failed",
                status="failed",
                component="publish",
                reason="command_refresh",
                error=describe_exception(error),
            )
    return chat_id
