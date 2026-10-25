"""Environment-backed configuration."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str | None
    minimum_margin_rub: int = 500
    delivery_rub: int = 400
    commission_rub: int = 0
    repair_reserve_used_rub: int = 1_500
    repair_reserve_broken_rub: int = 10_000
    browser_act_path: str = "browser-act"
    avito_session_name: str = "avito-check"
    avito_search_url: str = ""
    database_path: str = "data/seen.sqlite3"
    poll_interval_seconds: int = 60
    listings_source: str = "disabled"
    listings_source_url: str = ""
    listings_source_token: str = ""
    status_path: str = "data/runtime-status.json"
    source_retry_base_seconds: int = 60
    source_retry_max_seconds: int = 1_800
    source_alert_after_seconds: int = 900
    daily_summary_time: str = "20:00"
    summary_timezone: str = "Europe/Astrakhan"


def _validate_daily_summary_schedule(summary_time: str, timezone_name: str) -> None:
    try:
        hour_text, minute_text = summary_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError("DAILY_SUMMARY_TIME must have HH:MM format") from exc
    if len(hour_text) != 2 or len(minute_text) != 2 or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("DAILY_SUMMARY_TIME must have HH:MM format")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("SUMMARY_TIMEZONE must be a valid IANA timezone") from exc


def load_settings(path: str | Path = ".env") -> Settings:
    values: dict[str, str] = {}
    env_path = Path(path)
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values.update(os.environ)

    token = values.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required in .env")
    daily_summary_time = values.get("DAILY_SUMMARY_TIME", "20:00")
    summary_timezone = values.get("SUMMARY_TIMEZONE", "Europe/Astrakhan")
    _validate_daily_summary_schedule(daily_summary_time, summary_timezone)

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=values.get("TELEGRAM_CHAT_ID") or None,
        minimum_margin_rub=int(values.get("MINIMUM_MARGIN_RUB", "500")),
        delivery_rub=int(values.get("DELIVERY_RUB", "400")),
        commission_rub=int(values.get("COMMISSION_RUB", "0")),
        repair_reserve_used_rub=int(values.get("REPAIR_RESERVE_USED_RUB", "1500")),
        repair_reserve_broken_rub=int(values.get("REPAIR_RESERVE_BROKEN_RUB", "10000")),
        browser_act_path=values.get("BROWSER_ACT_PATH", "browser-act"),
        avito_session_name=values.get("AVITO_SESSION_NAME", "avito-check"),
        avito_search_url=values.get("AVITO_SEARCH_URL", ""),
        database_path=values.get("DATABASE_PATH", "data/seen.sqlite3"),
        poll_interval_seconds=int(values.get("POLL_INTERVAL_SECONDS", "60")),
        listings_source=values.get("LISTINGS_SOURCE", "disabled"),
        listings_source_url=values.get("LISTINGS_SOURCE_URL", ""),
        listings_source_token=values.get("LISTINGS_SOURCE_TOKEN", ""),
        status_path=values.get("STATUS_PATH", "data/runtime-status.json"),
        source_retry_base_seconds=int(values.get("SOURCE_RETRY_BASE_SECONDS", "60")),
        source_retry_max_seconds=int(values.get("SOURCE_RETRY_MAX_SECONDS", "1800")),
        source_alert_after_seconds=int(values.get("SOURCE_ALERT_AFTER_SECONDS", "900")),
        daily_summary_time=daily_summary_time,
        summary_timezone=summary_timezone,
    )


def save_chat_id(path: str | Path, chat_id: str) -> None:
    """Persist the non-secret Telegram chat ID while preserving the token."""
    env_path = Path(path)
    lines = env_path.read_text(encoding="utf-8").splitlines()
    replacement = f"TELEGRAM_CHAT_ID={chat_id}"
    for index, line in enumerate(lines):
        if line.startswith("TELEGRAM_CHAT_ID="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
