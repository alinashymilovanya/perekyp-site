"""Self-check for a fresh installation or a server deployment.

Run it before complaining that the bot "does not work":

    python -m iphone_monitor.doctor

Every check is independent: a failure in one line never hides the rest.
Exit code is 1 when at least one critical check failed.
"""

from __future__ import annotations

import socket
import sqlite3
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .avito_api_source import AvitoApiError, AvitoApiSource
from .config import Settings, load_settings
from .store import SeenStore
from .telegram_api import API_HOST

API_PORT = 443


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    critical: bool = True


def check_settings(settings: Settings) -> Check:
    problems: list[str] = []
    if settings.listings_source == "avito_api" and settings.poll_interval_seconds < 180:
        problems.append(
            "POLL_INTERVAL_SECONDS="
            f"{settings.poll_interval_seconds}: Авито отвечает 429, нужно не меньше 180"
        )
    if settings.telegram_transport == "mtproto" and not settings.telegram_proxy:
        problems.append("TELEGRAM_TRANSPORT=mtproto, но TELEGRAM_PROXY пустой")
    if not settings.telegram_bot_token:
        problems.append("TELEGRAM_BOT_TOKEN пустой")
    if problems:
        return Check("Настройки", False, "; ".join(problems))
    return Check(
        "Настройки",
        True,
        f"источник {settings.listings_source}, опрос {settings.poll_interval_seconds} с, "
        f"Telegram {settings.telegram_transport}",
    )


def check_database(settings: Settings) -> Check:
    path = Path(settings.database_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with SeenStore(path) as store:
            prices = store.comparable_prices("iPhone 15", 128, None)
            subscribers = store.list_subscribers()
    except (OSError, sqlite3.Error) as exc:
        return Check("База", False, f"{path}: {exc}")
    return Check(
        "База",
        True,
        f"{path}: {len(prices)} цен iPhone 15/128 в истории, "
        f"{len(subscribers)} подписчиков",
    )


def check_avito(settings: Settings, *, fetch_json: Callable[[str], dict] | None = None) -> Check:
    source = AvitoApiSource(
        location_id=settings.avito_api_location_id,
        max_listings=50,
        pages=1,
        sort=settings.avito_api_sort,
        fetch_json=fetch_json,
    )
    try:
        listings = source.fetch()
    except AvitoApiError as exc:
        return Check("Авито", False, str(exc))
    except OSError as exc:
        return Check("Авито", False, f"нет связи: {exc}")
    if not listings:
        return Check(
            "Авито",
            False,
            "лента ответила, но iPhone в ней не нашлось — проверьте AVITO_API_LOCATION_ID",
        )
    return Check("Авито", True, f"лента отдаёт объявления, пример: {listings[0].title}")


def _tcp_probe(host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} отвечает"
    except OSError as exc:
        return False, f"{host}:{port} недоступен: {exc}"


def _https_probe(host: str, port: int = API_PORT, timeout: float = 10.0) -> tuple[bool, str]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return True, f"TLS до {host}:{port} установлен ({tls.version()})"
    except (OSError, ssl.SSLError) as exc:
        return False, f"TLS до {host}:{port} не поднимается: {exc}"


def _proxy_endpoint(proxy: str) -> tuple[str, int] | None:
    value = proxy.strip()
    if not value:
        return None
    if "server=" in value:
        query = value.split("?", 1)[1] if "?" in value else value
        parts = dict(chunk.split("=", 1) for chunk in query.split("&") if "=" in chunk)
        server = parts.get("server")
        port = parts.get("port")
    else:
        chunks = value.split(":")
        if len(chunks) < 2:
            return None
        server, port = chunks[0], chunks[1]
    if not server or not port:
        return None
    try:
        return server, int(port)
    except ValueError:
        return None


def check_telegram(
    settings: Settings,
    *,
    tcp_probe: Callable[[str, int], tuple[bool, str]] = _tcp_probe,
    https_probe: Callable[[str, int], tuple[bool, str]] = _https_probe,
) -> Check:
    if settings.telegram_transport == "mtproto":
        endpoint = _proxy_endpoint(settings.telegram_proxy)
        if endpoint is None:
            return Check("Telegram", False, "не разобрать TELEGRAM_PROXY")
        ok, detail = tcp_probe(*endpoint)
        session = Path(f"{settings.telegram_session_path}.session")
        state = "сессия есть" if session.is_file() else "сессия создастся при запуске"
        if not ok:
            return Check("Telegram", False, f"MTProto-прокси {detail}; {state}")
        return Check("Telegram", True, f"MTProto-прокси {detail}; {state}")

    ok, detail = https_probe(API_HOST, API_PORT)
    if not ok:
        return Check(
            "Telegram",
            False,
            f"{detail}. Bot API заблокирован — включите TELEGRAM_TRANSPORT=mtproto",
        )
    return Check("Telegram", True, detail)


def check_single_instance(settings: Settings) -> Check:
    """Report whether a bot is already running; any answer here is healthy."""
    lock = Path(settings.database_path).parent / "bot.lock"
    if not lock.is_file():
        return Check("Блокировка", True, "бот не запущен — файла bot.lock нет", critical=False)
    try:
        pid = lock.read_text(encoding="ascii").strip()
    except OSError:
        return Check(
            "Блокировка", True, "бот уже работает (файл bot.lock занят)", critical=False
        )
    return Check("Блокировка", True, f"бот уже работает, pid {pid}", critical=False)


def run_checks(
    settings: Settings,
    *,
    fetch_json: Callable[[str], dict] | None = None,
    tcp_probe: Callable[[str, int], tuple[bool, str]] = _tcp_probe,
    https_probe: Callable[[str, int], tuple[bool, str]] = _https_probe,
) -> list[Check]:
    return [
        check_settings(settings),
        check_database(settings),
        check_avito(settings, fetch_json=fetch_json),
        check_telegram(settings, tcp_probe=tcp_probe, https_probe=https_probe),
        check_single_instance(settings),
    ]


def format_report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK  " if check.ok else ("СТОП" if check.critical else "ИНФО")
        lines.append(f"[{mark}] {check.name}: {check.detail}")
    failed = [check for check in checks if not check.ok and check.critical]
    lines.append("")
    lines.append(
        "Всё в порядке." if not failed else f"Проблем: {len(failed)}. Смотрите СТОП-строки выше."
    )
    return "\n".join(lines)


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"[СТОП] Настройки: {exc}")
        return 1
    checks = run_checks(settings)
    print(format_report(checks))
    return 0 if all(not c.critical or c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
