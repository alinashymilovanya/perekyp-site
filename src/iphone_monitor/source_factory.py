"""Selects an explicitly configured, permitted listings source."""

from .avito_browser import AvitoBrowserSource, DEFAULT_SEARCH_URL
from .json_api_source import JsonApiSource


def create_listings_source(settings):
    mode = settings.listings_source.strip().lower()
    if mode == "disabled":
        return None
    if mode == "json_api":
        if not settings.listings_source_url:
            raise ValueError("LISTINGS_SOURCE_URL is required for json_api mode")
        return JsonApiSource(settings.listings_source_url, settings.listings_source_token)
    if mode == "browser":
        return AvitoBrowserSource(
            browser_act_path=settings.browser_act_path,
            session_name=settings.avito_session_name,
            search_url=settings.avito_search_url or DEFAULT_SEARCH_URL,
        )
    raise ValueError("LISTINGS_SOURCE must be disabled, json_api, or browser")
