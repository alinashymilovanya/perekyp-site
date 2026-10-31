"""Small Telegram Bot API client using only the Python standard library."""

import http.client
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

API_HOST = "api.telegram.org"


class TelegramApiError(RuntimeError):
    """Raised when Telegram rejects a request or cannot be reached."""


def _open_http_proxy_tunnel(proxy_host: str, proxy_port: int, host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout)
    request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii")
    sock.sendall(request)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise TelegramApiError("Proxy closed the connection")
        data += chunk
        if len(data) > 65536:
            raise TelegramApiError("Proxy sent a huge response")
    status_line = data.split(b"\r\n", 1)[0]
    if b" 200 " not in status_line:
        raise TelegramApiError("Proxy refused the tunnel")
    return sock


def _open_socks5_tunnel(proxy_host: str, proxy_port: int, host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        raise TelegramApiError("SOCKS5 proxy requires authentication")
    host_bytes = host.encode("idna")
    sock.sendall(
        b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, "big")
    )
    reply = sock.recv(10)
    if len(reply) < 2 or reply[1] != 0:
        raise TelegramApiError("SOCKS5 proxy refused the tunnel")
    return sock


class _ProxiedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, proxy: str, timeout: float, context: ssl.SSLContext):
        super().__init__(API_HOST, 443, timeout=timeout, context=context)
        self._proxy = urlparse(proxy)

    def connect(self) -> None:
        proxy = self._proxy
        scheme = (proxy.scheme or "http").lower()
        proxy_port = proxy.port or (1080 if scheme.startswith("socks") else 8080)
        if scheme.startswith("socks"):
            raw = _open_socks5_tunnel(proxy.hostname or "", proxy_port, API_HOST, 443, self.timeout)
        else:
            raw = _open_http_proxy_tunnel(proxy.hostname or "", proxy_port, API_HOST, 443, self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=API_HOST)


class TelegramClient:
    def __init__(self, token: str, timeout_seconds: int = 40, proxy: str = ""):
        self._token = token
        self._api_root = f"https://{API_HOST}/bot{token}"
        self._timeout_seconds = timeout_seconds
        self._proxy = (proxy or "").strip()

    def _post_via_proxy(self, method: str, body: bytes) -> dict:
        connection = _ProxiedHTTPSConnection(
            self._proxy, self._timeout_seconds, ssl.create_default_context()
        )
        try:
            connection.request(
                "POST",
                f"/bot{self._token}/{method}",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        if response.status >= 400:
            raise TelegramApiError("Telegram request failed")
        return json.loads(payload.decode("utf-8"))

    def _call(self, method: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload or {}).encode("utf-8")
        try:
            if self._proxy:
                result = self._post_via_proxy(method, body)
            else:
                request = Request(
                    f"{self._api_root}/{method}",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
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
