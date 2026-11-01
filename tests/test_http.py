from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from lyfta_mcp.__main__ import build_http_app
from lyfta_mcp.auth import BearerKeyMiddleware, current_api_key, hash_key
from lyfta_mcp.client import LyftaClient
from lyfta_mcp.config import Settings

from .conftest import BASE
from .fixtures import collections_response


async def echo_key_app(scope: Any, receive: Any, send: Any) -> None:
    """Returns the key the middleware stored, after yielding to other requests."""
    await asyncio.sleep(random.random() / 50)
    body = json.dumps({"key": current_api_key()}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def asgi_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_missing_header_is_401() -> None:
    async with asgi_client(BearerKeyMiddleware(echo_key_app)) as c:
        response = await c.post("/mcp")
    assert response.status_code == 401
    assert response.json()["error"] == "missing_api_key"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_non_bearer_header_is_401() -> None:
    async with asgi_client(BearerKeyMiddleware(echo_key_app)) as c:
        response = await c.post("/mcp", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


async def test_health_is_exempt() -> None:
    async def ok_app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async with asgi_client(BearerKeyMiddleware(ok_app)) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/other")).status_code == 401


async def test_allowlist() -> None:
    app = BearerKeyMiddleware(echo_key_app, allowlist_sha256=frozenset({hash_key("mine")}))
    async with asgi_client(app) as c:
        denied = await c.post("/mcp", headers={"Authorization": "Bearer someone-else"})
        allowed = await c.post("/mcp", headers={"Authorization": "Bearer mine"})
    assert denied.status_code == 403
    assert allowed.json() == {"key": "mine"}


async def test_contextvar_isolated_across_concurrent_requests() -> None:
    async with asgi_client(BearerKeyMiddleware(echo_key_app)) as c:

        async def one(i: int) -> str:
            r = await c.post("/mcp", headers={"Authorization": f"Bearer key-{i}"})
            return str(r.json()["key"])

        keys = await asyncio.gather(*(one(i) for i in range(30)))
    assert keys == [f"key-{i}" for i in range(30)]


# --- end to end through the real Streamable HTTP app (in-process, no sockets) -------------


def lyfta_mock(request: httpx.Request) -> httpx.Response:
    """Fake Lyfta: names the caller's only program after their key."""
    key = request.headers["Authorization"].removeprefix("Bearer ")
    if request.url.path == "/api/v1/collections":
        return httpx.Response(
            200, json=collections_response([{"id": 1, "title": f"program of {key}"}])
        )
    return httpx.Response(404, json={"status": False, "message": "not mocked"})


@asynccontextmanager
async def running_app() -> AsyncIterator[BearerKeyMiddleware]:
    """The HTTP app with its lifespan running (kept in one task, as anyio requires)."""
    settings = Settings(base_url=BASE)
    client = LyftaClient(settings, transport=httpx.MockTransport(lyfta_mock))
    app = build_http_app(settings, host="0.0.0.0", client=client)
    starlette_app = app.app
    async with starlette_app.router.lifespan_context(starlette_app):  # type: ignore[attr-defined]
        yield app
    await client.aclose()


async def test_http_health() -> None:
    async with running_app() as http_app, asgi_client(http_app) as c:
        response = await c.get("/health")
    assert response.json() == {"status": "ok"}


async def test_http_mcp_requires_key() -> None:
    async with running_app() as http_app, asgi_client(http_app) as c:
        response = await c.post("/mcp", json={})
    assert response.status_code == 401


async def list_programs_as(app: BearerKeyMiddleware, key: str) -> str:
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers={"Authorization": f"Bearer {key}"},
    )
    async with http, Client(streamable_http_client("http://test/mcp", http_client=http)) as mcp:
        result = await mcp.call_tool("list_programs", {})
    assert not result.is_error, result
    return str(json.loads(result.content[0].text)["programs"][0]["title"])  # type: ignore[union-attr]


async def test_http_end_to_end_keys_do_not_leak() -> None:
    async with running_app() as http_app:
        titles = await asyncio.gather(*(list_programs_as(http_app, f"user{i}") for i in range(6)))
    assert titles == [f"program of user{i}" for i in range(6)]
