import json
import unittest

from iphone_monitor.avito_parser import ParsedListing
from iphone_monitor.detail_relay import (
    DetailRelay,
    RemoteEnricher,
    contents_url,
    dump,
    raw_url,
)
from iphone_monitor.formatter import format_detail_addendum
from iphone_monitor.item_detail import ItemDetail, apply_detail
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore


def detail_payload(item_id: str = "8372561555", **overrides) -> dict:
    payload = {
        "item_id": item_id,
        "title": "iPhone 17 Pro, 512 ГБ, eSIM",
        "description": "Телефон новый, не активирован.",
        "price_rub": 81990,
        "condition": "Новый",
        "battery_percent": 87,
        "storage_gb": 512,
        "color": "Оранжевый",
        "sim": "Только eSIM",
        "seller_name": "Магазин Техно",
        "address": "Москва, Багратионовский пр., 7к20А",
        "published_at": "2026-10-30T14:01:00+00:00",
        "images": ["big.jpg"],
        "params": [["Состояние", "Новое"]],
    }
    payload.update(overrides)
    return payload


def listing(**overrides) -> ParsedListing:
    base = ParsedListing(
        item_id="8372561555",
        title="iPhone 17 Pro, 512 ГБ, eSIM",
        model="iphone 17 pro",
        storage_gb=512,
        price_rub=81990,
        condition=None,
        battery_percent=None,
        location=None,
        description="",
        url="https://www.avito.ru/moskva/telefony/iphone_17_pro_512_gb_sim_esim_8372561555",
        images=(),
    )
    return overrides and ParsedListing(**{**base.__dict__, **overrides}) or base


