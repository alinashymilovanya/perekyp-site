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
from iphone_monitor.browser_detail import BrowserDetailReader  # noqa: E402
from iphone_monitor.config import load_settings  # noqa: E402
from iphone_monitor.detail_relay import (  # noqa: E402
    REQUESTS_PATH,
    RESULTS_PATH,
    contents_url,
    dump,
    fetch_json,
)
from iphone_monitor.github_publish import GitHubPublishError, publish_snapshot  # noqa: E402
from iphone_monitor.instance_lock import InstanceLock  # noqa: E402
from iphone_monitor.item_detail import (  # noqa: E402
    ItemDetailError,
    load_detail,
    looks_like_block_page,
)
from iphone_monitor.telemetry import configure_logging, log_event  # noqa: E402

POLL_SECONDS = 20.0
PAGE_PAUSE_SECONDS = 12.0
HEARTBEAT_SECONDS = 300.0
BLOCK_SLEEP_SECONDS = 600.0
BLOCK_SLEEP_MAX_SECONDS = 10_800.0
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


def _load_published(fetch, repo: str, branch: str, token: str = "") -> dict:
    try:
        payload = fetch(contents_url(repo, RESULTS_PATH, branch), token=token)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    published = payload.get("details")
    if isinstance(published, dict):
        payload = dict(payload)
        payload["details"] = {
            str(item_id): raw
            for item_id, raw in published.items()
            if isinstance(raw, dict)
            and not looks_like_block_page(raw.get("title"), raw.get("description"))
        }
    return payload


def _read_requests(fetch, repo: str, branch: str, token: str = "") -> list[dict]:
    try:
        payload = fetch(contents_url(repo, REQUESTS_PATH, branch), token=token)
    except (OSError, ValueError) as error:
        log_event("detail_agent_requests_unavailable", error=str(error)[:200])
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("item_id")]


def build_reader(settings):
    """Chrome reads the card; plain HTTP stays as the fallback."""
    exe = str(getattr(settings, "browser_act_path", "") or "")
    if exe and Path(exe).exists():
        return BrowserDetailReader(
            exe,
            str(getattr(settings, "avito_session_name", "") or "avito-check"),
            str(getattr(settings, "avito_browser_id", "") or ""),
        )
    return load_detail


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


def run_round(state, settings, fetch, *, sleep=time.sleep, reader=None) -> int:
    """Read up to MAX_PAGES_PER_ROUND requested pages; return how many were read."""
    reader = reader or load_detail
    if state.get("blocked_until", 0) > time.time():
        return 0
    queue = _read_requests(
        fetch, settings.github_repo, settings.github_branch, settings.github_token
    )
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
            detail = reader(listing)
        except ItemDetailError as error:
            blocked = bool(error.rate_limited or getattr(error, "blocked", False))
            log_event(
                "detail_agent_failed",
                item_id=item_id,
                rate_limited=bool(error.rate_limited),
                blocked=bool(getattr(error, "blocked", False)),
                error=str(error)[:200],
            )
            if blocked:
                # Each further block means the address is still banned: back off
                # harder instead of knocking every ten minutes.
                state["blocks"] = int(state.get("blocks", 0)) + 1
                delay = min(
                    BLOCK_SLEEP_SECONDS * (2 ** (state["blocks"] - 1)),
                    BLOCK_SLEEP_MAX_SECONDS,
                )
                state["blocked_until"] = time.time() + delay
                log_event(
                    "detail_agent_blocked",
                    blocks=state["blocks"],
                    sleep_seconds=delay,
                )
                return fetched
            continue
        except Exception as error:  # one bad page must not stop the agent
            log_event("detail_agent_crashed", item_id=item_id, error=str(error)[:200])
            continue
        state["details"][item_id] = detail.as_payload()
        state["blocks"] = 0
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
    published = _load_published(
        fetch_json, settings.github_repo, settings.github_branch, settings.github_token
    )
    details = published.get("details") if isinstance(published.get("details"), dict) else {}
    state = {
        "details": dict(details),
        "published_at": float(published.get("generated_at") or 0),
        "blocked_until": 0.0,
        "blocks": 0,
    }
    reader = build_reader(settings)
    log_event(
        "detail_agent_started",
        repo=settings.github_repo,
        known_details=len(state["details"]),
        reader=type(reader).__name__,
    )
    while True:
        log_event(
            "detail_agent_tick",
            known=len(state["details"]),
            blocked_for=round(max(0.0, state.get("blocked_until", 0) - time.time())),
        )
        try:
            fetched = run_round(state, settings, fetch_json, reader=reader)
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
