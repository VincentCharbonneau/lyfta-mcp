"""Per-request Lyfta API key resolution.

stdio: one user, key read from LYFTA_API_KEY at startup.
http:  every request carries the caller's own key in `Authorization: Bearer <key>`; the
       middleware stores it in a ContextVar for the duration of that request only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from contextvars import ContextVar
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_request_key: ContextVar[str | None] = ContextVar("lyfta_api_key", default=None)
_stdio_key: str | None = None


class AuthError(Exception):
    pass


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def configure_stdio(api_key: str) -> None:
    """Set the single-user key. Only called in stdio mode."""
    global _stdio_key
    _stdio_key = api_key


def current_api_key() -> str:
    key = _request_key.get() or _stdio_key
    if not key:
        raise AuthError("No Lyfta API key for this request.")
    return key


async def _send_json(
    send: Send,
    status: int,
    body: dict[str, str],
    extra_headers: Iterable[tuple[bytes, bytes]] = (),
) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class BearerKeyMiddleware:
    """ASGI middleware that reads the caller's Lyfta key from the Authorization header."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowlist_sha256: frozenset[str] = frozenset(),
        exempt_paths: Iterable[str] = ("/health",),
        exempt_prefixes: Iterable[str] = ("/.well-known/",),
    ) -> None:
        self.app = app
        self.allowlist = allowlist_sha256
        self.exempt_paths = frozenset(exempt_paths)
        self.exempt_prefixes = tuple(exempt_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path in self.exempt_paths or path.startswith(self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        key = _bearer_from_scope(scope)
        if not key:
            await _send_json(
                send,
                401,
                {
                    "error": "missing_api_key",
                    "message": "Send your Lyfta API key as 'Authorization: Bearer <key>'.",
                },
                [(b"www-authenticate", b'Bearer realm="lyfta-mcp"')],
            )
            return
        if self.allowlist and hash_key(key) not in self.allowlist:
            await _send_json(
                send,
                403,
                {"error": "key_not_allowed", "message": "This server does not accept that key."},
            )
            return

        token = _request_key.set(key)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_key.reset(token)


def _bearer_from_scope(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            header: str = bytes(value).decode("latin-1")
            scheme, _, credentials = header.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
            return None
    return None
