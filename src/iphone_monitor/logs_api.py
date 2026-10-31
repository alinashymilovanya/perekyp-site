"""Protected HTTP API that serves the bot log to the closed admin page.

Why this module exists
----------------------
The public site is static (InfinityFree) and its snapshot ``site/site-data.json``
is world readable, so the log can only be served from a process that actually
owns the log file: the bot container on Amvera, or this module started next to
it.  ``site/server.py`` is a local preview server and is *not* a production
gateway.

Security model
--------------
* Fail closed: without ``ADMIN_LOGS_PASSWORD`` and a ``ADMIN_LOGS_SESSION_SECRET``
  of at least 16 characters every endpoint answers ``503``.
* Password check is constant time; login attempts are rate limited per client.
* Sessions are short lived, HMAC-SHA256 signed and revocable; the page keeps the
  token in memory only.  With ``ADMIN_LOGS_COOKIE_MODE=1`` the same token is also
  set as an ``HttpOnly; Secure; SameSite=Strict`` cookie for same-origin setups.
* Every read needs a valid session, always reads the configured ``LOG_PATH``
  (a path from the query string is ignored), and returns at most ``limit``
  entries from the tail of the file and its rotated backups.
* Responses are ``no-store`` and ``X-Robots-Tag: noindex``; CORS is pinned to one
  exact origin and never ``*``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

from .config import read_env_file
from .log_reader import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_ROTATED_FILES,
    LogFilters,
    build_facets,
    parse_moment,
    parse_values,
    read_log_entries,
    select_entries,
)
from .telemetry import configure_logging, iso_timestamp, log_event

DEFAULT_PORT = 8787
DEFAULT_TTL_SECONDS = 3_600
DEFAULT_MAX_LOGIN_ATTEMPTS = 5
DEFAULT_LOGIN_WINDOW_SECONDS = 300
DEFAULT_LOCKOUT_SECONDS = 900
DEFAULT_DEFAULT_LIMIT = 50
DEFAULT_MAX_LIMIT = 200
MIN_SECRET_LENGTH = 16
SESSION_COOKIE = "admin_logs_session"
LOGIN_PATH = "/api/admin/logs/login"
LOGOUT_PATH = "/api/admin/logs/logout"
LOGS_PATH = "/api/admin/logs"
HEALTH_PATH = "/api/admin/logs/health"
MAX_REQUEST_BODY = 8_192

_CONFIGURED_MESSAGE = (
    "Раздел логов выключен: задайте ADMIN_LOGS_PASSWORD и "
    "ADMIN_LOGS_SESSION_SECRET (не короче 16 символов)."
)


@dataclass(frozen=True)
class LogsApiConfig:
    """Everything the API needs, read from the environment once."""

    password: str = ""
    session_secret: str = ""
    enabled: bool = False
    session_ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_login_attempts: int = DEFAULT_MAX_LOGIN_ATTEMPTS
    login_window_seconds: int = DEFAULT_LOGIN_WINDOW_SECONDS
    lockout_seconds: int = DEFAULT_LOCKOUT_SECONDS
    cors_origin: str = ""
    log_path: str = "data/bot.log"
    rotated_files: int = DEFAULT_ROTATED_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    max_entries: int = DEFAULT_MAX_ENTRIES
    default_limit: int = DEFAULT_DEFAULT_LIMIT
    max_limit: int = DEFAULT_MAX_LIMIT
    bind_host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    cookie_mode: bool = False
    cookie_secure: bool = True
    trust_proxy: bool = False
    api_log_path: str = "data/logs-api.log"

    @property
    def configured(self) -> bool:
        """Access is closed unless both secrets are present and strong enough."""
        return bool(self.password.strip()) and len(self.session_secret.strip()) >= MIN_SECRET_LENGTH

    @property
    def problems(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.password.strip():
            issues.append("ADMIN_LOGS_PASSWORD не задан")
        if len(self.session_secret.strip()) < MIN_SECRET_LENGTH:
            issues.append(
                f"ADMIN_LOGS_SESSION_SECRET короче {MIN_SECRET_LENGTH} символов"
            )
        return tuple(issues)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LogsApiConfig":
        values = _environment(env)
        port_env = values.get("PORT", "").strip()
        default_host = "0.0.0.0" if port_env else "127.0.0.1"
        return cls(
            password=values.get("ADMIN_LOGS_PASSWORD", ""),
            session_secret=values.get("ADMIN_LOGS_SESSION_SECRET", ""),
            enabled=_env_bool(values, "ADMIN_LOGS_ENABLED", False),
            session_ttl_seconds=_env_int(
                values, "ADMIN_LOGS_SESSION_TTL_SECONDS", DEFAULT_TTL_SECONDS, minimum=60
            ),
            max_login_attempts=_env_int(
                values, "ADMIN_LOGS_MAX_LOGIN_ATTEMPTS", DEFAULT_MAX_LOGIN_ATTEMPTS, minimum=1
            ),
            login_window_seconds=_env_int(
                values, "ADMIN_LOGS_LOGIN_WINDOW_SECONDS", DEFAULT_LOGIN_WINDOW_SECONDS, minimum=10
            ),
            lockout_seconds=_env_int(
                values, "ADMIN_LOGS_LOCKOUT_SECONDS", DEFAULT_LOCKOUT_SECONDS, minimum=10
            ),
            cors_origin=values.get("ADMIN_LOGS_CORS_ORIGIN", "").strip().rstrip("/"),
            log_path=values.get("LOG_PATH", "data/bot.log"),
            rotated_files=_env_int(
                values, "ADMIN_LOGS_ROTATED_FILES", DEFAULT_ROTATED_FILES, minimum=0
            ),
            max_bytes=_env_int(values, "ADMIN_LOGS_MAX_BYTES", DEFAULT_MAX_BYTES, minimum=4_096),
            max_entries=_env_int(
                values, "ADMIN_LOGS_MAX_ENTRIES", DEFAULT_MAX_ENTRIES, minimum=10
            ),
            default_limit=_env_int(
                values, "ADMIN_LOGS_DEFAULT_LIMIT", DEFAULT_DEFAULT_LIMIT, minimum=1
            ),
            max_limit=_env_int(values, "ADMIN_LOGS_MAX_LIMIT", DEFAULT_MAX_LIMIT, minimum=1),
            bind_host=values.get("ADMIN_LOGS_HOST", default_host).strip() or default_host,
            port=_env_int(
                values,
                "ADMIN_LOGS_PORT",
                int(port_env) if port_env.isdigit() else DEFAULT_PORT,
                minimum=1,
            ),
            cookie_mode=_env_bool(values, "ADMIN_LOGS_COOKIE_MODE", False),
            cookie_secure=_env_bool(values, "ADMIN_LOGS_COOKIE_SECURE", True),
            trust_proxy=_env_bool(values, "ADMIN_LOGS_TRUST_PROXY", False),
            api_log_path=values.get("ADMIN_LOGS_LOG_PATH", "data/logs-api.log"),
        )


def _environment(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    values = read_env_file(".env")
    values.update(os.environ)
    return values


def _env_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(values: Mapping[str, str], key: str, default: int, *, minimum: int) -> int:
    raw = values.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, parsed)


@dataclass(frozen=True)
class Session:
    sid: str
    issued_at: float
    expires_at: float


class SessionStore:
    """Stateless signed tokens plus an in-memory revocation list."""

    def __init__(self, secret: str, ttl_seconds: int, *, clock: Callable[[], float] = time.time):
        self._secret = secret.encode("utf-8")
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._clock = clock
        self._revoked: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> tuple[str, Session]:
        now = self._clock()
        session = Session(
            sid=secrets.token_hex(8), issued_at=now, expires_at=now + self.ttl_seconds
        )
        payload = _b64encode(
            json.dumps(
                {"v": 1, "sid": session.sid, "iat": int(now), "exp": int(session.expires_at)},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return f"{payload}.{self._sign(payload)}", session

    def verify(self, token: str) -> Session | None:
        data = self._decode(token)
        if data is None:
            return None
        try:
            sid = str(data["sid"])
            expires_at = float(data["exp"])
            issued_at = float(data.get("iat", 0))
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= self._clock():
            return None
        with self._lock:
            revoked_until = self._revoked.get(sid)
            if revoked_until is not None and revoked_until > self._clock():
                return None
        return Session(sid=sid, issued_at=issued_at, expires_at=expires_at)

    def revoke(self, token: str) -> bool:
        data = self._decode(token)
        if data is None:
            return False
        try:
            sid = str(data["sid"])
            expires_at = float(data["exp"])
        except (KeyError, TypeError, ValueError):
            return False
        with self._lock:
            self._revoked[sid] = expires_at
            self._prune_locked()
        return True

    def _decode(self, token: str) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        payload, _, signature = token.rpartition(".")
        if not payload or not signature:
            return None
        if not hmac.compare_digest(self._sign(payload), signature):
            return None
        try:
            raw = _b64decode(payload)
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).digest()
        return _b64encode(digest)

    def _prune_locked(self) -> None:
        now = self._clock()
        expired = [sid for sid, until in self._revoked.items() if until <= now]
        for sid in expired:
            self._revoked.pop(sid, None)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


class AttemptLimiter:
    """Sliding window of failed logins per client, with a lockout."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1, int(window_seconds))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self._clock = clock
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def blocked_for(self, key: str) -> float:
        """Seconds left in the lockout, 0 when the client may try again."""
        now = self._clock()
        with self._lock:
            state = self._state.get(key)
            if not state:
                return 0.0
            blocked_until = float(state.get("blocked_until", 0.0))
        return max(0.0, blocked_until - now)

    def register_failure(self, key: str) -> float:
        """Record a failed attempt and return the new lockout, if any."""
        now = self._clock()
        with self._lock:
            state = self._state.setdefault(key, {"failures": [], "blocked_until": 0.0})
            failures = [
                moment
                for moment in state["failures"]
                if now - float(moment) <= self.window_seconds
            ]
            failures.append(now)
            if len(failures) >= self.max_attempts:
                state["blocked_until"] = now + self.lockout_seconds
                state["failures"] = []
                blocked = float(self.lockout_seconds)
            else:
                state["failures"] = failures
                blocked = 0.0
            if len(self._state) > 1_024:
                self._prune_locked(now)
        return blocked

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def _prune_locked(self, now: float) -> None:
        stale = [
            key
            for key, state in self._state.items()
            if float(state.get("blocked_until", 0.0)) <= now
            and not state.get("failures")
        ]
        for key in stale:
            self._state.pop(key, None)


