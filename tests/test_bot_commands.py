import unittest
import tempfile
from pathlib import Path

from iphone_monitor.bot import handle_update
from iphone_monitor.store import SeenStore


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class BotCommandTests(unittest.TestCase):
    def test_start_replies_with_monitoring_status(self):
        telegram = FakeTelegram()
        update = {"message": {"chat": {"id": 123}, "text": "/start"}}

        chat_id = handle_update(update, telegram)

        self.assertEqual(chat_id, "123")
        self.assertEqual(telegram.sent[0][0], "123")
        self.assertIn("подключён", telegram.sent[0][1])

    def test_unknown_command_does_not_crash_and_explains_help(self):
        telegram = FakeTelegram()
        update = {"message": {"chat": {"id": 123}, "text": "/unknown"}}

        handle_update(update, telegram)

        self.assertIn("/start", telegram.sent[0][1])

    def test_pause_and_resume_are_persisted(self):
        telegram = FakeTelegram()
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                handle_update(
                    {"message": {"chat": {"id": 123}, "text": "/pause"}},
                    telegram,
                    store=store,
                )
                self.assertEqual(store.get_metadata("monitor_paused"), "1")

                handle_update(
                    {"message": {"chat": {"id": 123}, "text": "/resume"}},
                    telegram,
                    store=store,
                )
                self.assertEqual(store.get_metadata("monitor_paused"), "0")

    def test_start_subscribes_and_stop_unsubscribes(self):
        telegram = FakeTelegram()
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                handle_update(
                    {"message": {"chat": {"id": 555}, "text": "/start"}},
                    telegram,
                    store=store,
                )
                self.assertEqual(store.list_subscribers(), ("555",))

                handle_update(
                    {"message": {"chat": {"id": 555}, "text": "/stop"}},
                    telegram,
                    store=store,
                )
                self.assertEqual(store.list_subscribers(), ())

    def test_command_with_bot_suffix_is_recognised(self):
        telegram = FakeTelegram()
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                handle_update(
                    {"message": {"chat": {"id": 777}, "text": "/start@perekypibot"}},
                    telegram,
                    store=store,
                )

                self.assertEqual(store.list_subscribers(), ("777",))

    def test_status_reports_paused_state(self):
        telegram = FakeTelegram()
        with tempfile.TemporaryDirectory() as directory:
            with SeenStore(Path(directory) / "seen.sqlite3") as store:
                store.set_metadata("monitor_paused", "1")
                handle_update(
                    {"message": {"chat": {"id": 123}, "text": "/status"}},
                    telegram,
                    store=store,
                )

                self.assertIn("приостановлен", telegram.sent[0][1])
