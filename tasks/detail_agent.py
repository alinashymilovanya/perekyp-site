"""Home agent: reads the Avito pages that the cloud bot is not allowed to open.

The bot publishes the ids it wants into site/detail-requests.json. This script
runs on the user's PC (residential address), reads every requested page with
the same parser the bot uses, and publishes site/detail-results.json back.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iphone_monitor.avito_parser import ParsedListing  # noqa: E402
from iphone_monitor.config import load_settings  # noqa: E402
from iphone_monitor.detail_relay import (  # noqa: E402
    REQUESTS_PATH,
    RESULTS_PATH,
    dump,
    fetch_json,
    raw_url,
)
from iphone_monitor.github_publish import GitHubPublishError, publish_snapshot  # noqa: E402
from iphone_monitor.instance_lock import InstanceLock  # noqa: E402
from iphone_monitor.item_detail import ItemDetailError, load_detail  # noqa: E402
from iphone_monitor.telemetry import configure_logging, log_event  # noqa: E402

POLL_SECONDS = 20.0
PAGE_PAUSE_SECONDS = 12.0
HEARTBEAT_SECONDS = 300.0
BLOCK_SLEEP_SECONDS = 600.0
MAX_PAGES_PER_ROUND = 8
MAX_STORED_DETAILS = 400


def _stub(item_id: str, url: str, title: str) -> ParsedListing:
    """Only the address matters: load_detail reads the page by its URL."""
    return ParsedListing(
        item_id=str(item_id),
        title=title or "",
        model="",
        storage_gb=None,
        price_rub=0,
        condition=None,
        battery_percent=None,
        location=None,
        description="",
        url=url,
        images=(),
    )


def _load_published(fetch, repo: str, branch: str) -> dict:
    try:
        payload = fetch(raw_url(repo, branch, RESULTS_PATH, stamp=time.time()))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_requests(fetch, repo: str, branch: str) -> list[dict]:
    try:
        payload = fetch(raw_url(repo, branch, REQUESTS_PATH, stamp=time.time()))
    except (OSError, ValueError) as error:
        log_event("detail_agent_requests_unavailable", error=str(error)[:200])
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("item_id")]


def _trim(details: dict) -> dict:
    """Keep the newest entries: a dict keeps its insertion order."""
    if len(details) <= MAX_STORED_DETAILS:
        return details
    kept = list(details)[-MAX_STORED_DETAILS:]
    return {item_id: details[item_id] for item_id in kept}


def _publish(state, settings, *, message: str) -> bool:
    payload = {
        "generated_at": time.time(),
        "agent": "home-pc",
        "details": _trim(state["details"]),
    }
    try:
        publish_snapshot(
            token=settings.github_token,
            repo=settings.github_repo,
            path=RESULTS_PATH,
            content=dump(payload),
            message=message,
            branch=settings.github_branch,
        )
    except GitHubPublishError as error:
        log_event("detail_agent_publish_failed", error=str(error)[:300])
        return False
    state["published_at"] = payload["generated_at"]
    log_event("detail_agent_published", details=len(payload["details"]))
    return True


def run_round(state, settings, fetch, *, sleep=time.sleep) -> int:
    """Read up to MAX_PAGES_PER_ROUND requested pages; return how many were read."""
    if state.get("blocked_until", 0) > time.time():
        return 0
    queue = _read_requests(fetch, settings.github_repo, settings.github_branch)
    if not queue:
        return 0
    fetched = 0
    for item in queue:
        if fetched >= MAX_PAGES_PER_ROUND:
            break
        item_id = str(item["item_id"])
        if item_id in state["details"]:
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        listing = _stub(item_id, url, str(item.get("title") or ""))
        try:
            detail = load_detail(listing)
        except ItemDetailError as error:
            log_event(
                "detail_agent_failed",
                item_id=item_id,
                rate_limited=bool(error.rate_limited),
                error=str(error)[:200],
            )
            if error.rate_limited:
                state["blocked_until"] = time.time() + BLOCK_SLEEP_SECONDS
                return fetched
            continue
        except Exception as error:  # one bad page must not stop the agent
            log_event("detail_agent_crashed", item_id=item_id, error=str(error)[:200])
            continue
        state["details"][item_id] = detail.as_payload()
        fetched += 1
        log_event(
            "detail_agent_fetched",
            item_id=item_id,
            condition=detail.condition,
            battery=detail.battery_percent,
        )
        sleep(PAGE_PAUSE_SECONDS)
    return fetched


def main() -> None:
    settings = load_settings()
    configure_logging(str(ROOT / "data" / "detail-agent.log"))
    lock = InstanceLock(ROOT / "data" / "detail-agent.lock")
    if not lock.acquire():
        log_event("detail_agent_already_running", lock_path=str(lock.path))
        return
    if not settings.github_token or "/" not in settings.github_repo:
        log_event("detail_agent_disabled", reason="нет GITHUB_TOKEN или GITHUB_REPO")
        return
    published = _load_published(fetch_json, settings.github_repo, settings.github_branch)
    details = published.get("details") if isinstance(published.get("details"), dict) else {}
    state = {
        "details": dict(details),
        "published_at": float(published.get("generated_at") or 0),
        "blocked_until": 0.0,
    }
    log_event("detail_agent_started", repo=settings.github_repo, known_details=len(state["details"]))
    while True:
        try:
            fetched = run_round(state, settings, fetch_json)
        except Exception as error:  # the agent must survive anything
            log_event("detail_agent_round_failed", error=str(error)[:300])
            fetched = 0
        due = time.time() - state["published_at"] >= HEARTBEAT_SECONDS
        if fetched or (due and state.get("blocked_until", 0) <= time.time()):
            _publish(
                state,
                settings,
                message=(
                    f"detail: {fetched} новых карточек"
                    if fetched
                    else "detail: агент на связи"
                ),
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
