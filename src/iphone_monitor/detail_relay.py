"""Hands page reads off to the home PC: the cloud bot asks, the PC answers.

Avito closes listing pages for datacenter addresses but happily serves them to
a residential connection. The bot therefore publishes the ids it needs into
site/detail-requests.json, the agent on the user's PC reads the pages and
publishes site/detail-results.json, and the bot folds the answer back in.
"""

import json
import time
import urllib.error
import urllib.request

from .github_publish import GitHubPublishError, publish_snapshot
from .item_detail import ItemDetail, ItemDetailError, apply_detail
from .telemetry import log_event

REQUESTS_PATH = "site/detail-requests.json"
RESULTS_PATH = "site/detail-results.json"
RAW_ROOT = "https://raw.githubusercontent.com"
AGENT_ONLINE_SECONDS = 900.0
RESULTS_TTL_SECONDS = 15.0


def raw_url(repo: str, branch: str, path: str, *, stamp: float | None = None) -> str:
    """Address a file in the repository without hitting the API rate limit."""
    suffix = f"?v={int(stamp)}" if stamp is not None else ""
    return f"{RAW_ROOT}/{repo}/{branch}/{path.lstrip('/')}{suffix}"


def fetch_json(url: str, *, timeout: float = 20.0):
    """Read a JSON document, returning None when the file does not exist yet."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "iphone-monitor", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class DetailRelay:
    """Publishes page requests and collects the answers the home agent wrote."""

    def __init__(
        self,
        store,
        *,
        token: str,
        repo: str,
        branch: str = "master",
        fetch=fetch_json,
        publish=publish_snapshot,
        clock=time.time,
        results_ttl: float = RESULTS_TTL_SECONDS,
        timeout: float = 20.0,
    ):
        self.store = store
        self.token = token or ""
        self.repo = repo or ""
        self.branch = branch or "master"
        self._fetch = fetch
        self._publish = publish
        self.clock = clock
        self.results_ttl = results_ttl
        self.timeout = timeout
        self._results: dict = {}
        self._results_at = 0.0
        self._published_items: tuple[tuple[str, str, str], ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.token) and "/" in self.repo

    def results(self, *, force: bool = False) -> dict:
        """Return the agent's answers, re-reading them at most every few seconds."""
        now = self.clock()
        if not force and self._results and now - self._results_at < self.results_ttl:
            return self._results
        try:
            payload = self._fetch(raw_url(self.repo, self.branch, RESULTS_PATH, stamp=now))
        except (urllib.error.URLError, OSError, ValueError) as error:
            log_event("detail_results_unavailable", error=str(error)[:200])
            return self._results
        if isinstance(payload, dict):
            self._results = payload
            self._results_at = now
        return self._results

    def agent_online(self, *, max_age_seconds: float = AGENT_ONLINE_SECONDS) -> bool:
        """True when the home PC checked in recently enough to answer quickly."""
        stamp = self.results().get("generated_at")
        if not isinstance(stamp, (int, float)):
            return False
        return 0 <= self.clock() - float(stamp) <= max_age_seconds

    def publish_requests(self) -> bool:
        """Write the waiting ids to GitHub; skips the write when nothing changed."""
        rows = tuple(self.store.detail_requests(50))
        if not rows or rows == self._published_items:
            return False
        payload = {
            "generated_at": self.clock(),
            "items": [
                {"item_id": item_id, "url": url, "title": title}
                for item_id, url, title in rows
            ],
        }
        try:
            self._publish(
                token=self.token,
                repo=self.repo,
                path=REQUESTS_PATH,
                content=dump(payload),
                message="detail: запрос на чтение карточек",
                branch=self.branch,
            )
        except GitHubPublishError as error:
            log_event("detail_requests_publish_failed", error=str(error)[:200])
            return False
        self._published_items = rows
        log_event("detail_requests_published", items=len(rows))
        return True

    def sync(self) -> list[ItemDetail]:
        """Cache every new answer from the agent and return what arrived."""
        payload = self.results(force=True)
        answers = payload.get("details") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return []
        fresh: list[ItemDetail] = []
        for item_id, raw in answers.items():
            if not isinstance(raw, dict):
                continue
            if self.store.load_item_detail(item_id) is not None:
                continue
            try:
                detail = ItemDetail.from_payload(raw)
            except (TypeError, ValueError, KeyError):
                continue
            self.store.save_item_detail(detail)
            fresh.append(detail)
        if fresh:
            log_event("detail_results_received", count=len(fresh))
        return fresh


class RemoteEnricher:
    """Asks the home agent for a page and waits a short while for the answer."""

    def __init__(
        self,
        relay: DetailRelay,
        store,
        *,
        wait_seconds: float = 40.0,
        poll_seconds: float = 5.0,
        max_waits_per_cycle: int = 2,
        sleep=time.sleep,
        clock=time.time,
    ):
        self.relay = relay
        self.store = store
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.max_waits_per_cycle = max_waits_per_cycle
        self._sleep = sleep
        self._clock = clock
        self._waits = 0
        self._details: dict[str, ItemDetail] = {}

    def start_cycle(self) -> None:
        self._waits = 0
        self._details = {}

    def detail_for(self, item_id):
        if item_id in self._details:
            return self._details[item_id]
        return self.store.load_item_detail(item_id)

    def _wait(self, item_id: str) -> ItemDetail | None:
        deadline = self._clock() + self.wait_seconds
        while self._clock() < deadline:
            self._sleep(self.poll_seconds)
            self.relay.sync()
            detail = self.store.load_item_detail(item_id)
            if detail is not None:
                self._details[item_id] = detail
                return detail
        return None

    def __call__(self, listing):
        cached = self.store.load_item_detail(listing.item_id)
        if cached is not None:
            self._details[listing.item_id] = cached
            return apply_detail(listing, cached)
        if not self.relay.enabled:
            return listing
        self.store.add_detail_request(listing.item_id, listing.url, listing.title)
        self.relay.publish_requests()
        if self._waits >= self.max_waits_per_cycle or not self.relay.agent_online():
            log_event("detail_wait_skipped", item_id=listing.item_id, waits=self._waits)
            return listing
        self._waits += 1
        detail = self._wait(listing.item_id)
        if detail is None:
            log_event("detail_wait_timeout", item_id=listing.item_id)
            return listing
        log_event(
            "detail_received",
            item_id=listing.item_id,
            condition=detail.condition,
            battery=detail.battery_percent,
        )
        return apply_detail(listing, detail)
