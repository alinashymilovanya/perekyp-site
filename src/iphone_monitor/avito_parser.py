"""Parsing and conservative normalization of Avito search cards."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedListing:
    item_id: str
    title: str
    model: str
    storage_gb: int | None
    price_rub: int
    condition: str | None
    battery_percent: int | None
    location: str | None
    description: str
    url: str
    images: tuple[str, ...]


_CONDITIONS = ("Новый", "Отличное", "Хорошее", "Удовлетворительное", "Битый")


def _number(value: str) -> int:
    return int(re.sub(r"[^0-9]", "", value))


def _item_id(raw_id: str) -> str:
    match = re.search(r"(\d+)$", raw_id)
    return match.group(1) if match else raw_id


def _storage(title: str) -> int | None:
    match = re.search(r"(\d+)\s*(ГБ|GB|ТБ|TB)\b", title, flags=re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value * 1024 if match.group(2).lower() in {"тб", "tb"} else value


def _model(title: str) -> str:
    return title.split(",", 1)[0].strip()


def _price(text: str) -> int:
    matches = re.findall(r"(\d[\d \u00a0]*)\s*₽", text)
    if not matches:
        raise ValueError("listing price is missing")
    return _number(matches[-1])


def _condition(text: str) -> str | None:
    for condition in _CONDITIONS:
        if re.search(rf"\b{re.escape(condition)}\b", text, flags=re.IGNORECASE):
            return condition
    return None


def _battery(text: str) -> int | None:
    match = re.search(
        r"(?:акб|аккумулятор|батарея|емкость|ёмкость)[^%\d]{0,30}(\d{2,3})\s*%",
        text,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _location(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if "обл." in line or "край," in line or line in {"Москва", "Санкт-Петербург"}:
            return line
    return None


def parse_card(card: dict) -> ParsedListing:
    title = str(card.get("title") or "").strip()
    text = str(card.get("text") or "")
    return ParsedListing(
        item_id=_item_id(str(card.get("id") or "")),
        title=title,
        model=_model(title),
        storage_gb=_storage(title),
        price_rub=_price(text),
        condition=_condition(text),
        battery_percent=_battery(text),
        location=_location(text),
        description=text,
        url=str(card.get("url") or ""),
        images=tuple(str(image) for image in (card.get("images") or []) if image),
    )
