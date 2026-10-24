"""Small Telegram Bot API client using only the Python standard library."""

import json
import http.client
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramApiError(RuntimeError):
    """Raised when Telegram rejects a request or cannot be reached."""


class TelegramClient:
    def __init__(self, token: str, timeout_seconds: int = 40):
        self._api_root = f"https://api.telegram.org/bot{token}"
        self._timeout_seconds = timeout_seconds

    def _call(self, method: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self._api_root}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.RemoteDisconnected,
            json.JSONDecodeError,
        ) as exc:
            raise TelegramApiError("Telegram request failed") from exc
        if not result.get("ok"):
            raise TelegramApiError("Telegram rejected the request")
        return result["result"]

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[dict]:
        payload = {"timeout": timeout_seconds, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload)

    def send_message(self, chat_id: str, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def send_photo(self, chat_id: str, photo: str, caption: str) -> None:
        self._call(
            "sendPhoto",
            {"chat_id": chat_id, "photo": photo, "caption": caption},
        )
