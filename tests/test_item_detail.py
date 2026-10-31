import json
import tempfile
import unittest
from pathlib import Path

from iphone_monitor.item_detail import candidate_urls, probe, run_probe_once
from iphone_monitor.store import SeenStore


class FakeListing:
    def __init__(self, item_id, url):
        self.item_id = item_id
        self.url = url


class FakeSource:
    def __init__(self, listings):
        self.listings = listings
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return list(self.listings)


def make_fetcher(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        for needle, response in responses.items():
            if needle in url:
                return response
        return (404, "text/html", "nope")

    fetch.calls = calls
    return fetch


class ProbeTests(unittest.TestCase):
    def test_candidates_cover_json_and_html(self):
        urls = candidate_urls("123", "/moskva/telefony/iphone_13_128_gb-123?context=1")

        names = [name for name, _ in urls]
        self.assertEqual(names, ["web1_item", "web1_items", "html_page", "mobile_api"])
        html = dict(urls)["html_page"]
        self.assertEqual(html, "https://www.avito.ru/moskva/telefony/iphone_13_128_gb-123")

    def test_candidate_urls_skip_html_without_path(self):
        self.assertNotIn("html_page", [name for name, _ in candidate_urls("1", "")])

    def test_probe_reports_first_success_and_all_attempts(self):
        fetcher = make_fetcher(
            {
                "/web/1/item/": (200, "application/json", '{"description": "АКБ 87%"}'),
                "/web/1/items/": (404, "text/html", "no"),
            }
        )

        result = probe("55", "/x/y-55", fetch_raw=fetcher, pause_seconds=0)

        self.assertEqual(result["ok"], "web1_item")
        self.assertEqual([item["name"] for item in result["attempts"]][:2], ["web1_item", "web1_items"])
        self.assertTrue(result["attempts"][0]["has_description"])
        self.assertEqual((result["attempts"][1]["status"]), 404)

    def test_probe_marks_failure_when_nothing_answers(self):
        fetcher = make_fetcher({})

        result = probe("56", "/x/y-56", fetch_raw=fetcher, pause_seconds=0)

        self.assertIsNone(result["ok"])
        self.assertEqual(len(result["attempts"]), 4)


class RunProbeOnceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = SeenStore(Path(self.dir.name) / "db.sqlite3")
        self.addCleanup(self.store.close)

    def test_probe_runs_once_and_is_stored(self):
        source = FakeSource([FakeListing("77", "/a/b-77")])
        fetcher = make_fetcher({"/web/1/item/77/": (200, "application/json", "{}")})

        first = run_probe_once(self.store, source, fetch_raw=fetcher, pause_seconds=0)
        second = run_probe_once(self.store, source, fetch_raw=fetcher, pause_seconds=0)

        self.assertEqual(first["ok"], "web1_item")
        self.assertIsNone(second)
        self.assertEqual(source.calls, 1)
        stored = json.loads(self.store.get_metadata("detail_probe_v1"))
        self.assertEqual(stored["item_id"], "77")

    def test_no_listings_leaves_probe_unset(self):
        source = FakeSource([])

        self.assertIsNone(run_probe_once(self.store, source, fetch_raw=make_fetcher({}), pause_seconds=0))
        self.assertIsNone(self.store.get_metadata("detail_probe_v1"))


if __name__ == "__main__":
    unittest.main()
