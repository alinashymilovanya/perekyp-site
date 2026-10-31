import unittest

from iphone_monitor.avito_api_source import (
    AvitoApiError,
    AvitoApiSource,
    is_iphone_phone,
    parse_api_item,
)


def make_item(**overrides):
    item = {
        "id": 8370068088,
        "title": "iPhone 16, 128 ГБ, SIM + eSIM",
        "priceDetailed": {"string": "49 990 ₽", "value": 0},
        "location": {"id": 637640, "name": ""},
        "urlPath": "/moskva/telefony/iphone_16_128_gb_sim_esim_8370068088?context=abc",
        "images": [
            {
                "140x140": "https://img/140.jpg",
                "278x278": "https://img/278.jpg",
                "678x678": "https://img/678.jpg",
            }
        ],
    }
    item.update(overrides)
    return item


class ParseApiItemTests(unittest.TestCase):
    def test_parses_phone_listing_without_browser(self):
        listing = parse_api_item(make_item())

        self.assertEqual(listing.item_id, "8370068088")
        self.assertEqual(listing.title, "iPhone 16, 128 ГБ, SIM + eSIM")
        self.assertEqual(listing.model, "iPhone 16")
        self.assertEqual(listing.storage_gb, 128)
        self.assertEqual(listing.price_rub, 49990)
        self.assertIsNone(listing.condition)
        self.assertEqual(listing.url, "https://www.avito.ru/moskva/telefony/iphone_16_128_gb_sim_esim_8370068088")
        self.assertEqual(listing.images, ("https://img/678.jpg",))

    def test_price_uses_printed_string_because_value_is_unreliable(self):
        listing = parse_api_item(make_item(priceDetailed={"string": "1 234 567 ₽", "value": 1}))

        self.assertEqual(listing.price_rub, 1234567)

    def test_price_falls_back_to_numeric_value(self):
        listing = parse_api_item(make_item(priceDetailed={"string": "", "value": 51_000}))

        self.assertEqual(listing.price_rub, 51000)

    def test_city_comes_from_url_slug_when_location_name_is_empty(self):
        listing = parse_api_item(make_item(location={"id": 621630, "name": ""}, urlPath="/barnaul/telefony/x_1?context=2"))

        self.assertEqual(listing.location, "Барнаул")

    def test_unknown_slug_is_still_readable(self):
        listing = parse_api_item(make_item(urlPath="/novyy-urengoy/telefony/x_1"))

        self.assertEqual(listing.location, "novyy urengoy")

    def test_accessories_are_skipped(self):
        self.assertIsNone(parse_api_item(make_item(title="iPhone 15 Pro Silicone Case Light Pink")))
        self.assertIsNone(parse_api_item(make_item(title="Чехол для iPhone 14")))
        self.assertIsNone(parse_api_item(make_item(title="Apple Watch Series 11")))

    def test_non_iphone_items_are_skipped(self):
        self.assertIsNone(parse_api_item(make_item(title="Samsung Galaxy S24, 256 ГБ")))

    def test_items_without_price_are_skipped(self):
        self.assertIsNone(parse_api_item(make_item(priceDetailed={"string": "", "value": 0})))

    def test_missing_id_is_skipped(self):
        self.assertIsNone(parse_api_item(make_item(id=None, title="iPhone 13")))

    def test_tb_storage_is_converted_to_gb(self):
        listing = parse_api_item(make_item(title="iPhone 15 Pro, 1 ТБ, eSIM"))

        self.assertEqual(listing.storage_gb, 1024)


class IsIphonePhoneTests(unittest.TestCase):
    def test_matches_plain_titles(self):
        self.assertTrue(is_iphone_phone("iPhone 12, 64 ГБ, SIM + eSIM"))
        self.assertTrue(is_iphone_phone("  apple iPhone 11"))

    def test_rejects_accessory_words(self):
        self.assertFalse(is_iphone_phone("iPhone 14 плёнка"))
        self.assertFalse(is_iphone_phone("Ремешок iPhone"))


class AvitoApiSourceTests(unittest.TestCase):
    def test_url_contains_the_documented_query_parameters(self):
        source = AvitoApiSource(location_id="107620", query="iphone", max_listings=50)

        url = source.url_for_page(2)

        self.assertIn("locationId=107620", url)
        self.assertIn("categoryId=84", url)
        self.assertIn("limit=50", url)
        self.assertIn("page=2", url)
        self.assertIn("query=iphone", url)

    def test_fetch_parses_every_page(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if "page=1" in url:
                return {"items": [make_item()]}
            return {"items": [make_item(id=1, title="iPhone 13, 128 ГБ, eSIM")]}

        source = AvitoApiSource(location_id="107620", max_listings=2, pages=2, fetch_json=fake_fetch)
        listings = source.fetch()

        self.assertEqual([listing.item_id for listing in listings], ["8370068088", "1"])
        self.assertEqual(len(calls), 2)

    def test_for_page_shifts_the_first_requested_page(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return {"items": [make_item()]}

        source = AvitoApiSource(max_listings=5, pages=1, fetch_json=fake_fetch, start_page=3)
        source.fetch()

        self.assertEqual(len(calls), 1)
        self.assertIn("page=3", calls[0])

    def test_for_page_does_not_mutate_the_original_source(self):
        source = AvitoApiSource(max_listings=5)

        shifted = source.for_page(4)

        self.assertEqual(source.start_page, 1)
        self.assertEqual(shifted.start_page, 4)
        self.assertEqual(shifted.max_listings, 5)

    def test_fetch_stops_after_enough_listings(self):
        def fake_fetch(url):
            return {"items": [make_item(), make_item(id=2, title="iPhone 14, 256 ГБ")]}

        source = AvitoApiSource(max_listings=1, pages=3, fetch_json=fake_fetch)

        self.assertEqual(len(source.fetch()), 2)

    def test_unexpected_payload_raises(self):
        source = AvitoApiSource(fetch_json=lambda url: {"items": "nope"})

        with self.assertRaises(AvitoApiError):
            source.fetch()

    def test_rate_limit_error_is_marked(self):
        error = AvitoApiError("slow down", rate_limited=True)

        self.assertTrue(error.rate_limited)


if __name__ == "__main__":
    unittest.main()
