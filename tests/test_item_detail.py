import json
import unittest
from dataclasses import replace

from iphone_monitor.avito_parser import ParsedListing
from iphone_monitor.item_detail import (
    DetailEnricher,
    ItemDetail,
    ItemDetailError,
    apply_detail,
    detail_url_candidates,
    load_detail,
    parse_item_page,
    probe,
    run_probe_once,
)
from iphone_monitor.monitor import Monitor
from iphone_monitor.store import SeenStore


def item_payload(**overrides) -> dict:
    item = {
        "id": 8372561555,
        "title": "iPhone 17 Pro, 512 ГБ, eSIM",
        "description": "Телефон новый, не активирован. Комплект полный.",
        "price": {"value": "81 990"},
        "time": 1793440909,
        "address": "Москва, Багратионовский пр., 7к20А",
        "seller": {"name": "Магазин Техно"},
        "images": [{"140x140": "s.jpg", "1080x810": "big.jpg"}],
        "parameters": {
            "flat": [
                {"title": "Состояние", "description": "Новое"},
                {"title": "Модель", "description": "iPhone 17 Pro"},
                {"title": "Встроенная память", "description": "512 ГБ"},
                {"title": "SIM-карты", "description": "Только eSIM"},
                {"title": "Цвет", "description": "Оранжевый"},
                {"title": "Аккумулятор", "description": "87%"},
            ]
        },
    }
    item.update(overrides)
    return item


def page_html(item: dict) -> str:
    payload = {"item": {"item": item}}
    literal = json.dumps(json.dumps(payload, ensure_ascii=False))
    return (
        "<html><head><script>window.__initialData__ = "
        + literal
        + ";</script></head><body>card</body></html>"
    )


def listing(**overrides) -> ParsedListing:
    base = ParsedListing(
        item_id="8372561555",
        title="iPhone 17 Pro, 512 ГБ, eSIM",
        model="iphone 17 pro",
        storage_gb=512,
        price_rub=81_990,
        condition=None,
        battery_percent=None,
        location="Москва",
        description="iPhone 17 Pro, 512 ГБ, eSIM",
        url="https://www.avito.ru/moskva/telefony/iphone_17_pro_512_gb_esim_8372561555?context=abc",
        images=(),
    )
    return replace(base, **overrides)


class ParseItemPageTests(unittest.TestCase):
    def test_reads_condition_battery_and_description(self):
        detail = parse_item_page(page_html(item_payload()), "8372561555")
        self.assertEqual(detail.condition, "Новый")
        self.assertEqual(detail.battery_percent, 87)
        self.assertEqual(detail.storage_gb, 512)
        self.assertEqual(detail.color, "Оранжевый")
        self.assertEqual(detail.sim, "Только eSIM")
        self.assertEqual(detail.price_rub, 81_990)
        self.assertEqual(detail.seller_name, "Магазин Техно")
        self.assertIn("не активирован", detail.description)
        self.assertEqual(detail.images, ("big.jpg",))
        self.assertTrue(detail.published_at.startswith("2026-10-31"))

    def test_survives_quotes_inside_the_string_literal(self):
        item = item_payload(description='Продаю "iPhone 17 Pro" \\ новый')
        detail = parse_item_page(page_html(item), "1")
        self.assertIn("iPhone 17 Pro", detail.description)

    def test_missing_block_is_an_error(self):
        with self.assertRaises(ItemDetailError):
            parse_item_page("<html>no data here</html>", "1")

    def test_battery_below_eighty_counts_as_worn(self):
        item = item_payload()
        item["parameters"]["flat"] = [{"title": "Аккумулятор", "description": "Ниже 80%"}]
        self.assertEqual(parse_item_page(page_html(item), "1").battery_percent, 79)

    def test_terabyte_storage_is_converted(self):
        item = item_payload()
        item["parameters"]["flat"] = [{"title": "Встроенная память", "description": "1 ТБ"}]
        self.assertEqual(parse_item_page(page_html(item), "1").storage_gb, 1024)


