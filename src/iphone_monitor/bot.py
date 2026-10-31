"""Command handling for the Telegram bot."""

from . import plans
from .telemetry import describe_exception, log_event

HELP_TEXT = (
    "Команды:\n"
    "/start — подключить уведомления\n"
    "/stop — отключить уведомления\n"
    "/status — показать статус бота\n"
    "/plan — тариф и остаток сигналов\n"
    "/upgrade — тарифы и цены\n"
    "/track — сводка по сделкам\n"
    "/pause — временно остановить мониторинг\n"
    "/resume — возобновить мониторинг"
)


def _money(value: int) -> str:
    """«1 490 ₽» — неразрывный для глаза разделитель тысяч."""
    return f"{int(value):,}".replace(",", " ") + " ₽"


def _plan_text(store, chat_id: str) -> str:
    """Тариф человека и сколько сигналов осталось на сегодня."""
    account = store.ensure_account(chat_id)
    current = plans.effective_plan(account.plan, account.plan_until)
    room = plans.quota(current.code, store.signals_used(chat_id, plans.day_key()))
    if room.limit is None:
        left = "сегодня без ограничений"
    else:
        left = f"сегодня использовано {room.used} из {room.limit}"
    return f"Тариф: {plans.describe(current.code)}.\n{left[0].upper() + left[1:]}."


def _upgrade_text() -> str:
    lines = ["Тарифы VISHIT:"]
    for code in plans.PLAN_CHOICES:
        chosen = plans.plan(code)
        if chosen.price_month_rub == 0:
            price = "0 ₽"
        else:
            price = f"{_money(chosen.price_month_rub)}/мес или {_money(chosen.price_year_rub)}/год"
        lines.append(f"• {chosen.title} — {price}. {chosen.note}.")
    lines.append("Оплата в Telegram, отмена в один клик: /plan покажет остаток сигналов.")
    return "\n".join(lines)


def _track_text(store, chat_id: str) -> str:
    summary = store.deals_summary(chat_id)
    if not summary["count"]:
        return "Сделок пока нет. Отметьте купленный телефон в кабинете — трекер посчитает прибыль."
    return (
        f"Сделки: {summary['count']} · продано {summary['sold']}.\n"
        f"Прибыль: {_money(summary['profit_rub'])} · оборот: {_money(summary['revenue_rub'])} · "
        f"средняя маржа: {_money(summary['avg_profit_rub'])}."
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
            # Новый человек начинает с Free: три сигнала в сутки и честная
            # задержка, чтобы тариф можно было проверить до оплаты.
            store.ensure_account(chat_id, plans.DEFAULT_PLAN_CODE)
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
    elif command == "/plan":
        text = _plan_text(store, chat_id) if store is not None else plans.describe("free")
        telegram.send_message(chat_id, text)
        log_event("telegram_command", status="success", component="bot", command=command)
    elif command == "/upgrade":
        telegram.send_message(chat_id, _upgrade_text())
        log_event("telegram_command", status="success", component="bot", command=command)
    elif command == "/track":
        text = (
            _track_text(store, chat_id)
            if store is not None
            else "Трекер доступен в кабинете."
        )
        telegram.send_message(chat_id, text)
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
