"""Подробности объявления: описание, состояние, аккумулятор.

Лента Авито отдаёт только заголовок и цену, поэтому описание приходится
забирать отдельным запросом. Этот модуль сначала проверяет, какой адрес
вообще отвечает с сервера (результат один раз пишется в метаданные и
публикуется на сайте), а затем умеет вытаскивать из ответа поля.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

APP_USER_AGENT = "Avito/121.0 (Android 13; Pixel 6)"
PROBE_METADATA_KEY = "detail_probe_v1"
PROBE_PAUSE_SECONDS = 8.0

RawFetcher = Callable[[str], tuple[int, str, str]]


def _default_fetch_raw(url: str, timeout: float = 20.0) -> tuple[int, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": APP_USER_AGENT,
            "Accept": "application/json, text/html;q=0.9",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(400_000).decode("utf-8", "replace")
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", exc.read(2000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return -1, "", str(exc)


def candidate_urls(item_id: str | int, url_path: str) -> list[tuple[str, str]]:
    path = (url_path or "").split("?")[0]
    candidates = [
        ("web1_item", f"https://www.avito.ru/web/1/item/{item_id}/"),
        ("web1_items", f"https://www.avito.ru/web/1/items/{item_id}/"),
        ("html_page", f"https://www.avito.ru{path}"),
        (
            "mobile_api",
            "https://m.avito.ru/api/9/items?key=af0deccbgcgidddjgnvljtntccdduijhdinfgjgfjir&ids="
            f"{item_id}",
        ),
    ]
    return [(name, url) for name, url in candidates if name != "html_page" or path]


def probe(item_id: str | int, url_path: str, *, fetch_raw: RawFetcher | None = None, pause_seconds: float = PROBE_PAUSE_SECONDS) -> dict:
    fetch = fetch_raw or _default_fetch_raw
    results = []
    first_ok = None
    for index, (name, url) in enumerate(candidate_urls(item_id, url_path)):
        if index:
            time.sleep(pause_seconds)
        status, content_type, body = fetch(url)
        entry = {
            "name": name,
            "url": url,
            "status": status,
            "content_type": content_type[:60],
            "length": len(body),
            "snippet": body[:200].replace("\n", " "),
            "has_description": "description" in body,
        }
        results.append(entry)
        if status == 200 and body and first_ok is None:
            first_ok = name
    return {"item_id": item_id, "ok": first_ok, "attempts": results}


def run_probe_once(store, source, *, fetch_raw: RawFetcher | None = None, pause_seconds: float = PROBE_PAUSE_SECONDS) -> dict | None:
    if store.get_metadata(PROBE_METADATA_KEY):
        return None
    listings = source.fetch()
    if not listings:
        return None
    sample = listings[0]
    result = probe(sample.item_id, sample.url, fetch_raw=fetch_raw, pause_seconds=pause_seconds)
    store.set_metadata(PROBE_METADATA_KEY, json.dumps(result, ensure_ascii=False))
    return result
