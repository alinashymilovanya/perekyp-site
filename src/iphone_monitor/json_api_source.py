"""Adapter for a permitted provider that exposes normalized JSON listings."""

import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .avito_parser import ParsedListing, parse_card


class ListingsSourceError(RuntimeError):
    """The configured permitted listings source could not be read."""


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("LISTINGS_SOURCE_URL must be an HTTPS URL without credentials")


def _fetch_json(url: str, token: str) -> dict | list:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ListingsSourceError("The listings provider request failed") from exc


class JsonApiSource:
    """Fetches a provider response shaped as an array or `{ "items": [...] }`."""

    def __init__(
        self,
        url: str,
        token: str,
        max_listings: int = 50,
        fetch_json: Callable[[str, str], dict | list] | None = None,
    ):
        _validate_url(url)
        self._url = url
        self._token = token
        if max_listings < 1:
            raise ValueError("max_listings must be positive")
        self._max_listings = max_listings
        self._fetch_json = fetch_json or _fetch_json

    def fetch(self) -> tuple[ParsedListing, ...]:
        try:
            payload = self._fetch_json(self._url, self._token)
            items = payload.get("items") if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                raise ValueError("Provider payload must contain an items array")
            return tuple(parse_card(item) for item in items[: self._max_listings])
        except (ListingsSourceError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, ListingsSourceError):
                raise
            raise ListingsSourceError("The listings provider returned invalid data") from exc
