"""Environment-backed configuration."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str | None
    telegram_proxy: str = ""
    telegram_transport: str = "botapi"
    telegram_api_id: int = 2040
    telegram_api_hash: str = "b18441a1ff607e10a989891a5462e627"
    telegram_session_path: str = "data/bot_mtproto"
    minimum_margin_rub: int = 500
    notify_min_margin_rub: int = 0
    delivery_rub: int = 400
    commission_rub: int = 0
    repair_reserve_used_rub: int = 1_500
    repair_reserve_broken_rub: int = 10_000
    weak_deal_margin_rub: int = 1_500
    browser_act_path: str = "browser-act"
    avito_browser_id: str = ""
    avito_session_name: str = "avito-check"
    avito_search_url: str = ""
    avito_api_location_id: str = "107620"
    avito_api_query: str = ""
    avito_api_pages: int = 1
    avito_api_sort: str = "date"
    database_path: str = "data/seen.sqlite3"
    poll_interval_seconds: int = 60
    max_listings_per_poll: int = 50
    listings_source: str = "disabled"
    listings_source_url: str = ""
    listings_source_token: str = ""
    vision_api_token: str = ""
    vision_folder_id: str = ""
    vision_profile_id: str = ""
    vision_node_path: str = "node"
    vision_playwright_path: str = "playwright"
    status_path: str = "data/runtime-status.json"
    log_path: str = "data/bot.log"
    source_retry_base_seconds: int = 60
    source_retry_max_seconds: int = 1_800
    source_alert_after_seconds: int = 900
    daily_summary_time: str = "20:00"
    summary_timezone: str = "Europe/Astrakhan"
    site_export_path: str = "data/site-data.json"
    site_export_interval_seconds: int = 3_600
    site_ftp_host: str = ""
    site_ftp_user: str = ""
    site_ftp_password: str = ""
    site_ftp_dir: str = "/htdocs"
    github_token: str = ""
    github_repo: str = ""
    github_site_path: str = "site/site-data.json"
    github_branch: str = "master"


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
        telegram_proxy=values.get("TELEGRAM_PROXY", ""),
        telegram_transport=values.get("TELEGRAM_TRANSPORT", "botapi").strip().lower(),
        telegram_api_id=int(values.get("TELEGRAM_API_ID", "2040")),
        telegram_api_hash=values.get(
            "TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627"
        ),
        telegram_session_path=values.get("TELEGRAM_SESSION_PATH", "data/bot_mtproto"),
        minimum_margin_rub=int(values.get("MINIMUM_MARGIN_RUB", "500")),
        notify_min_margin_rub=int(values.get("NOTIFY_MIN_MARGIN_RUB", "0")),
        delivery_rub=int(values.get("DELIVERY_RUB", "400")),
        commission_rub=int(values.get("COMMISSION_RUB", "0")),
        repair_reserve_used_rub=int(values.get("REPAIR_RESERVE_USED_RUB", "1500")),
        repair_reserve_broken_rub=int(values.get("REPAIR_RESERVE_BROKEN_RUB", "10000")),
        weak_deal_margin_rub=int(values.get("WEAK_DEAL_MARGIN_RUB", "1500")),
        browser_act_path=values.get("BROWSER_ACT_PATH", "browser-act"),
        avito_browser_id=values.get("AVITO_BROWSER_ID", ""),
        avito_session_name=values.get("AVITO_SESSION_NAME", "avito-check"),
        avito_search_url=values.get("AVITO_SEARCH_URL", ""),
        avito_api_location_id=values.get("AVITO_API_LOCATION_ID", "107620"),
        avito_api_query=values.get("AVITO_API_QUERY", ""),
        avito_api_pages=int(values.get("AVITO_API_PAGES", "1")),
        avito_api_sort=values.get("AVITO_API_SORT", "date"),
        database_path=values.get("DATABASE_PATH", "data/seen.sqlite3"),
        poll_interval_seconds=int(values.get("POLL_INTERVAL_SECONDS", "60")),
        max_listings_per_poll=int(values.get("MAX_LISTINGS_PER_POLL", "50")),
        listings_source=values.get("LISTINGS_SOURCE", "disabled"),
        listings_source_url=values.get("LISTINGS_SOURCE_URL", ""),
        listings_source_token=values.get("LISTINGS_SOURCE_TOKEN", ""),
        vision_api_token=values.get("VISION_API_TOKEN", ""),
        vision_folder_id=values.get("VISION_FOLDER_ID", ""),
        vision_profile_id=values.get("VISION_PROFILE_ID", ""),
        vision_node_path=values.get("VISION_NODE_PATH", "node"),
        vision_playwright_path=values.get("VISION_PLAYWRIGHT_PATH", "playwright"),
        status_path=values.get("STATUS_PATH", "data/runtime-status.json"),
        log_path=values.get("LOG_PATH", "data/bot.log"),
        source_retry_base_seconds=int(values.get("SOURCE_RETRY_BASE_SECONDS", "60")),
        source_retry_max_seconds=int(values.get("SOURCE_RETRY_MAX_SECONDS", "1800")),
        source_alert_after_seconds=int(values.get("SOURCE_ALERT_AFTER_SECONDS", "900")),
        daily_summary_time=daily_summary_time,
        summary_timezone=summary_timezone,
        site_export_path=values.get("SITE_EXPORT_PATH", "data/site-data.json"),
        site_export_interval_seconds=int(
            values.get("SITE_EXPORT_INTERVAL_SECONDS", "3600")
        ),
        site_ftp_host=values.get("SITE_FTP_HOST", ""),
        site_ftp_user=values.get("SITE_FTP_USER", ""),
        site_ftp_password=values.get("SITE_FTP_PASSWORD", ""),
        site_ftp_dir=values.get("SITE_FTP_DIR", "/htdocs"),
        github_token=values.get("GITHUB_TOKEN", ""),
        github_repo=values.get("GITHUB_REPO", ""),
        github_site_path=values.get("GITHUB_SITE_PATH", "site/site-data.json"),
        github_branch=values.get("GITHUB_BRANCH", "master"),
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
