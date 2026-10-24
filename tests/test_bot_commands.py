import unittest

from iphone_monitor.bot import handle_update


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
