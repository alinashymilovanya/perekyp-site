"""Read the listing page so condition, battery and description are real.

The search feed carries only the title and the price, so every notification
said "Состояние: не указано" while the page itself knew everything. The
mobile page of m.avito.ru renders the whole card — description and the
parameter block included — and it answers the Avito app user agent even from
the datacenter IP the monitor runs on.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from .avito_parser import ParsedListing, normalize_model
from .telemetry import log_event

APP_USER_AGENT = "Avito/121.0 (Android 13; Pixel 6)"
_INITIAL_DATA_MARKER = "window.__initialData__ = "
PROBE_METADATA_KEY = "detail_probe_v3"
SAMPLE_ITEM_URL = "https://m.avito.ru/moskva/telefony/iphone_17_pro_512_gb_sim_esim_8372561555"
COOLDOWN_METADATA_KEY = "detail_cooldown_until"
PROBE_PAUSE_SECONDS = 8.0
DEFAULT_TIMEOUT_SECONDS = 25.0
_BLOCK_MARKERS = (
    "Доступ ограничен",
    "проверка безопасности",
    "проблема с IP",
    "Подтвердите, что вы не робот",
)
_CONDITION_MAP = {
    "новое": "Новый",
    "новый": "Новый",
    "б/у": "Б/у",
    "бу": "Б/у",
    "отличное": "Отличное",
    "хорошее": "Хорошее",
    "удовлетворительное": "Удовлетворительное",
    "битый": "Битый",
    "битое": "Битый",
    "на запчасти": "Битый",
}
_BATTERY_TITLES = {
    "аккумулятор",
    "ёмкость аккумулятора",
    "емкость аккумулятора",
    "состояние аккумулятора",
    "акб",
}
_STORAGE_TITLES = {"встроенная память", "память", "объём памяти", "объем памяти"}
_COLOR_TITLES = {"цвет"}
_SIM_TITLES = {"sim-карты", "sim", "количество sim-карт"}
_MODEL_TITLES = {"модель"}
_IMAGE_SIZE = re.compile(r"^(\d+)x\d+$")


class ItemDetailError(RuntimeError):
    """The listing page could not be read."""

    def __init__(
        self,
        message: str,
        *,
        rate_limited: bool = False,
        blocked: bool = False,
    ):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.blocked = blocked


@dataclass(frozen=True)
class ItemDetail:
    item_id: str
    title: str = ""
    description: str = ""
    price_rub: int | None = None
    condition: str | None = None
    battery_percent: int | None = None
    storage_gb: int | None = None
    color: str = ""
    sim: str = ""
    seller_name: str = ""
    address: str = ""
    published_at: str = ""
    images: tuple[str, ...] = ()
    params: tuple[tuple[str, str], ...] = ()

    def as_payload(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "description": self.description,
            "price_rub": self.price_rub,
            "condition": self.condition,
            "battery_percent": self.battery_percent,
            "storage_gb": self.storage_gb,
            "color": self.color,
            "sim": self.sim,
            "seller_name": self.seller_name,
            "address": self.address,
            "published_at": self.published_at,
            "images": list(self.images),
            "params": [list(pair) for pair in self.params],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "ItemDetail":
        return cls(
            item_id=str(payload.get("item_id") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            price_rub=payload.get("price_rub"),
            condition=payload.get("condition"),
            battery_percent=payload.get("battery_percent"),
            storage_gb=payload.get("storage_gb"),
            color=str(payload.get("color") or ""),
            sim=str(payload.get("sim") or ""),
            seller_name=str(payload.get("seller_name") or ""),
            address=str(payload.get("address") or ""),
            published_at=str(payload.get("published_at") or ""),
            images=tuple(str(image) for image in payload.get("images") or ()),
            params=tuple(
                (str(pair[0]), str(pair[1])) for pair in payload.get("params") or ()
            ),
        )


def _number(value: str) -> int:
    return int(re.sub(r"[^0-9]", "", value) or 0)


def url_path(url: str) -> str:
    """'/moskva/telefony/iphone_16_128_gb_123' — no origin, no query."""
    path = str(url or "").strip()
    path = re.sub(r"^https?://[^/]+", "", path)
    return path.split("?", 1)[0]


def fetch_page(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Ask for the mobile card exactly the way the Avito app asks."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": APP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        snippet = ""
        try:
            snippet = error.read().decode("utf-8", "replace")[:200]
        except OSError:
            pass
        raise ItemDetailError(
            f"HTTP {error.code} for {url} {snippet}".strip(),
            rate_limited=error.code in {403, 429, 439},
        ) from error
    except (urllib.error.URLError, OSError) as error:
        raise ItemDetailError(f"network error for {url}: {error}") from error


def _initial_data(html: str) -> dict:
    """Decode window.__initialData__ — a JSON document stored as a JS string."""
    index = html.find(_INITIAL_DATA_MARKER)
    if index < 0:
        raise ItemDetailError("the page has no __initialData__ block")
    index += len(_INITIAL_DATA_MARKER)
    while index < len(html) and html[index] in " \t\r\n":
        index += 1
    if index >= len(html) or html[index] != '"':
        raise ItemDetailError("unexpected __initialData__ format")
    index += 1
    buffer: list[str] = []
    while index < len(html):
        char = html[index]
        if char == "\\":
            buffer.append(html[index : index + 2])
            index += 2
            continue
        if char == '"':
            break
        buffer.append(char)
        index += 1
    literal = '"' + "".join(buffer) + '"'
    try:
        return json.loads(json.loads(literal))
    except ValueError as error:
        raise ItemDetailError(f"__initialData__ is not JSON: {error}") from error


def _item_node(data: dict) -> dict:
    node = data.get("item") or {}
    item = node.get("item") or {}
    if not item:
        raise ItemDetailError("the page carries no item block")
    return item


def _params(item: dict) -> tuple[tuple[str, str], ...]:
    flat = ((item.get("parameters") or {}).get("flat")) or []
    pairs = []
    for entry in flat:
        title = str(entry.get("title") or "").strip()
        value = str(entry.get("description") or "").strip()
        if title and value:
            pairs.append((title, value))
    return tuple(pairs)


def _lookup(params: tuple[tuple[str, str], ...], titles: set[str]) -> str:
    for title, value in params:
        if title.strip().lower() in titles:
            return value
    return ""


def _normalize_condition(value: str) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return _CONDITION_MAP.get(cleaned.lower(), cleaned)


def _battery_percent(value: str) -> int | None:
    """'87%' -> 87; 'Ниже 80%' -> 79 so it still counts as a worn battery."""
    text = str(value or "")
    match = re.search(r"(\d{1,3})\s*%", text) or re.search(r"\b(\d{1,3})\b", text)
    if not match:
        return None
    percent = int(match.group(1))
    if not 1 <= percent <= 100:
        return None
    if "ниже" in text.lower():
        return max(percent - 1, 1)
    return percent


def _storage_gb(value: str) -> int | None:
    match = re.search(r"(\d+)\s*(ГБ|GB|ТБ|TB)\b", str(value or ""), flags=re.IGNORECASE)
    if not match:
        return None
    size = int(match.group(1))
    return size * 1024 if match.group(2).lower() in {"тб", "tb"} else size


def _price_rub(price: dict) -> int | None:
    raw = str(price.get("value") or "").strip()
    if not raw:
        raw = str((price.get("current") or {}).get("value_signed") or "")
    digits = re.sub(r"[^0-9]", "", raw)
    return int(digits) if digits else None


def _image_urls(images: list) -> tuple[str, ...]:
    urls = []
    for image in images or ():
        if not isinstance(image, dict):
            continue
        best_size, best_url = -1, ""
        for size, link in image.items():
            match = _IMAGE_SIZE.match(str(size))
            if match and int(match.group(1)) > best_size and link:
                best_size, best_url = int(match.group(1)), str(link)
        if best_url:
            urls.append(best_url)
    return tuple(urls)


def parse_item_page(html: str, item_id: str) -> ItemDetail:
    """Turn a mobile listing page into the facts the feed does not carry."""
    item = _item_node(_initial_data(html))
    params = _params(item)
    published = ""
    raw_time = item.get("time")
    if isinstance(raw_time, (int, float)) and raw_time > 0:
        published = datetime.fromtimestamp(float(raw_time), tz=timezone.utc).isoformat()
    seller = item.get("seller") or {}
    return ItemDetail(
        item_id=str(item.get("id") or item_id),
        title=str(item.get("title") or "").strip(),
        description=str(item.get("description") or "").strip(),
        price_rub=_price_rub(item.get("price") or {}),
        condition=_normalize_condition(_lookup(params, {"состояние"})),
        battery_percent=_battery_percent(_lookup(params, _BATTERY_TITLES)),
        storage_gb=_storage_gb(_lookup(params, _STORAGE_TITLES))
        or _storage_gb(str(item.get("title") or "")),
        color=_lookup(params, _COLOR_TITLES),
        sim=_lookup(params, _SIM_TITLES),
        seller_name=str(seller.get("name") or ""),
        address=str(item.get("address") or ""),
        published_at=published,
        images=_image_urls(item.get("images") or []),
        params=params,
    )


def detail_url_candidates(listing: ParsedListing) -> list[tuple[str, str]]:
    """Mobile page first: it is the one that survives the app user agent."""
    path = url_path(listing.url)
    if not path:
        return []
    return [
        ("m_html", "https://m.avito.ru" + path),
        ("www_html", "https://www.avito.ru" + path),
    ]


def load_detail(
    listing: ParsedListing,
    *,
    fetch: Callable[[str], str] = fetch_page,
) -> ItemDetail:
    """Read the first candidate URL that answers with a real card."""
    errors = []
    for name, url in detail_url_candidates(listing):
        try:
            html = fetch(url)
        except ItemDetailError as error:
            errors.append(f"{name}: {error}")
            if error.rate_limited:
                raise
            continue
        if any(marker in html for marker in _BLOCK_MARKERS):
            errors.append(f"{name}: Avito block page")
            continue
        try:
            return parse_item_page(html, listing.item_id)
        except ItemDetailError as error:
            errors.append(f"{name}: {error}")
    raise ItemDetailError("; ".join(errors) or "no candidate url")


def apply_detail(listing: ParsedListing, detail: ItemDetail) -> ParsedListing:
    """Merge the page facts into the feed listing, keeping the feed price."""
    title = detail.title or listing.title
    return replace(
        listing,
        title=title,
        model=normalize_model(title) or listing.model,
        storage_gb=detail.storage_gb or listing.storage_gb,
        condition=detail.condition or listing.condition,
        battery_percent=(
            detail.battery_percent
            if detail.battery_percent is not None
            else listing.battery_percent
        ),
        location=detail.address or listing.location,
        description=detail.description or listing.description,
        images=detail.images or listing.images,
    )


class DetailEnricher:
    """Fetch pages for promising listings only, slowly, and remember them."""

    def __init__(
        self,
        store,
        *,
        max_per_cycle: int = 4,
        pause_seconds: float = 4.0,
        cooldown_minutes: int = 15,
        fetch: Callable[[str], str] = fetch_page,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.max_per_cycle = max(1, int(max_per_cycle))
        self.pause_seconds = max(0.0, float(pause_seconds))
        self.cooldown_seconds = max(0, int(cooldown_minutes)) * 60
        self.fetch = fetch
        self.sleep = sleep
        self.clock = clock
        self._used = 0
        self._last_fetch = 0.0
        self._details: dict[str, ItemDetail] = {}

    def start_cycle(self) -> None:
        self._used = 0
        self._details = {}

    def detail_for(self, item_id: str) -> ItemDetail | None:
        """The facts read for this listing during the current cycle."""
        return self._details.get(str(item_id))

    def _cooling_down(self) -> bool:
        raw = self.store.get_metadata(COOLDOWN_METADATA_KEY)
        if not raw:
            return False
        try:
            until = float(raw)
        except ValueError:
            return False
        return self.clock() < until

    def _cool_down(self, error: ItemDetailError) -> None:
        if self.cooldown_seconds:
            self.store.set_metadata(
                COOLDOWN_METADATA_KEY, str(self.clock() + self.cooldown_seconds)
            )
        log_event("detail_rate_limited", error=str(error))

    def __call__(self, listing: ParsedListing) -> ParsedListing:
        cached = self.store.load_item_detail(listing.item_id)
        if cached is not None:
            self._details[listing.item_id] = cached
            return apply_detail(listing, cached)
        if self._cooling_down():
            return listing
        if self._used >= self.max_per_cycle:
            log_event("detail_skipped_budget", item_id=listing.item_id)
            return listing
        pause = self.pause_seconds - (self.clock() - self._last_fetch)
        if self._used and pause > 0:
            self.sleep(pause)
        self._used += 1
        try:
            detail = load_detail(listing, fetch=self.fetch)
        except ItemDetailError as error:
            self._last_fetch = self.clock()
            if error.rate_limited:
                self._cool_down(error)
            else:
                log_event("detail_failed", item_id=listing.item_id, error=str(error))
            return listing
        self._last_fetch = self.clock()
        self._details[listing.item_id] = detail
        self.store.save_item_detail(detail)
        log_event(
            "detail_fetched",
            item_id=listing.item_id,
            condition=detail.condition,
            battery=detail.battery_percent,
            description_chars=len(detail.description),
        )
        return apply_detail(listing, detail)


def probe(listing: ParsedListing, *, fetch: Callable[[str], str] = fetch_page) -> dict:
    """Try every candidate once and report what each one answered."""
    attempts = []
    winner = None
    for name, url in detail_url_candidates(listing):
        attempt = {"name": name, "url": url}
        try:
            html = fetch(url)
        except ItemDetailError as error:
            attempt["error"] = str(error)
            attempt["rate_limited"] = error.rate_limited
        else:
            attempt["length"] = len(html)
            attempt["has_initial_data"] = _INITIAL_DATA_MARKER in html
            attempt["blocked"] = any(marker in html for marker in _BLOCK_MARKERS)
            try:
                detail = parse_item_page(html, listing.item_id)
            except ItemDetailError as error:
                attempt["parse_error"] = str(error)
            else:
                attempt["condition"] = detail.condition
                attempt["battery"] = detail.battery_percent
                attempt["description_chars"] = len(detail.description)
                attempt["param_titles"] = [title for title, _ in detail.params]
                winner = winner or name
        attempts.append(attempt)
    return {"item_id": listing.item_id, "ok": winner, "attempts": attempts}


def _cold_attempt(fetch: Callable[[str], str]) -> dict:
    """Fetch one fixed page before any feed traffic: shows whether the IP may read HTML."""
    attempt = {"name": "cold_html", "url": SAMPLE_ITEM_URL}
    try:
        html = fetch(SAMPLE_ITEM_URL)
    except ItemDetailError as error:
        attempt["error"] = str(error)[:300]
        attempt["rate_limited"] = error.rate_limited
    except Exception as error:  # noqa: BLE001 - the probe only reports
        attempt["error"] = f"{type(error).__name__}: {error}"[:300]
    else:
        attempt["length"] = len(html)
        attempt["has_initial_data"] = _INITIAL_DATA_MARKER in html
        attempt["blocked"] = any(marker in html for marker in _BLOCK_MARKERS)
        try:
            detail = parse_item_page(html, "sample")
        except ItemDetailError as error:
            attempt["parse_error"] = str(error)[:300]
        else:
            attempt["condition"] = detail.condition
            attempt["description_chars"] = len(detail.description)
    return attempt


def run_probe_once(store, source, *, fetch: Callable[[str], str] = fetch_page) -> dict | None:
    """Probe one fixed page and the first listing once, then park the answer.

    The cold attempt runs before the feed is touched, so a block code there
    proves the egress IP itself is refused and not a reaction to burst traffic.
    """
    if store.get_metadata(PROBE_METADATA_KEY):
        return None
    cold = _cold_attempt(fetch)
    try:
        listings = source.fetch()
    except Exception:  # the probe must never break the start of the bot
        listings = []
    if not listings:
        result = {"cold": cold, "item_id": None, "ok": None, "attempts": []}
        store.set_metadata(PROBE_METADATA_KEY, json.dumps(result, ensure_ascii=False))
        return result
    result = probe(listings[0], fetch=fetch)
    result["cold"] = cold
    store.set_metadata(PROBE_METADATA_KEY, json.dumps(result, ensure_ascii=False))
    return result
