import http.client
import unittest
from unittest.mock import patch

from iphone_monitor.telegram_api import TelegramApiError, TelegramClient


class TelegramApiTests(unittest.TestCase):
    def test_remote_disconnect_becomes_recoverable_api_error(self):
        client = TelegramClient("test-token")

        with patch(
            "iphone_monitor.telegram_api.urlopen",
            side_effect=http.client.RemoteDisconnected("closed"),
        ):
            with self.assertRaises(TelegramApiError):
                client._call("getMe")
