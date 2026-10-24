import unittest
from types import SimpleNamespace

from iphone_monitor.json_api_source import JsonApiSource
from iphone_monitor.source_factory import create_listings_source


class SourceFactoryTests(unittest.TestCase):
    def test_disabled_mode_does_not_create_a_source(self):
        settings = SimpleNamespace(listings_source="disabled")

        self.assertIsNone(create_listings_source(settings))

    def test_json_api_mode_creates_permitted_source(self):
        settings = SimpleNamespace(
            listings_source="json_api",
            listings_source_url="https://provider.example/feed",
            listings_source_token="provider-token",
        )

        source = create_listings_source(settings)

        self.assertIsInstance(source, JsonApiSource)