class LoadDetailTests(unittest.TestCase):
    def test_mobile_page_is_tried_first(self):
        urls = detail_url_candidates(listing())
        self.assertEqual(urls[0][0], "m_html")
        self.assertEqual(
            urls[0][1],
            "https://m.avito.ru/moskva/telefony/iphone_17_pro_512_gb_esim_8372561555",
        )

    def test_falls_back_to_www_when_mobile_is_blocked(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "m.avito.ru" in url:
                return "<html>Доступ ограничен: проблема с IP</html>"
            return page_html(item_payload())

        detail = load_detail(listing(), fetch=fetch)
        self.assertEqual(detail.condition, "Новый")
        self.assertEqual(len(calls), 2)

    def test_rate_limit_stops_further_attempts(self):
        calls = []

        def fetch(url):
            calls.append(url)
            raise ItemDetailError("HTTP 429", rate_limited=True)

        with self.assertRaises(ItemDetailError) as caught:
            load_detail(listing(), fetch=fetch)
        self.assertTrue(caught.exception.rate_limited)
        self.assertEqual(len(calls), 1)

    def test_apply_detail_keeps_the_feed_price(self):
        detail = parse_item_page(page_html(item_payload(price={"value": "99 000"})), "1")
        enriched = apply_detail(listing(price_rub=81_990), detail)
        self.assertEqual(enriched.price_rub, 81_990)
        self.assertEqual(enriched.condition, "Новый")
        self.assertEqual(enriched.battery_percent, 87)
        self.assertIn("не активирован", enriched.description)


class StoreCacheTests(unittest.TestCase):
    def test_saved_detail_is_returned_as_is(self):
        store = SeenStore(":memory:")
        self.addCleanup(store.close)
        self.assertIsNone(store.load_item_detail("1"))
        store.save_item_detail(ItemDetail(item_id="1", condition="Б/у", battery_percent=91))
        loaded = store.load_item_detail("1")
        self.assertEqual(loaded.condition, "Б/у")
        self.assertEqual(loaded.battery_percent, 91)
        self.assertEqual(store.count_item_details(), 1)

    def test_broken_payload_is_not_fatal(self):
        store = SeenStore(":memory:")
        self.addCleanup(store.close)
        store.set_metadata("x", "1")
        store._connection.execute(
            "INSERT INTO item_details(item_id, payload) VALUES ('2', 'not json')"
        )
        self.assertIsNone(store.load_item_detail("2"))


class EnricherTests(unittest.TestCase):
    def setUp(self):
        self.store = SeenStore(":memory:")
        self.addCleanup(self.store.close)
        self.calls = []

    def _fetch(self, url):
        self.calls.append(url)
        return page_html(item_payload())

    def test_fetches_once_then_answers_from_the_cache(self):
        enricher = DetailEnricher(self.store, fetch=self._fetch, sleep=lambda _: None)
        enricher.start_cycle()
        first = enricher(listing())
        self.assertEqual(first.condition, "Новый")
        self.assertEqual(len(self.calls), 1)
        enricher.start_cycle()
        second = enricher(listing())
        self.assertEqual(second.condition, "Новый")
        self.assertEqual(len(self.calls), 1)

    def test_cycle_budget_limits_requests(self):
        enricher = DetailEnricher(
            self.store, max_per_cycle=1, fetch=self._fetch, sleep=lambda _: None
        )
        enricher.start_cycle()
        enricher(listing(item_id="1"))
        enricher(listing(item_id="2"))
        self.assertEqual(len(self.calls), 1)

    def test_rate_limit_sets_a_cooldown(self):
        def failing(url):
            self.calls.append(url)
            raise ItemDetailError("HTTP 429", rate_limited=True)

        enricher = DetailEnricher(
            self.store,
            fetch=failing,
            sleep=lambda _: None,
            cooldown_minutes=15,
            clock=lambda: 1_000.0,
        )
        enricher.start_cycle()
        enricher(listing(item_id="1"))
        enricher.start_cycle()
        unchanged = enricher(listing(item_id="2"))
        self.assertEqual(unchanged.condition, None)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.get_metadata("detail_cooldown_until"), "1900.0")


