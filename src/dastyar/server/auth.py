"""Access control for a server that can run shell commands.

* Host header allow-list (blocks DNS rebinding) when no token is configured.
* Origin must match Host on WebSockets and state-changing requests (blocks cross-site
  WebSocket hijacking / CSRF from any web page the user happens to visit).
* Optional shared token (``DASTYAR_TOKEN``) -> login cookie; mandatory on non-loopback binds.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlparse

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
COOKIE_NAME = "dastyar_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def configured_token() -> str | None:
    return os.environ.get("DASTYAR_TOKEN") or None


def is_loopback(host: str) -> bool:
    return host.strip("[]").lower() in LOOPBACK_HOSTS


def cookie_value(token: str) -> str:
    return hmac.new(token.encode(), b"dastyar-session-v1", hashlib.sha256).hexdigest()


def token_matches(supplied: str, token: str) -> bool:
    a = hashlib.sha256(supplied.encode()).digest()
    b = hashlib.sha256(token.encode()).digest()
    return hmac.compare_digest(a, b)


def hostname_of(host_header: str) -> str:
    host_header = host_header.strip()
    if host_header.startswith("["):  # [::1]:8765
        return host_header[1 : host_header.find("]")].lower()
    return host_header.rsplit(":", 1)[0].lower() if ":" in host_header else host_header.lower()


def is_authed(conn: HTTPConnection, token: str | None) -> bool:
    if not token:
        return True
    supplied = conn.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(supplied, cookie_value(token))


class LoginThrottle:
    """Tiny in-memory brute-force limiter: 8 failures per minute per client."""

    def __init__(self, limit: int = 8, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self.failures: dict[str, list[float]] = {}

    def blocked(self, client: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.failures.get(client, []) if now - t < self.window]
        self.failures[client] = recent
        return len(recent) >= self.limit

    def fail(self, client: str) -> None:
        self.failures.setdefault(client, []).append(time.monotonic())


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, token: str | None, extra_hosts: set[str] | None = None):
        self.app = app
        self.token = token
        self.allowed_hosts = LOOPBACK_HOSTS | {h.lower() for h in (extra_hosts or set())}

    async def _deny(self, scope: Scope, receive: Receive, send: Send, code: int, msg: str) -> None:
        if scope["type"] == "websocket":
            await receive()  # consume websocket.connect
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": msg}, status_code=code)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if not self.token and hostname_of(host) not in self.allowed_hosts:
            return await self._deny(scope, receive, send, 403, "host not allowed")

        origin = headers.get("origin")
        method = scope.get("method", "GET")
        if origin is not None and (scope["type"] == "websocket" or method not in SAFE_METHODS):
            if urlparse(origin).netloc.lower() != host.lower():
                return await self._deny(scope, receive, send, 403, "cross-origin request refused")

        path = scope.get("path", "")
        if (
            self.token
            and path.startswith("/api/")
            and not path.startswith("/api/auth/")
            and not is_authed(HTTPConnection(scope), self.token)
        ):
            return await self._deny(scope, receive, send, 401, "authentication required")

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out["X-Frame-Options"] = "DENY"  # the approval buttons must not be clickjackable
                out["X-Content-Type-Options"] = "nosniff"
                out["Referrer-Policy"] = "no-referrer"
                out["Content-Security-Policy"] = "frame-ancestors 'none'"
                if path.startswith("/api/"):
                    out["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
