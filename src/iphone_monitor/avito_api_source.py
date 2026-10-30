"""Source backed by Avito's public web JSON endpoint (works without a browser)."""

import json
import re
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .avito_parser import ParsedListing, normalize_model

API_URL = "https://www.avito.ru/web/1/main/items"
APP_USER_AGENT = "Avito/121.0 (Android 13; Pixel 6)"
PHONE_CATEGORY_ID = "84"

_PHONE_TITLE = re.compile(r"^\s*(?:apple\s+)?iphone\b", re.IGNORECASE)
_ACCESSORY_TITLE = re.compile(
    r"чехол|плёнк|пленк|стекл|кабел|наушник|airpods|watch|case|cover|ремешок",
    re.IGNORECASE,
)
_STORAGE = re.compile(r"(\d+)\s*(ГБ|GB|ТБ|TB)\b", re.IGNORECASE)
_PRICE = re.compile(r"(\d[\d\s\u00a0]*)₽")
_CITY_SLUGS = {
    "moskva": "Москва",
    "sankt-peterburg": "Санкт-Петербург",
    "novosibirsk": "Новосибирск",
    "ekaterinburg": "Екатеринбург",
    "kazan": "Казань",
    "nizhniy_novgorod": "Нижний Новгород",
    "chelyabinsk": "Челябинск",
    "omsk": "Омск",
    "samara": "Самара",
    "rostov-na-donu": "Ростов-на-Дону",
    "ufa": "Уфа",
    "krasnoyarsk": "Красноярск",
    "voronezh": "Воронеж",
    "perm": "Пермь",
    "volgograd": "Волгоград",
    "krasnodar": "Краснодар",
    "saratov": "Саратов",
    "tyumen": "Тюмень",
    "tolyatti": "Тольятти",
    "izhevsk": "Ижевск",
    "barnaul": "Барнаул",
    "ulyanovsk": "Ульяновск",
    "irkutsk": "Иркутск",
    "khabarovsk": "Хабаровск",
    "yaroslavl": "Ярославль",
    "vladivostok": "Владивосток",
    "makhachkala": "Махачкала",
    "tomsk": "Томск",
    "orenburg": "Оренбург",
    "kemerovo": "Кемерово",
    "novokuznetsk": "Новокузнецк",
    "ryazan": "Рязань",
    "astrahan": "Астрахань",
    "astrakhan": "Астрахань",
    "penza": "Пенза",
    "lipetsk": "Липецк",
    "kirov": "Киров",
    "cheboksary": "Чебоксары",
    "tula": "Тула",
    "kaliningrad": "Калининград",
    "sochi": "Сочи",
    "stavropol": "Ставрополь",
    "belgorod": "Белгород",
    "surgut": "Сургут",
}

_SIZE = re.compile(r"(\d+)x(\d+)")


