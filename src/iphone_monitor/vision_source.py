"""Vision Antidetect source using its local API and DevTools connection."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from .avito_parser import ParsedListing, parse_card
from .json_api_source import ListingsSourceError


class VisionSource:
    def __init__(
        self,
        *,
        node_path: str,
        playwright_path: str,
        api_token: str,
        folder_id: str,
        profile_id: str,
        search_url: str,
        max_listings: int = 50,
        runner: Callable[[], str] | None = None,
    ):
        if not api_token or not folder_id or not profile_id:
            raise ValueError("Vision API token, folder ID and profile ID are required")
        if max_listings < 1:
            raise ValueError("max_listings must be positive")
        self._node_path = node_path
        self._playwright_path = playwright_path
        self._api_token = api_token
        self._folder_id = folder_id
        self._profile_id = profile_id
        self._search_url = search_url
        self._max_listings = max_listings
        self._runner = runner or self._run_helper

    @property
    def _helper_path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "tasks" / "vision_fetch.mjs"

    def _run_helper(self) -> str:
        environment = os.environ.copy()
        environment.update(
            {
                "VISION_API_TOKEN": self._api_token,
                "VISION_FOLDER_ID": self._folder_id,
                "VISION_PROFILE_ID": self._profile_id,
                "VISION_SEARCH_URL": self._search_url,
                "VISION_MAX_LISTINGS": str(self._max_listings),
                "VISION_PLAYWRIGHT_PATH": self._playwright_path,
            }
        )
        try:
            completed = subprocess.run(
                [self._node_path, str(self._helper_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ListingsSourceError("Vision helper could not be started") from exc
        if completed.returncode != 0:
            raise ListingsSourceError("Vision profile could not provide listings")
        return completed.stdout.strip()

    def fetch(self) -> tuple[ParsedListing, ...]:
        try:
            cards = json.loads(self._runner() or "[]")
            if not isinstance(cards, list):
                raise ValueError("Vision helper payload must be an array")
            return tuple(parse_card(card) for card in cards[: self._max_listings])
        except (ListingsSourceError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if isinstance(exc, ListingsSourceError):
                raise
            raise ListingsSourceError("Vision returned invalid listing data") from exc
