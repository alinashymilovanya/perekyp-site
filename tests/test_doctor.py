import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from iphone_monitor.avito_api_source import AvitoApiError
from iphone_monitor.config import Settings
from iphone_monitor.doctor import (
    Check,
    check_avito,
    check_settings,
    check_telegram,
    format_report,
    run_checks,
)


def settings(**overrides) -> Settings:
    base = Settings(
        telegram_bot_token="123:abc",
        telegram_chat_id=None,
        database_path="data/seen.sqlite3",
    )
    return replace(base, **overrides)


ITEM = {
    "id": 1,
    "title": "iPhone 15, 128 ГБ, eSIM",
    "priceDetailed": {"string": "55 000 ₽", "value": 55_000},
    "urlPath": "/moskva/telefony/iphone_15_128_gb_1",
}


class SettingsCheckTests(unittest.TestCase):
    def test_short_poll_interval_for_avito_is_rejected(self):
        check = check_settings(settings(listings_source="avito_api", poll_interval_seconds=60))

        self.assertFalse(check.ok)
        self.assertIn("POLL_INTERVAL_SECONDS=60", check.detail)

    def test_acceptable_configuration_passes(self):
        check = check_settings(
            settings(listings_source="avito_api", poll_interval_seconds=600)
        )

        self.assertTrue(check.ok)
        self.assertIn("avito_api", check.detail)

    def test_mtproto_without_proxy_is_rejected(self):
        check = check_settings(settings(telegram_transport="mtproto"))

        self.assertFalse(check.ok)
        self.assertIn("TELEGRAM_PROXY", check.detail)


class AvitoCheckTests(unittest.TestCase):
    def test_reports_a_sample_listing(self):
        check = check_avito(settings(), fetch_json=lambda url: {"items": [ITEM]})

        self.assertTrue(check.ok)
        self.assertIn("iPhone 15", check.detail)

    def test_rate_limit_message_is_shown_as_is(self):
        def failing(url):
            raise AvitoApiError("Авито временно ограничил частоту запросов", rate_limited=True)

        check = check_avito(settings(), fetch_json=failing)

        self.assertFalse(check.ok)
        self.assertIn("ограничил", check.detail)

    def test_empty_feed_is_a_failure(self):
        check = check_avito(settings(), fetch_json=lambda url: {"items": []})

        self.assertFalse(check.ok)


class TelegramCheckTests(unittest.TestCase):
    def test_blocked_bot_api_suggests_mtproto(self):
        check = check_telegram(
            settings(), https_probe=lambda host, port: (False, "таймаут")
        )

        self.assertFalse(check.ok)
        self.assertIn("mtproto", check.detail)

    def test_mtproto_reports_proxy_state(self):
        check = check_telegram(
            settings(telegram_transport="mtproto", telegram_proxy="127.0.0.1:1443:deadbeef"),
            tcp_probe=lambda host, port: (True, f"{host}:{port} отвечает"),
        )

        self.assertTrue(check.ok)
        self.assertIn("127.0.0.1:1443", check.detail)

    def test_mtproto_proxy_down_is_reported(self):
        check = check_telegram(
            settings(telegram_transport="mtproto", telegram_proxy="127.0.0.1:1443:deadbeef"),
            tcp_probe=lambda host, port: (False, "connection refused"),
        )

        self.assertFalse(check.ok)
        self.assertIn("connection refused", check.detail)

    def test_proxy_link_format_is_understood(self):
        check = check_telegram(
            settings(
                telegram_transport="mtproto",
                telegram_proxy="https://t.me/proxy?server=10.0.0.5&port=1443&secret=abc",
            ),
            tcp_probe=lambda host, port: (host == "10.0.0.5" and port == 1443, "ok"),
        )

        self.assertTrue(check.ok)


class ReportTests(unittest.TestCase):
    def test_report_lists_every_check_and_counts_failures(self):
        report = format_report(
            [
                Check("Настройки", True, "всё ок"),
                Check("Авито", False, "429"),
                Check("Диагностика", False, "мелочь", critical=False),
            ]
        )

        self.assertIn("[OK  ] Настройки", report)
        self.assertIn("[СТОП] Авито", report)
        self.assertIn("Проблем: 1", report)

    def test_success_report_says_everything_is_fine(self):
        report = format_report([Check("Настройки", True, "ок")])

        self.assertIn("Всё в порядке", report)

    def test_run_checks_covers_the_four_areas(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = run_checks(
                settings(database_path=str(Path(tmp) / "db.sqlite3")),
                fetch_json=lambda url: {"items": [ITEM]},
                https_probe=lambda host, port: (True, "ok"),
            )

        self.assertEqual(
            [check.name for check in checks],
            ["Настройки", "База", "Авито", "Telegram", "Блокировка"],
        )
        self.assertTrue(all(check.ok for check in checks))


if __name__ == "__main__":
    unittest.main()