class AvitoApiError(RuntimeError):
    """The Avito JSON endpoint could not be read."""

    def __init__(self, message: str, *, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


def _largest_image(images) -> str:
    if not isinstance(images, list) or not images:
        return ""
    first = images[0]
    if isinstance(first, str):
        return first
    if not isinstance(first, dict):
        return ""
    best = ""
    best_size = -1
    for key, value in first.items():
        match = _SIZE.search(str(key))
        size = int(match.group(1)) if match else 0
        if value and size > best_size:
            best, best_size = str(value), size
    return best


def _price_rub(price_detailed) -> int | None:
    if not isinstance(price_detailed, dict):
        return None
    text = str(price_detailed.get("string") or "")
    match = _PRICE.search(text)
    if match:
        return int(re.sub(r"[^0-9]", "", match.group(1)))
    value = price_detailed.get("value")
    if isinstance(value, int) and value > 0:
        return value
    return None


def _storage(title: str) -> int | None:
    match = _STORAGE.search(title)
    if not match:
        return None
    value = int(match.group(1))
    return value * 1024 if match.group(2).lower() in {"тб", "tb"} else value


def is_iphone_phone(title: str) -> bool:
    return bool(_PHONE_TITLE.search(title)) and not _ACCESSORY_TITLE.search(title)


def parse_api_item(item: dict) -> ParsedListing | None:
    if not isinstance(item, dict):
        return None
    item_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    price = _price_rub(item.get("priceDetailed"))
    if not item_id or not title or price is None or not is_iphone_phone(title):
        return None
    location = item.get("location") or {}
    location_name = str(location.get("name") or "").strip() if isinstance(location, dict) else ""
    url_path = str(item.get("urlPath") or "").split("?", 1)[0]
    if not location_name:
        slug = url_path.strip("/").split("/", 1)[0]
        location_name = _CITY_SLUGS.get(slug, slug.replace("_", " ").replace("-", " "))
    image = _largest_image(item.get("images"))
    return ParsedListing(
        item_id=item_id,
        title=title,
        model=normalize_model(title),
        storage_gb=_storage(title),
        price_rub=price,
        condition=None,
        battery_percent=None,
        location=location_name or None,
        description=title,
        url=f"https://www.avito.ru{url_path}" if url_path else "",
        images=(image,) if image else (),
    )


def _fetch_json(url: str) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": APP_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {429, 439}:
            raise AvitoApiError("Avito временно ограничил частоту запросов", rate_limited=True) from exc
        raise AvitoApiError(f"Avito вернул HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AvitoApiError("Не удалось подключиться к Avito") from exc
    except json.JSONDecodeError as exc:
        raise AvitoApiError("Avito вернул не JSON") from exc


class AvitoApiSource:
    """Reads the same public listing feed the Avito web client uses."""

    def __init__(
        self,
        location_id: str = "107620",
        query: str = "",
        max_listings: int = 50,
        pages: int = 1,
        sort: str = "date",
        fetch_json: Callable[[str], dict] | None = None,
        start_page: int = 1,
    ):
        if max_listings < 1:
            raise ValueError("max_listings must be positive")
        if pages < 1:
            raise ValueError("pages must be positive")
        if start_page < 1:
            raise ValueError("start_page must be positive")
        self.location_id = str(location_id)
        self.query = query
        self.max_listings = max_listings
        self.pages = pages
        self.sort = sort
        self.start_page = start_page
        self._fetch_json = fetch_json or _fetch_json

    def for_query(self, query: str) -> "AvitoApiSource":
        """Return a copy of this source that searches another phrase."""
        return AvitoApiSource(
            location_id=self.location_id,
            query=query,
            max_listings=self.max_listings,
            pages=self.pages,
            sort=self.sort,
            fetch_json=self._fetch_json,
            start_page=self.start_page,
        )

    def for_page(self, page: int) -> "AvitoApiSource":
        """Return a copy of this source that starts reading from another page."""
        if page < 1:
            raise ValueError("page must be positive")
        return AvitoApiSource(
            location_id=self.location_id,
            query=self.query,
            max_listings=self.max_listings,
            pages=self.pages,
            sort=self.sort,
            fetch_json=self._fetch_json,
            start_page=page,
        )

    def url_for_page(self, page: int) -> str:
        params = {
            "locationId": self.location_id,
            "categoryId": PHONE_CATEGORY_ID,
            "limit": str(self.max_listings),
            "page": str(page),
        }
        if self.query:
            params["query"] = self.query
        if self.sort:
            params["sort"] = self.sort
        return f"{API_URL}?{urlencode(params)}"

    def fetch(self) -> tuple[ParsedListing, ...]:
        listings: list[ParsedListing] = []
        for page in range(self.start_page, self.start_page + self.pages):
            payload = self._fetch_json(self.url_for_page(page))
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise AvitoApiError("Avito вернул неожиданный формат данных")
            for item in items:
                parsed = parse_api_item(item)
                if parsed is not None:
                    listings.append(parsed)
            if len(listings) >= self.max_listings:
                break
        return tuple(listings)
