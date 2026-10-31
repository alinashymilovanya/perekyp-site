"""Тарифы, квоты и промокоды VISHIT.

Бот вырос из «одного бесплатного потока»: теперь у человека есть план, лимит
сигналов в сутки, задержка доставки и трекер сделок. Этот модуль — единственный
источник правды о том, что каждый план разрешает.

Деньги и лимиты лежат рядом намеренно: тариф, который нельзя проверить кодом, —
это обещание в лендинге, а не продукт. Модуль ничего не знает про SQLite,
Telegram и HTTP: он только считает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Сколько дней длится пробный Pro после первого входа.
TRIAL_DAYS = 7

# Задержка «мгновенной» доставки: даже Pro не обещает 0 секунд.
INSTANT_DELAY_SECONDS = 20
PRO_DELAY_SECONDS = 90
FREE_DELAY_SECONDS = 30 * 60


@dataclass(frozen=True)
class Plan:
    """Один тариф: цена, лимит сигналов, задержка и набор функций."""

    code: str
    title: str
    price_month_rub: int
    price_year_rub: int
    signals_per_day: int | None
    delay_seconds: int
    max_filters: int
    history_days: int
    tracker: bool
    seats: int = 1
    # Осознанная пауза Free: сигнал не теряется, а приходит позже.
    # У платных планов 0 — доставляем сразу, как только сработал фильтр.
    queue_seconds: int = 0
    note: str = ""

    @property
    def unlimited(self) -> bool:
        return self.signals_per_day is None

    @property
    def delay_minutes(self) -> int:
        return max(1, round(self.delay_seconds / 60))

    def price(self, period: str = "month") -> int:
        """Цена за период: ``month`` или ``year``."""
        return self.price_year_rub if period == "year" else self.price_month_rub

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "price_month_rub": self.price_month_rub,
            "price_year_rub": self.price_year_rub,
            "signals_per_day": self.signals_per_day,
            "delay_seconds": self.delay_seconds,
            "queue_seconds": self.queue_seconds,
            "max_filters": self.max_filters,
            "history_days": self.history_days,
            "tracker": self.tracker,
            "seats": self.seats,
        }


FREE = Plan(
    code="free",
    title="Free",
    price_month_rub=0,
    price_year_rub=0,
    signals_per_day=3,
    delay_seconds=FREE_DELAY_SECONDS,
    max_filters=1,
    history_days=0,
    tracker=False,
    queue_seconds=FREE_DELAY_SECONDS,
    note="3 сигнала в сутки, задержка до 30 минут",
)

PRO = Plan(
    code="pro",
    title="Pro",
    price_month_rub=690,
    price_year_rub=6_900,
    signals_per_day=None,
    delay_seconds=PRO_DELAY_SECONDS,
    max_filters=20,
    history_days=90,
    tracker=True,
    note="без лимита сигналов, 90 секунд, история 90 дней",
)

INSTANT = Plan(
    code="instant",
    title="Instant",
    price_month_rub=1_490,
    price_year_rub=14_900,
    signals_per_day=None,
    delay_seconds=INSTANT_DELAY_SECONDS,
    max_filters=50,
    history_days=365,
    tracker=True,
    note="20 секунд, приоритетная очередь, 50 фильтров",
)

BUSINESS = Plan(
    code="business",
    title="Business",
    price_month_rub=3_900,
    price_year_rub=39_000,
    signals_per_day=None,
    delay_seconds=INSTANT_DELAY_SECONDS,
    max_filters=50,
    history_days=365,
    tracker=True,
    seats=5,
    note="5 мест, API и выгрузка, менеджер",
)

# Старая цена для тех, кто платил 399 ₽ до смены сетки: их нельзя
# переводить на новый прайс задним числом, иначе получим волну возвратов.
LEGACY = Plan(
    code="legacy",
    title="Pro Legacy",
    price_month_rub=399,
    price_year_rub=3_990,
    signals_per_day=None,
    delay_seconds=PRO_DELAY_SECONDS,
    max_filters=20,
    history_days=90,
    tracker=True,
    note="399 ₽ навсегда для действующих подписчиков",
)

PLANS: dict[str, Plan] = {plan.code: plan for plan in (FREE, PRO, INSTANT, BUSINESS, LEGACY)}
DEFAULT_PLAN_CODE = FREE.code
# Порядок для клавиатуры и лендинга: от дешёвого к дорогому, legacy отдельно.
PLAN_CHOICES: tuple[str, ...] = (FREE.code, PRO.code, INSTANT.code, BUSINESS.code)


def plan(code: str | None) -> Plan:
    """Тариф по коду; неизвестный код — это Free, а не падение."""
    if not code:
        return FREE
    return PLANS.get(str(code).strip().lower(), FREE)


@dataclass(frozen=True)
class Promo:
    """Скидка на первый платёж."""

    code: str
    percent: int
    months: int
    note: str


PROMOS: dict[str, Promo] = {
    "FIRST": Promo(code="FIRST", percent=50, months=1, note="−50% на первый месяц"),
}


def promo(code: str | None) -> Promo | None:
    if not code:
        return None
    return PROMOS.get(str(code).strip().upper())


def discounted_price(
    plan_code: str,
    *,
    period: str = "month",
    promo_code: str | None = None,
) -> dict:
    """Цена с промокодом и без него.

    Возвращает словарь, чтобы экран оплаты и вебхук считали одинаково:
    ``price`` — итог к списанию, ``discount`` — сколько срезали.
    """
    chosen = plan(plan_code)
    price = chosen.price(period)
    applied = promo(promo_code)
    if applied is None or price == 0:
        return {"plan": chosen.code, "price": price, "discount": 0, "promo": None}
    discount = round(price * applied.percent / 100)
    return {
        "plan": chosen.code,
        "price": price - discount,
        "discount": discount,
        "promo": applied.code,
    }


def day_key(moment: datetime | None = None) -> str:
    """Ключ суток в UTC: лимит обнуляется в 00:00 UTC."""
    current = _utc(moment)
    return current.strftime("%Y-%m-%d")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_in(moment: datetime | None, seconds: int) -> str:
    """Момент в будущем в том же формате, что пишут записи логов."""
    return (_utc(moment) + timedelta(seconds=max(0, int(seconds)))).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    current = _utc(moment)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def effective_plan(
    plan_code: str | None,
    plan_until: str | None,
    *,
    now: datetime | None = None,
) -> Plan:
    """Тариф с учётом срока: истёкшая подписка автоматически становится Free."""
    chosen = plan(plan_code)
    if not plan_until or chosen.code == FREE.code:
        return chosen
    until = parse_moment(plan_until)
    if until is None:
        return chosen
    return chosen if until > _utc(now) else FREE


def trial_until(moment: datetime | None = None) -> str:
    """ISO-момент окончания пробного периода."""
    return (_utc(moment) + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_moment(value: str | None) -> datetime | None:
    """Разбирает SQLite- или ISO-время; мусор превращается в None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Quota:
    """Сколько сигналов осталось у человека сегодня."""

    plan: Plan
    used: int

    @property
    def limit(self) -> int | None:
        return self.plan.signals_per_day

    @property
    def remaining(self) -> int | None:
        limit = self.limit
        if limit is None:
            return None
        return max(0, limit - self.used)

    @property
    def exhausted(self) -> bool:
        remaining = self.remaining
        return remaining is not None and remaining <= 0

    def details(self) -> dict:
        return {
            "plan": self.plan.code,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
        }


def quota(plan_code: str, used: int, *, plan_until: str | None = None, now: datetime | None = None) -> Quota:
    return Quota(plan=effective_plan(plan_code, plan_until, now=now), used=max(0, int(used)))


def describe(plan_code: str | None) -> str:
    """Строка для Telegram: «Pro · без лимита · 90 сек»."""
    chosen = plan(plan_code)
    if chosen.unlimited:
        limit = "без лимита"
    else:
        limit = f"{chosen.signals_per_day} в сутки"
    if chosen.delay_seconds < 120:
        delay = f"{chosen.delay_seconds} сек"
    else:
        delay = f"{chosen.delay_minutes} мин"
    return f"{chosen.title} · {limit} · {delay}"


def _utc(moment: datetime | None = None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)
