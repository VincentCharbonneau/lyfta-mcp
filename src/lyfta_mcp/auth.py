"""Access control for the MCP endpoint.

The Lyfta API key stays on the server. Clients prove they may use it with a separate
access token (MCP_AUTH_TOKEN) sent as `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


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


class BearerTokenMiddleware:
    """ASGI middleware that only lets requests carrying the server's access token through."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        exempt_paths: Iterable[str] = ("/health",),
        exempt_prefixes: Iterable[str] = ("/.well-known/",),
    ) -> None:
        if not token:
            raise ValueError("an access token is required")
        self.app = app
        self._token = token.encode()
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

        presented = _bearer_from_scope(scope)
        if presented is None or not hmac.compare_digest(presented.encode(), self._token):
            await _send_json(
                send,
                401,
                {
                    "error": "unauthorized",
                    "message": "Send the server's access token as 'Authorization: Bearer <token>'.",
                },
                [(b"www-authenticate", b'Bearer realm="lyfta-mcp"')],
            )
            return
        await self.app(scope, receive, send)


def _bearer_from_scope(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            header: str = bytes(value).decode("latin-1")
            scheme, _, credentials = header.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
            return None
    return None
