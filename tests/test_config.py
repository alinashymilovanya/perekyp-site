import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iphone_monitor.config import load_settings, save_chat_id


class ConfigTests(unittest.TestCase):
    def test_loads_settings_without_exposing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "TELEGRAM_BOT_TOKEN=secret-value\n"
                "TELEGRAM_CHAT_ID=123\n"
                "MINIMUM_MARGIN_RUB=700\n",
                encoding="utf-8",
            )

            settings = load_settings(path)

            self.assertEqual(settings.telegram_bot_token, "secret-value")
            self.assertEqual(settings.telegram_chat_id, "123")
            self.assertEqual(settings.minimum_margin_rub, 700)
            self.assertEqual(settings.listings_source, "disabled")
            self.assertEqual(settings.daily_summary_time, "20:00")
            self.assertEqual(settings.summary_timezone, "Europe/Astrakhan")

    def test_loads_daily_summary_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "TELEGRAM_BOT_TOKEN=secret-value\n"
                "DAILY_SUMMARY_TIME=09:30\n"
                "SUMMARY_TIMEZONE=Europe/Moscow\n",
                encoding="utf-8",
            )

            settings = load_settings(path)

            self.assertEqual(settings.daily_summary_time, "09:30")
            self.assertEqual(settings.summary_timezone, "Europe/Moscow")

    def test_rejects_invalid_daily_summary_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "TELEGRAM_BOT_TOKEN=secret-value\nDAILY_SUMMARY_TIME=25:00\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_settings(path)

    def test_loads_settings_from_container_environment_without_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / ".env"
            with patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BOT_TOKEN": "container-token",
                    "TELEGRAM_CHAT_ID": "123",
                    "LISTINGS_SOURCE": "disabled",
                },
                clear=True,
            ):
                settings = load_settings(missing_path)

            self.assertEqual(settings.telegram_bot_token, "container-token")
            self.assertEqual(settings.telegram_chat_id, "123")

    def test_saves_chat_id_without_replacing_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TELEGRAM_BOT_TOKEN=secret-value\n", encoding="utf-8")

            save_chat_id(path, "987654")

            content = path.read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_BOT_TOKEN=secret-value", content)
            self.assertIn("TELEGRAM_CHAT_ID=987654", content)
