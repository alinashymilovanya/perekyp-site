"""Selects an explicitly configured, permitted listings source."""

from .avito_api_source import AvitoApiSource
from .avito_browser import AvitoBrowserSource, DEFAULT_SEARCH_URL
from .json_api_source import JsonApiSource
from .vision_source import VisionSource


def create_listings_source(settings):
    mode = settings.listings_source.strip().lower()
    if mode == "disabled":
        return None
    if mode == "json_api":
        if not settings.listings_source_url:
            raise ValueError("LISTINGS_SOURCE_URL is required for json_api mode")
        return JsonApiSource(
            settings.listings_source_url,
            settings.listings_source_token,
            max_listings=getattr(settings, "max_listings_per_poll", 50),
        )
    if mode == "avito_api":
        return AvitoApiSource(
            location_id=settings.avito_api_location_id,
            query=settings.avito_api_query,
            max_listings=getattr(settings, "max_listings_per_poll", 50),
            pages=settings.avito_api_pages,
            sort=settings.avito_api_sort,
        )
    if mode == "browser":
        return AvitoBrowserSource(
            browser_act_path=settings.browser_act_path,
            browser_id=getattr(settings, "avito_browser_id", ""),
            session_name=settings.avito_session_name,
            search_url=settings.avito_search_url or DEFAULT_SEARCH_URL,
            max_listings=getattr(settings, "max_listings_per_poll", 50),
        )
    if mode == "vision":
        return VisionSource(
            node_path=settings.vision_node_path,
            playwright_path=settings.vision_playwright_path,
            api_token=settings.vision_api_token,
            folder_id=settings.vision_folder_id,
            profile_id=settings.vision_profile_id,
            search_url=settings.avito_search_url or DEFAULT_SEARCH_URL,
            max_listings=getattr(settings, "max_listings_per_poll", 50),
        )
    raise ValueError(
        "LISTINGS_SOURCE must be disabled, json_api, avito_api, browser, or vision"
    )
