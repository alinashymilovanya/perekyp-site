"""Read a listing card in the user's real Chrome through the BrowserAct CLI.

Raw HTTP from the home address is answered with HTTP 439 (the Avito firewall
blocks datacenter *and* home addresses for HTML pages), but the same address in
a real Chrome window opens the card fine. The agent therefore asks the browser
for the DOM instead of downloading HTML, and parses the usual data-markers.
"""

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from .avito_browser import looks_blocked
from .item_detail import (
    ItemDetail,
    ItemDetailError,
    _battery_percent,
    _lookup,
    _normalize_condition,
    _storage_gb,
    _BATTERY_TITLES,
    _COLOR_TITLES,
    _SIM_TITLES,
    _STORAGE_TITLES,
)

CONDITION_TITLES = {"состояние"}
SELLER_MARKER = "seller-info/name"


def detail_script() -> str:
    """Collect the card fields from the DOM of an Avito listing page."""
    return r"""
(function(){
  function text(marker){
    var node = document.querySelector('[data-marker="' + marker + '"]');
    return node ? (node.innerText || '').replace(/\s+\n/g, '\n').trim() : '';
  }
  function rows(marker){
    var box = document.querySelector('[data-marker="' + marker + '"]');
    if (!box) return [];
    return Array.from(box.querySelectorAll('li')).map(function(li){
      var spans = li.querySelectorAll('span');
      var title = spans.length ? spans[0].textContent.replace(/:\s*$/, '').trim() : '';
      var whole = (li.innerText || '').replace(/\s+/g, ' ').trim();
      var value = whole.indexOf(title) === 0
        ? whole.slice(title.length).replace(/^[:\s]+/, '')
        : whole;
      return [title, value];
    }).filter(function(pair){ return pair[0]; });
  }
  function specs(){
    var out = [];
    Array.from(document.querySelectorAll('h2')).forEach(function(head){
      if ((head.innerText || '').trim() !== 'Характеристики') return;
      var box = head.parentElement;
      if (!box) return;
      Array.from(box.querySelectorAll('li')).forEach(function(li){
        var line = (li.innerText || '').replace(/\s+/g, ' ').trim();
        var at = line.indexOf(':');
        if (at > 0) out.push([line.slice(0, at).trim(), line.slice(at + 1).trim()]);
      });
    });
    return out;
  }
  return JSON.stringify({
    title: text('item-view/title-info') || ((document.querySelector('h1') || {}).innerText || ''),
    params: rows('item-view/item-params'),
    specs: specs(),
    description: text('item-view/item-description'),
    price: text('item-view/item-price'),
    date: text('item-view/item-date'),
    item_id: text('item-view/item-id'),
    seller: text('seller-info/name'),
    page: (document.body ? document.body.innerText : '').slice(0, 400)
  });
})()
""".strip()


_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}


def _published_at(text: str, *, now: datetime | None = None) -> str:
    """'· сегодня в 15:14' -> ISO time; unknown wording gives an empty string."""
    cleaned = str(text or "").replace("\u00a0", " ")
    match = re.search(r"(\d{1,2}):(\d{2})", cleaned)
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    moment = now or datetime.now()
    day = None
    lowered = cleaned.lower()
    if "сегодня" in lowered:
        day = moment.date()
    elif "вчера" in lowered:
        day = moment.date() - timedelta(days=1)
    else:
        found = re.search(r"(\d{1,2})\s+([а-яё]+)", lowered)
        if found:
            month = _MONTHS.get(found.group(2))
            if month:
                year = moment.year
                candidate = datetime(year, month, int(found.group(1)), hour, minute)
                if candidate > moment:
                    candidate = datetime(year - 1, month, int(found.group(1)), hour, minute)
                return candidate.isoformat(timespec="seconds")
        return ""
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute
    ).isoformat(timespec="seconds")


def _price(text: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(text or ""))
    return int(digits) if digits else None


def _clean(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def parse_browser_payload(raw: str, item_id: str, *, now: datetime | None = None) -> ItemDetail:
    """Turn the JSON produced by detail_script() into an ItemDetail."""
    try:
        data = json.loads(str(raw or "").strip())
    except ValueError as error:
        raise ItemDetailError(f"browser returned non-JSON for {item_id}") from error
    if not isinstance(data, dict):
        raise ItemDetailError(f"browser returned no card for {item_id}")
    params = tuple(
        (str(pair[0]).strip(), str(pair[1]).strip())
        for pair in data.get("params") or ()
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    )
    specs = tuple(
        (str(pair[0]).strip(), str(pair[1]).strip())
        for pair in data.get("specs") or ()
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    )
    description = _clean(data.get("description") or "")
    title = _clean(data.get("title") or "")
    if not title and not description and not params:
        raise ItemDetailError(f"empty card for {item_id}: {str(data.get('page') or '')[:120]}")
    condition = _normalize_condition(_lookup(params, CONDITION_TITLES))
    battery = _battery_percent(_lookup(params, _BATTERY_TITLES))
    return ItemDetail(
        item_id=str(item_id),
        title=title,
        description=description,
        price_rub=_price(data.get("price") or ""),
        condition=condition,
        battery_percent=battery,
        storage_gb=_storage_gb(_lookup(specs, _STORAGE_TITLES)),
        color=_lookup(specs, _COLOR_TITLES),
        sim=_lookup(specs, _SIM_TITLES),
        seller_name=_clean(data.get("seller") or ""),
        address="",
        published_at=_published_at(data.get("date") or "", now=now),
        images=(),
        params=params + specs,
    )


class BrowserDetailReader:
    """Opens the card in the user's Chrome and reads the DOM."""

    def __init__(
        self,
        browser_act_path: str,
        session_name: str,
        browser_id: str = "",
        *,
        runner: Callable[[list[str]], str] | None = None,
        timeout: int = 90,
        pause_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.browser_act_path = browser_act_path
        self.session_name = session_name
        self.browser_id = browser_id
        self._runner = runner or self._run_command
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self._sleep = sleep
        self._opened = False

    def _run_command(self, args: list[str]) -> str:
        command = [self.browser_act_path, "--session", self.session_name, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ItemDetailError(f"browser-act could not start: {error}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:200]
            raise ItemDetailError(f"browser-act failed: {detail}")
        return (completed.stdout or "").strip()

    def __call__(self, listing) -> ItemDetail:
        url = str(getattr(listing, "url", "") or "")
        if not url:
            raise ItemDetailError("listing has no url")
        item_id = str(getattr(listing, "item_id", "") or "")
        if self.browser_id:
            self._runner(["browser", "open", self.browser_id, url])
        else:
            self._runner(["navigate", url])
        self._opened = True
        self._runner(["wait", "stable", "--timeout", "30000"])
        raw = self._runner(["eval", detail_script()])
        try:
            detail = parse_browser_payload(raw, item_id)
        except ItemDetailError:
            state = self._state()
            if looks_blocked(state + raw):
                raise ItemDetailError(
                    "Avito показал страницу блокировки вместо карточки", blocked=True
                )
            raise
        self._sleep(self.pause_seconds)
        return detail

    def _state(self) -> str:
        try:
            return self._runner(["state"])
        except ItemDetailError:
            return ""
