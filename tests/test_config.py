import tempfile
import unittest
from pathlib import Path

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

    def test_saves_chat_id_without_replacing_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TELEGRAM_BOT_TOKEN=secret-value\n", encoding="utf-8")

            save_chat_id(path, "987654")

            content = path.read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_BOT_TOKEN=secret-value", content)
            self.assertIn("TELEGRAM_CHAT_ID=987654", content)
