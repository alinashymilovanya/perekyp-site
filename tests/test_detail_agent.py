import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

TASKS = Path(__file__).resolve().parents[1] / "tasks"
if str(TASKS) not in sys.path:
    sys.path.insert(0, str(TASKS))

import detail_agent  # noqa: E402
from iphone_monitor.item_detail import ItemDetail, ItemDetailError  # noqa: E402


def detail(item_id: str) -> ItemDetail:
    return ItemDetail(
        item_id=item_id,
        title="iPhone 17 Pro, 512 ГБ",
        description="Новый",
        price_rub=81990,
        condition="Новый",
        battery_percent=87,
        storage_gb=512,
        color="Оранжевый",
        sim="eSIM",
        seller_name="Магазин",
        address="Москва",
        published_at="2026-10-30T14:01:00+00:00",
    )


class AgentRoundTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            github_token="t", github_repo="u/r", github_branch="master"
        )
        self.files = {}
        self.calls = []
        self.sleeps = []

        def fetch(url, **_kwargs):
            for path, payload in self.files.items():
                if path in url:
                    return payload
            return None

        self.fetch = fetch
        self.original = detail_agent.load_detail
        self.original_sleep = detail_agent.time.sleep
        detail_agent.time.sleep = lambda seconds: self.sleeps.append(seconds)

    def tearDown(self):
        detail_agent.load_detail = self.original
        detail_agent.time.sleep = self.original_sleep

    def requests(self, *items):
        self.files["site/detail-requests.json"] = {
            "generated_at": 1.0,
            "items": [{"item_id": i, "url": f"https://m.avito.ru/x/{i}", "title": "t"} for i in items],
        }

    def test_reads_only_new_pages(self):
        self.requests("1", "2")
        detail_agent.load_detail = lambda listing: (
            self.calls.append(listing.item_id) or detail(listing.item_id)
        )
        state = {"details": {"1": detail("1").as_payload()}, "blocked_until": 0}
        fetched = detail_agent.run_round(state, self.settings, self.fetch, sleep=lambda s: None)
        self.assertEqual(fetched, 1)
        self.assertEqual(self.calls, ["2"])
        self.assertIn("2", state["details"])
        self.assertEqual(self.sleeps, [])

    def test_rate_limit_stops_the_round(self):
        self.requests("1", "2", "3")

        def failing(listing):
            raise ItemDetailError("HTTP 439", rate_limited=True)

        detail_agent.load_detail = failing
        state = {"details": {}, "blocked_until": 0}
        fetched = detail_agent.run_round(state, self.settings, self.fetch, sleep=lambda s: None)
        self.assertEqual(fetched, 0)
        self.assertGreater(state["blocked_until"], 0)
        self.assertEqual(
            detail_agent.run_round(state, self.settings, self.fetch, sleep=lambda s: None), 0
        )

    def test_ordinary_failure_keeps_going(self):
        self.requests("1", "2")
        seen = []

        def flaky(listing):
            seen.append(listing.item_id)
            if listing.item_id == "1":
                raise ItemDetailError("HTTP 500")
            return detail(listing.item_id)

        detail_agent.load_detail = flaky
        state = {"details": {}, "blocked_until": 0}
        fetched = detail_agent.run_round(state, self.settings, self.fetch, sleep=lambda s: None)
        self.assertEqual(fetched, 1)
        self.assertEqual(seen, ["1", "2"])

    def test_empty_queue_does_nothing(self):
        state = {"details": {}, "blocked_until": 0}
        self.assertEqual(
            detail_agent.run_round(state, self.settings, self.fetch, sleep=lambda s: None), 0
        )

    def test_trim_keeps_newest(self):
        details = {str(index): {} for index in range(10)}
        trimmed = detail_agent._trim(details)
        self.assertEqual(len(trimmed), 10)
        detail_agent.MAX_STORED_DETAILS = 3
        try:
            trimmed = detail_agent._trim(details)
        finally:
            detail_agent.MAX_STORED_DETAILS = 400
        self.assertEqual(list(trimmed), ["7", "8", "9"])


if __name__ == "__main__":
    unittest.main()