class FakeFetch:
    """Answers GitHub file requests from a dict of paths."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.urls = []
        self.tokens = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        self.tokens.append(kwargs.get("token"))
        for path, payload in self.files.items():
            if url.endswith(path) or path in url:
                return payload
        return None


class FakePublish:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            from iphone_monitor.github_publish import GitHubPublishError

            raise GitHubPublishError("boom")
        return {"ok": True}


class RelayTests(unittest.TestCase):
    def make_relay(self, files=None, store=None, publish=None, clock=None):
        self.store = store or SeenStore(":memory:")
        self.publish = publish or FakePublish()
        self.fetch = FakeFetch(files)
        self.now = 1_000_000.0
        relay = DetailRelay(
            self.store,
            token="t",
            repo="user/repo",
            fetch=self.fetch,
            publish=self.publish,
            clock=clock or (lambda: self.now),
        )
        return relay

    def test_disabled_without_token_or_repo(self):
        self.assertFalse(DetailRelay(SeenStore(":memory:"), token="", repo="u/r").enabled)
        self.assertFalse(DetailRelay(SeenStore(":memory:"), token="t", repo="repo").enabled)
        self.assertTrue(DetailRelay(SeenStore(":memory:"), token="t", repo="u/r").enabled)

    def test_raw_url_avoids_api_and_caches(self):
        self.assertEqual(
            raw_url("u/r", "master", "site/x.json", stamp=12.7),
            "https://raw.githubusercontent.com/u/r/master/site/x.json?v=12",
        )

    def test_contents_url_targets_the_api(self):
        self.assertEqual(
            contents_url("u/r", "/site/x.json", "master"),
            "https://api.github.com/repos/u/r/contents/site/x.json?ref=master",
        )

    def test_results_are_read_through_the_api_with_the_token(self):
        relay = self.make_relay({"site/detail-results.json": {"generated_at": 1.0, "details": {}}})
        relay.results(force=True)
        self.assertEqual(
            self.fetch.urls[0],
            "https://api.github.com/repos/user/repo/contents/site/detail-results.json?ref=master",
        )
        self.assertEqual(self.fetch.tokens[0], "t")

    def test_publish_requests_only_when_set_changes(self):
        relay = self.make_relay()
        self.store.add_detail_request("1", "https://x/1", "t")
        self.assertTrue(relay.publish_requests())
        payload = json.loads(self.publish.calls[0]["content"])
        self.assertEqual(payload["items"][0]["item_id"], "1")
        self.assertEqual(self.publish.calls[0]["path"], "site/detail-requests.json")
        self.assertFalse(relay.publish_requests())
        self.assertEqual(len(self.publish.calls), 1)
        self.store.drop_detail_request("1")
        self.assertFalse(relay.publish_requests())

    def test_publish_failure_is_not_fatal(self):
        relay = self.make_relay(publish=FakePublish(fail=True))
        self.store.add_detail_request("1", "https://x/1", "t")
        self.assertFalse(relay.publish_requests())
        self.assertEqual(self.store.count_detail_requests(), 1)

    def test_sync_caches_new_answers_once(self):
        answers = {
            "generated_at": 1000.0,
            "details": {"8372561555": detail_payload()},
        }
        relay = self.make_relay({"site/detail-results.json": answers})
        fresh = relay.sync()
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].condition, "Новый")
        self.assertIsNotNone(self.store.load_item_detail("8372561555"))
        self.assertEqual(relay.sync(), [])

    def test_sync_skips_block_pages(self):
        answers = {
            "details": {
                "5": detail_payload("5", title="Страница не загрузилась из-за проблем с IP"),
                "6": detail_payload("6"),
            }
        }
        relay = self.make_relay({"site/detail-results.json": answers})
        fresh = relay.sync()
        self.assertEqual([item.item_id for item in fresh], ["6"])
        self.assertIsNone(self.store.load_item_detail("5"))

    def test_sync_skips_nbsp_block_titles(self):
        answers = {
            "details": {
                "7": detail_payload("7", title="Страница не загрузилась из-за проблем с IP"),
            }
        }
        relay = self.make_relay({"site/detail-results.json": answers})
        self.assertEqual(relay.sync(), [])
        self.assertIsNone(self.store.load_item_detail("7"))

    def test_sync_ignores_broken_payloads(self):
        answers = {"details": {"2": "нет", "3": None, "4": ["не", "словарь"]}}
        relay = self.make_relay({"site/detail-results.json": answers})
        self.assertEqual(relay.sync(), [])

    def test_agent_online_uses_generated_at(self):
        self.now = 1_000_000.0
        relay = self.make_relay({"site/detail-results.json": {"generated_at": self.now - 60}})
        self.assertTrue(relay.agent_online())
        relay = self.make_relay({"site/detail-results.json": {"generated_at": self.now - 5000}})
        self.assertFalse(relay.agent_online())
        relay = self.make_relay({})
        self.assertFalse(relay.agent_online())


class RemoteEnricherTests(unittest.TestCase):
    def setUp(self):
        self.store = SeenStore(":memory:")
        self.publish = FakePublish()
        self.now = 1_000_000.0
        self.fetch = FakeFetch({})
        self.relay = DetailRelay(
            self.store,
            token="t",
            repo="u/r",
            fetch=self.fetch,
            publish=self.publish,
            clock=lambda: self.now,
        )
        self.sleeps = []

        def fake_sleep(seconds):
            self.sleeps.append(seconds)
            self.now += seconds

        self.enricher = RemoteEnricher(
            self.relay,
            self.store,
            wait_seconds=10.0,
            poll_seconds=5.0,
            sleep=fake_sleep,
            clock=lambda: self.now,
        )

    def test_cached_detail_is_applied_without_requests(self):
        self.store.save_item_detail(ItemDetail.from_payload(detail_payload()))
        enriched = self.enricher(listing())
        self.assertEqual(enriched.condition, "Новый")
        self.assertEqual(enriched.battery_percent, 87)
        self.assertEqual(self.store.count_detail_requests(), 0)
        self.assertEqual(self.publish.calls, [])

    def test_offline_agent_queues_request_and_returns_listing(self):
        self.store.add_detail_request("old", "https://x/old", "t")
        enriched = self.enricher(listing())
        self.assertIs(enriched.condition, None)
        self.assertEqual(self.store.count_detail_requests(), 2)
        self.assertEqual(len(self.publish.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_online_agent_waits_for_the_answer(self):
        self.fetch.files["site/detail-results.json"] = {
            "generated_at": self.now - 30,
            "details": {},
        }
        answer = {"generated_at": self.now, "details": {"8372561555": detail_payload()}}

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now += seconds
            # The home agent publishes while the bot waits.
            self.fetch.files["site/detail-results.json"] = answer

        self.enricher._sleep = sleep
        enriched = self.enricher(listing())
        self.assertEqual(enriched.condition, "Новый")
        self.assertEqual(len(self.sleeps), 1)
        self.assertIsNotNone(self.store.load_item_detail("8372561555"))

    def test_wait_budget_per_cycle(self):
        self.fetch.files["site/detail-results.json"] = {
            "generated_at": self.now,
            "details": {},
        }
        self.enricher.start_cycle()
        for _ in range(2):
            self.enricher(listing(item_id=f"id-{_}"))
        before = len(self.sleeps)
        self.enricher(listing(item_id="id-last"))
        self.assertGreater(before, 0)
        self.assertEqual(len(self.sleeps), before)


class AddendumTests(unittest.TestCase):
    def setUp(self):
        self.store = SeenStore(":memory:")
        self.sent = []

        class Telegram:
            def send_message(self, chat_id, text):
                self_outer.sent.append((chat_id, text))

        self_outer = self
        self.monitor = Monitor(
            source=_NoSource(),
            store=self.store,
            telegram=Telegram(),
            chat_ids=("1", "2"),
        )

    def test_addendum_goes_out_once_per_listing(self):
        self.store.add_detail_request("8372561555", "https://www.avito.ru/x", "iPhone 17 Pro")
        self.store.save_item_detail(ItemDetail.from_payload(detail_payload()))
        self.assertEqual(self.monitor.send_detail_addendums(), 1)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Состояние: Новый", self.sent[0][1])
        self.assertIn("Аккумулятор: 87%", self.sent[0][1])
        self.assertIn("Описание:", self.sent[0][1])
        self.assertEqual(self.store.count_detail_requests(), 0)
        self.assertEqual(self.monitor.send_detail_addendums(), 0)

    def test_request_without_answer_stays(self):
        self.store.add_detail_request("1", "https://www.avito.ru/x", "t")
        self.assertEqual(self.monitor.send_detail_addendums(), 0)
        self.assertEqual(self.store.count_detail_requests(), 1)
        self.assertEqual(self.sent, [])

    def test_message_is_not_empty(self):
        text = format_detail_addendum(title="t", url="u")
        self.assertIn("🔗 u", text)


class _NoSource:
    def fetch(self):
        return ()


if __name__ == "__main__":
    unittest.main()