class LogsApi:
    """The application behind the HTTP handler (kept separate to be testable)."""

    def __init__(
        self,
        config: LogsApiConfig,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.clock = clock
        self.sessions = SessionStore(config.session_secret, config.session_ttl_seconds, clock=clock)
        self.limiter = AttemptLimiter(
            config.max_login_attempts,
            config.login_window_seconds,
            config.lockout_seconds,
            clock=monotonic,
        )

    @property
    def configured(self) -> bool:
        return self.config.configured

    def password_matches(self, candidate: str) -> bool:
        expected = self.config.password
        if not expected or not isinstance(candidate, str):
            return False
        return hmac.compare_digest(
            hashlib.sha256(candidate.encode("utf-8")).digest(),
            hashlib.sha256(expected.encode("utf-8")).digest(),
        )

    def read_feed(
        self,
        *,
        filters: LogFilters,
        before: float | None,
        limit: int,
    ) -> dict[str, Any]:
        window = read_log_entries(
            self.config.log_path,
            rotated_files=self.config.rotated_files,
            max_bytes=self.config.max_bytes,
            max_entries=self.config.max_entries,
        )
        entries = select_entries(window["entries"], filters, before=before, limit=limit + 1)
        has_more = len(entries) > limit
        page = entries[:limit]
        next_before = None
        if has_more and page:
            last = page[-1].get("timestamp")
            next_before = float(last) if last is not None else None
        return {
            "entries": page,
            "count": len(page),
            "limit": limit,
            "has_more": has_more,
            "next_before": next_before,
            "generated_at": iso_timestamp(self.clock()),
            "window": {
                "files": window["files"],
                "bytes_read": window["bytes_read"],
                "scanned": len(window["entries"]),
                "truncated": window["truncated"],
            },
            "facets": build_facets(window["entries"]),
        }


def build_handler(app: LogsApi):
    """Bind one :class:`LogsApi` application to a request handler class."""

    class LogsApiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "admin-logs/1.0"
        sys_version = ""
        api = app

        # -- plumbing -----------------------------------------------------
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Silence the default stderr line: every access is a real event."""
            return None

        def _security_headers(self) -> dict[str, str]:
            return {
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            }

        def _cors_headers(self) -> dict[str, str]:
            origin = (self.headers.get("Origin") or "").strip()
            allowed = (self.api.config.cors_origin or "").strip()
            headers = {"Vary": "Origin"}
            if (
                allowed
                and origin
                and hmac.compare_digest(
                    origin.encode("utf-8", "surrogateescape"),
                    allowed.encode("utf-8", "surrogateescape"),
                )
            ):
                headers["Access-Control-Allow-Origin"] = allowed
                headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
                headers["Access-Control-Max-Age"] = "600"
            return headers

        def _send_json(
            self,
            status: int,
            payload: Mapping[str, Any],
            *,
            extra: Mapping[str, str] | None = None,
            cookies: tuple[str, ...] = (),
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in {**self._security_headers(), **self._cors_headers(), **(extra or {})}.items():
                self.send_header(name, value)
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _client_key(self) -> str:
            if self.api.config.trust_proxy:
                forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                if forwarded:
                    return forwarded
            return str(self.client_address[0]) if self.client_address else "unknown"

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length") or "0"
            try:
                length = int(raw_length)
            except ValueError:
                return None
            if length <= 0 or length > MAX_REQUEST_BODY:
                return None
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

        def _cookie_token(self) -> str:
            header = self.headers.get("Cookie") or ""
            for part in header.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE:
                    return value.strip()
            return ""

        def _bearer_token(self) -> str:
            header = (self.headers.get("Authorization") or "").strip()
            if header.lower().startswith("bearer "):
                return header[7:].strip()
            return ""

        def _authenticate(self) -> Session | None:
            token = self._bearer_token()
            if not token and self.api.config.cookie_mode and self.command == "GET":
                # Read-only access via the HttpOnly cookie; state changes always
                # need the in-memory token, which keeps CSRF out of reach.
                token = self._cookie_token()
            if not token:
                return None
            return self.api.sessions.verify(token)

        def _require_configured(self) -> bool:
            if self.api.configured:
                return True
            self._send_json(503, {"error": "not_configured", "message": _CONFIGURED_MESSAGE})
            return False

        def _unauthorized(self) -> None:
            self._send_json(
                401,
                {"error": "unauthorized", "message": "Требуется вход в раздел логов."},
                extra={"WWW-Authenticate": 'Bearer realm="admin-logs"'},
            )

        # -- routing ------------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802
            cors = self._cors_headers()
            if "Access-Control-Allow-Origin" not in cors:
                self._send_json(403, {"error": "origin_not_allowed"})
                return
            self.send_response(204)
            for name, value in {**self._security_headers(), **cors}.items():
                self.send_header(name, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == HEALTH_PATH:
                self._handle_health()
                return
            if path == LOGS_PATH:
                self._handle_logs()
                return
            self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path == LOGIN_PATH:
                self._handle_login()
                return
            if path == LOGOUT_PATH:
                self._handle_logout()
                return
            self._send_json(404, {"error": "not_found"})

        # -- endpoints ----------------------------------------------------
        def _handle_health(self) -> None:
            self._send_json(
                200,
                {
                    "ok": True,
                    "auth_required": True,
                    "configured": self.api.configured,
                    "log_present": Path(self.api.config.log_path).is_file(),
                    "time": iso_timestamp(self.api.clock()),
                },
            )

        def _handle_login(self) -> None:
            if not self._require_configured():
                return
            client = self._client_key()
            blocked = self.api.limiter.blocked_for(client)
            if blocked > 0:
                log_event(
                    "admin_logs_login_blocked",
                    status="skipped",
                    component="logs_api",
                    client=client,
                    retry_in_seconds=round(blocked, 1),
                )
                self._send_json(
                    429,
                    {"error": "too_many_attempts", "retry_in": round(blocked, 1)},
                    extra={"Retry-After": str(int(blocked) + 1)},
                )
                return
            payload = self._read_json()
            candidate = payload.get("password") if payload else None
            if not isinstance(candidate, str) or not candidate:
                self.api.limiter.register_failure(client)
                log_event(
                    "admin_logs_login_failed",
                    status="failed",
                    component="logs_api",
                    client=client,
                    reason="empty_password",
                )
                self._send_json(401, {"error": "invalid_credentials"})
                return
            if not self.api.password_matches(candidate):
                wait = self.api.limiter.register_failure(client)
                log_event(
                    "admin_logs_login_failed",
                    status="failed",
                    component="logs_api",
                    client=client,
                    reason="wrong_password",
                )
                extra = {"Retry-After": str(int(wait) + 1)} if wait else None
                self._send_json(401, {"error": "invalid_credentials"}, extra=extra)
                return
            self.api.limiter.reset(client)
            token, session = self.api.sessions.issue()
            log_event(
                "admin_logs_login_succeeded",
                component="logs_api",
                client=client,
                expires_at=iso_timestamp(session.expires_at),
            )
            cookies: tuple[str, ...] = ()
            if self.api.config.cookie_mode:
                cookies = (self._session_cookie(token, session),)
            self._send_json(
                200,
                {
                    "ok": True,
                    "token": token,
                    "expires_at": iso_timestamp(session.expires_at),
                    "expires_in": int(max(0, session.expires_at - self.api.clock())),
                },
                cookies=cookies,
            )

        def _session_cookie(self, token: str, session: Session) -> str:
            parts = [
                f"{SESSION_COOKIE}={token}",
                "Path=/api/admin/logs",
                "HttpOnly",
                "SameSite=Strict",
                f"Max-Age={max(0, int(session.expires_at - self.api.clock()))}",
            ]
            if self.api.config.cookie_secure:
                parts.append("Secure")
            return "; ".join(parts)

        def _handle_logout(self) -> None:
            if not self._require_configured():
                return
            token = self._bearer_token()
            if not token or self.api.sessions.verify(token) is None:
                self._unauthorized()
                return
            self.api.sessions.revoke(token)
            log_event("admin_logs_logout", component="logs_api", client=self._client_key())
            cookies = (
                f"{SESSION_COOKIE}=; Path=/api/admin/logs; HttpOnly; SameSite=Strict; Max-Age=0",
            )
            self._send_json(200, {"ok": True}, cookies=cookies)

        def _handle_logs(self) -> None:
            if not self._require_configured():
                return
            session = self._authenticate()
            if session is None:
                log_event(
                    "admin_logs_denied",
                    status="skipped",
                    component="logs_api",
                    client=self._client_key(),
                )
                self._unauthorized()
                return
            query = parse_qs(urlparse(self.path).query)
            filters = LogFilters(
                status=parse_values(query.get("status")),
                event=parse_values(query.get("event")),
                level=parse_values(query.get("level")),
                component=parse_values(query.get("component")),
                cycle_id=(query.get("cycle_id") or [""])[0].strip()[:64],
                listing_id=(query.get("listing_id") or [""])[0].strip()[:64],
                since=parse_moment((query.get("since") or [None])[0]),
                until=parse_moment((query.get("until") or [None])[0]),
                search=(query.get("q") or [""])[0].strip()[:200],
            )
            limit = self._limit(query)
            before = parse_moment((query.get("before") or [None])[0])
            payload = self.api.read_feed(filters=filters, before=before, limit=limit)
            log_event(
                "admin_logs_viewed",
                component="logs_api",
                client=self._client_key(),
                count=payload["count"],
                status_filter=",".join(filters.status) or None,
                event_filter=",".join(filters.event) or None,
            )
            self._send_json(200, payload)

        def _limit(self, query: Mapping[str, list[str]]) -> int:
            config = self.api.config
            raw = (query.get("limit") or [""])[0]
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = config.default_limit
            if value <= 0:
                value = config.default_limit
            return min(value, config.max_limit)

    return LogsApiHandler


class LogsApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, app: LogsApi):
        self.app = app
        super().__init__(address, handler)


def create_server(
    config: LogsApiConfig,
    *,
    port: int | None = None,
    host: str | None = None,
    app: LogsApi | None = None,
) -> LogsApiServer:
    application = app or LogsApi(config)
    handler = build_handler(application)
    address = (host or config.bind_host, config.port if port is None else port)
    return LogsApiServer(address, handler, app=application)


def start_in_thread(config: LogsApiConfig) -> tuple[LogsApiServer, threading.Thread]:
    """Serve the API from a daemon thread inside the bot process."""
    server = create_server(config)
    thread = threading.Thread(target=server.serve_forever, name="admin-logs-api", daemon=True)
    thread.start()
    return server, thread


def start_from_env(env: Mapping[str, str] | None = None) -> LogsApiServer | None:
    """Start the API when ``ADMIN_LOGS_ENABLED=1``; never breaks the bot loop."""
    config = LogsApiConfig.from_env(env)
    if not config.enabled:
        return None
    if not config.configured:
        log_event(
            "admin_logs_api_disabled",
            status="skipped",
            component="logs_api",
            problems="; ".join(config.problems),
        )
        return None
    try:
        server, _ = start_in_thread(config)
    except OSError as error:
        log_event(
            "admin_logs_api_failed",
            status="failed",
            component="logs_api",
            error=str(error),
        )
        return None
    host, port = server.server_address[:2]
    log_event(
        "admin_logs_api_started",
        component="logs_api",
        host=host,
        port=port,
        ttl_seconds=config.session_ttl_seconds,
        cors_origin=config.cors_origin or None,
    )
    return server


def start_admin_logs_api(env: Mapping[str, str] | None = None) -> LogsApiServer | None:
    """Entry point used by the bot: start the viewer when it is enabled."""
    return start_from_env(env)


def main() -> None:
    """Run the API as a standalone process (local machine or a tunnel)."""
    config = LogsApiConfig.from_env()
    configure_logging(config.api_log_path)
    if not config.configured:
        log_event(
            "admin_logs_api_disabled",
            status="skipped",
            component="logs_api",
            problems="; ".join(config.problems),
        )
        raise SystemExit("; ".join(config.problems))
    server = create_server(config)
    host, port = server.server_address[:2]
    log_event(
        "admin_logs_api_started",
        component="logs_api",
        host=host,
        port=port,
        standalone=True,
        log_path=config.log_path,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log_event("admin_logs_api_stopped", status="skipped", component="logs_api")


if __name__ == "__main__":
    main()
