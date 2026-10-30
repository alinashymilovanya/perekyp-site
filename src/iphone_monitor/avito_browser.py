"""BrowserAct-backed source for the public Avito search results page."""

import json
import re
import subprocess
from collections.abc import Callable

from .avito_parser import ParsedListing, parse_card

DEFAULT_SEARCH_URL = (
    "https://www.avito.ru/all/telefony/mobilnye_telefony/"
    "apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc"
)


def extraction_script(max_listings: int) -> str:
    if max_listings < 1:
        raise ValueError("max_listings must be positive")
    script = """
JSON.stringify([...document.querySelectorAll('h2 a[title]')].map(a => {
  const card = a.closest('div[id]');
  return {
    id: card?.id || '',
    title: a.title || a.textContent.trim(),
    url: a.href || '',
    text: card?.innerText || '',
    images: [...(card?.querySelectorAll('img') || [])]
      .map(img => img.currentSrc || img.src).filter(Boolean).slice(0, 8)
  };
}).slice(0, __LIMIT__))
""".strip()
    return script.replace("__LIMIT__", str(max_listings))


def parse_cards_json(raw: str) -> tuple[ParsedListing, ...]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ()
    cards = json.loads(lines[-1])
    return tuple(parse_card(card) for card in cards)


BLOCK_MARKERS = (
    "Доступ ограничен",
    "проблема с IP",
    "проверка безопасности",
    "Подтвердите, что вы не робот",
)


class BrowserActError(RuntimeError):
    """Raised when BrowserAct cannot provide a search page."""

    def __init__(self, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked


def looks_blocked(state: str) -> bool:
    return any(marker in state for marker in BLOCK_MARKERS)


class AvitoBrowserSource:
    def __init__(
        self,
        browser_act_path: str,
        session_name: str,
        search_url: str = DEFAULT_SEARCH_URL,
        browser_id: str = "",
        max_listings: int = 50,
        runner: Callable[[list[str]], str] | None = None,
    ):
        self.browser_act_path = browser_act_path
        self.session_name = session_name
        self.search_url = search_url
        self.browser_id = browser_id
        self.max_listings = max_listings
        self._runner = runner or self._run_command
        self._initialized = False

    def _run_command(self, args: list[str]) -> str:
        command = [self.browser_act_path, "--session", self.session_name, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrowserActError("BrowserAct command timed out or could not start") from exc
        if completed.returncode != 0:
            raise BrowserActError("BrowserAct command failed")
        return completed.stdout.strip()

    @staticmethod
    def _index_for_label(state: str, label: str) -> str:
        lines = state.splitlines()
        for line_number, line in enumerate(lines):
            if label not in line:
                continue
            match = re.search(r"\[(\d+)\]", line)
            if match:
                return match.group(1)
            for previous in reversed(lines[max(0, line_number - 3) : line_number]):
                match = re.search(r"\[(\d+)\]", previous)
                if match:
                    return match.group(1)
        raise BrowserActError(f"Could not find page control: {label}")

    def _check_not_blocked(self) -> None:
        state = self._runner(["state"])
        if looks_blocked(state):
            raise BrowserActError(
                "Avito отвечает страницей блокировки IP: нужен российский "
                "домашний IP или прокси, VPN и зарубежные серверы заблокированы",
                blocked=True,
            )

    def _sort_by_date(self) -> None:
        sort_state = self._runner(["state"])
        self._runner(["click", self._index_for_label(sort_state, "Сортировка")])
        menu_state = self._runner(["state"])
        self._runner(["click", self._index_for_label(menu_state, "По дате")])
        self._runner(["wait", "stable", "--timeout", "30000"])

    def fetch(self) -> tuple[ParsedListing, ...]:
        if not self._initialized:
            if self.browser_id:
                self._runner(["browser", "open", self.browser_id, self.search_url])
            else:
                self._runner(["navigate", self.search_url])
            self._initialized = True
        else:
            self._runner(["reload"])
        self._runner(["wait", "stable", "--timeout", "30000"])
        self._check_not_blocked()
        self._sort_by_date()
        raw = self._runner(["eval", extraction_script(self.max_listings)])
        return parse_cards_json(raw)