class ProbeAndMonitorTests(unittest.TestCase):
    def test_probe_reports_the_working_candidate(self):
        def fetch(url):
            if "m.avito.ru" in url:
                raise ItemDetailError("HTTP 404")
            return page_html(item_payload())

        result = probe(listing(), fetch=fetch)
        self.assertEqual(result["ok"], "www_html")
        self.assertEqual(len(result["attempts"]), 2)

    def test_probe_runs_only_once(self):
        store = SeenStore(":memory:")
        self.addCleanup(store.close)

        class Source:
            calls = 0

            def fetch(self):
                Source.calls += 1
                return [listing()]

        first = run_probe_once(store, Source(), fetch=lambda url: page_html(item_payload()))
        self.assertIsNotNone(first)
        self.assertIsNone(run_probe_once(store, Source(), fetch=lambda url: page_html(item_payload())))
        self.assertEqual(Source.calls, 1)

    def test_monitor_notifies_with_the_real_condition(self):
        store = SeenStore(":memory:")
        self.addCleanup(store.close)
        store.mark_seen("old")

        comps = [
            listing(item_id="a", price_rub=100_000, condition=None),
            listing(item_id="b", price_rub=101_000, condition=None),
            listing(item_id="c", price_rub=102_000, condition=None),
            listing(item_id="target", price_rub=80_000, condition=None),
        ]

        class Source:
            def fetch(self):
                return comps

        sent = []

        class Telegram:
            def send_message(self, chat_id, text):
                sent.append(text)

            def send_photo(self, chat_id, photo, caption):
                sent.append(caption)

        store.mark_seen_many([item.item_id for item in comps[:3]])
        enricher = DetailEnricher(
            store, fetch=lambda url: page_html(item_payload()), sleep=lambda _: None
        )
        monitor = Monitor(
            Source(), store, Telegram(), "42", enricher=enricher, notify_min_margin_rub=1
        )
        self.assertEqual(monitor.process_once(), 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("Состояние: Новый", sent[0])
        self.assertIn("Аккумулятор: 87%", sent[0])
        self.assertIn("не активирован", sent[0])
        self.assertIn("Цвет: Оранжевый", sent[0])

    def test_monitor_skips_a_deal_that_detail_marks_as_broken(self):
        store = SeenStore(":memory:")
        self.addCleanup(store.close)
        store.mark_seen("old")

        comps = [
            listing(item_id="a", price_rub=100_000),
            listing(item_id="b", price_rub=101_000),
            listing(item_id="c", price_rub=102_000),
            listing(item_id="target", price_rub=99_000),
        ]

        class Source:
            def fetch(self):
                return comps

        class Telegram:
            def send_message(self, chat_id, text):
                raise AssertionError("nothing should be sent")

            send_photo = send_message

        store.mark_seen_many([item.item_id for item in comps[:3]])
        broken = item_payload()
        broken["parameters"]["flat"] = [
            {"title": "Состояние", "description": "Битый"},
            {"title": "Аккумулятор", "description": "Ниже 80%"},
        ]
        enricher = DetailEnricher(
            store, fetch=lambda url: page_html(broken), sleep=lambda _: None
        )
        monitor = Monitor(
            Source(),
            store,
            Telegram(),
            "42",
            enricher=enricher,
            notify_min_margin_rub=0,
            repair_reserve_broken_rub=10_000,
        )
        self.assertEqual(monitor.process_once(), 0)


if __name__ == "__main__":
    unittest.main()
