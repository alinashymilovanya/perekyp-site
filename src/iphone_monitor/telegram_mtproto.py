"""Telegram transport over MTProto (Telethon).

Telegram's Bot API is delivered over HTTPS to api.telegram.org, which Russian
providers block by TLS SNI.  Raw MTProto connections are not blocked, so the bot
can talk to Telegram through Telethon while Avito keeps seeing the home IP.
"""

import asyncio
import io
import queue
import threading
import urllib.request

from .telegram_api import TelegramApiError

try:  # pragma: no cover - the dependency is optional at import time
    from telethon import TelegramClient as _TelethonClient, events
    from telethon.network.connection.tcpmtproxy import (
        ConnectionTcpMTProxyRandomizedIntermediate,
    )

    TELETHON_AVAILABLE = True
except Exception:  # pragma: no cover
    _TelethonClient = None
    events = None
    ConnectionTcpMTProxyRandomizedIntermediate = None
    TELETHON_AVAILABLE = False

DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


def update_from_event(chat_id: int, text: str, update_id: int) -> dict:
    """Shape a Telethon event like a Bot API update so handle_update stays shared."""
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


class MtprotoTelegram:
    """Same surface as TelegramClient, backed by a real MTProto session."""

    def __init__(
        self,
        token: str,
        *,
        api_id: int = DEFAULT_API_ID,
        api_hash: str = DEFAULT_API_HASH,
        session_path: str = "data/bot_mtproto",
        proxy: str = "",
        queue_size: int = 200,
    ):
        if not TELETHON_AVAILABLE:
            raise TelegramApiError("Telethon is not installed")
        self._token = token
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._proxy = proxy.strip()
        self._updates: "queue.Queue[dict]" = queue.Queue(maxsize=queue_size)
        self._update_counter = 0
        self._client = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="telegram-mtproto", daemon=True)

    # -- lifecycle ---------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self, timeout_seconds: float = 90.0) -> None:
        self._thread.start()
        self._submit(self._create_client(), timeout_seconds)
        self._submit(self._connect(), timeout_seconds)

    async def _create_client(self) -> None:
        options = {"connection_retries": 3, "request_retries": 3}
        if self._proxy:
            options["connection"] = ConnectionTcpMTProxyRandomizedIntermediate
            host, port, secret = self._parse_proxy(self._proxy)
            options["proxy"] = (host, int(port), secret)
        self._client = _TelethonClient(
            self._session_path, self._api_id, self._api_hash, **options
        )
        self._client.add_event_handler(self._on_message, events.NewMessage(incoming=True))

    async def _connect(self) -> None:
        await self._client.start(bot_token=self._token)

    @staticmethod
    def _parse_proxy(proxy: str) -> tuple[str, str, str]:
        """Accept host:port:secret or a t.me/proxy style query string."""
        if "server=" in proxy:
            parts = dict(
                item.split("=", 1) for item in proxy.split("?", 1)[-1].split("&") if "=" in item
            )
            return parts.get("server", ""), parts.get("port", ""), parts.get("secret", "")
        pieces = proxy.split(":")
        if len(pieces) != 3:
            raise TelegramApiError("TELEGRAM_PROXY must look like host:port:secret")
        return pieces[0], pieces[1], pieces[2]

    def close(self) -> None:
        try:
            if self._client is not None:
                self._submit(self._client.disconnect(), 10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # -- plumbing ----------------------------------------------------------
    def _submit(self, coro, timeout_seconds: float = 60.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout_seconds)
        except TelegramApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as one transport error
            raise TelegramApiError(f"MTProto request failed: {exc}") from exc

    async def _on_message(self, event) -> None:
        text = event.raw_text or ""
        self._update_counter += 1
        try:
            self._updates.put_nowait(update_from_event(event.chat_id, text, self._update_counter))
        except queue.Full:
            pass

    # -- TelegramClient-compatible API ------------------------------------
    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[dict]:
        updates: list[dict] = []
        try:
            first = self._updates.get(timeout=timeout_seconds)
        except queue.Empty:
            return updates
        updates.append(first)
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except queue.Empty:
                break
        return updates

    def send_message(self, chat_id: str, text: str) -> None:
        self._submit(self._client.send_message(int(chat_id), text))

    def send_photo(self, chat_id: str, photo: str, caption: str) -> None:
        file = self._download_photo(photo) if photo.startswith("http") else photo
        self._submit(self._client.send_file(int(chat_id), file, caption=caption), 120.0)

    @staticmethod
    def _download_photo(url: str):
        """Avito's CDN rejects foreign user agents, so fetch with the app UA."""
        last_error = None
        for headers in (
            {"User-Agent": "Avito/121.0 (Android 13; Pixel 6)"},
            {"User-Agent": "Mozilla/5.0"},
        ):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = response.read(8 * 1024 * 1024)
                if data:
                    buffer = io.BytesIO(data)
                    buffer.name = "photo.jpg"
                    return buffer
            except Exception as exc:  # noqa: BLE001 - try the next header set
                last_error = exc
        raise TelegramApiError(f"Не удалось скачать фото объявления: {last_error}")
