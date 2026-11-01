from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from lyfta_mcp.__main__ import build_app
from lyfta_mcp.auth import BearerTokenMiddleware
from lyfta_mcp.client import LyftaClient
from lyfta_mcp.config import Settings

from .conftest import API_KEY, AUTH_TOKEN, BASE
from .fixtures import collections_response


async def ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def asgi_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {AUTH_TOKEN}x"},
        {"Authorization": f"Bearer {API_KEY}"},  # the Lyfta key is not the access token
    ],
)
async def test_rejects_missing_or_wrong_token(headers: dict[str, str]) -> None:
    async with asgi_client(BearerTokenMiddleware(ok_app, token=AUTH_TOKEN)) as c:
        response = await c.post("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_accepts_the_token() -> None:
    async with asgi_client(BearerTokenMiddleware(ok_app, token=AUTH_TOKEN)) as c:
        response = await c.post("/mcp", headers={"Authorization": f"bearer {AUTH_TOKEN}"})
    assert response.status_code == 200


async def test_health_is_exempt() -> None:
    async with asgi_client(BearerTokenMiddleware(ok_app, token=AUTH_TOKEN)) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/other")).status_code == 401


def test_token_required() -> None:
    with pytest.raises(ValueError):
        BearerTokenMiddleware(ok_app, token="")


# --- end to end through the real Streamable HTTP app (in-process, no sockets) -------------

lyfta_requests: list[httpx.Request] = []


def lyfta_mock(request: httpx.Request) -> httpx.Response:
    lyfta_requests.append(request)
    if request.url.path == "/api/v1/collections":
        return httpx.Response(200, json=collections_response([{"id": 1, "title": "Strength"}]))
    return httpx.Response(404, json={"status": False, "message": "not mocked"})


@asynccontextmanager
async def running_app() -> AsyncIterator[BearerTokenMiddleware]:
    """The app with its lifespan running (kept in one task, as anyio requires)."""
    settings = Settings(api_key=API_KEY, auth_token=AUTH_TOKEN, base_url=BASE)
    client = LyftaClient(settings, transport=httpx.MockTransport(lyfta_mock))
    app = build_app(settings, host="0.0.0.0", client=client)
    starlette_app = app.app
    async with starlette_app.router.lifespan_context(starlette_app):  # type: ignore[attr-defined]
        yield app
    await client.aclose()


async def test_http_health() -> None:
    async with running_app() as app, asgi_client(app) as c:
        response = await c.get("/health")
    assert response.json() == {"status": "ok"}


async def test_http_mcp_requires_token() -> None:
    async with running_app() as app, asgi_client(app) as c:
        response = await c.post("/mcp", json={})
    assert response.status_code == 401


async def test_http_end_to_end_uses_server_key() -> None:
    lyfta_requests.clear()
    async with running_app() as app:
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        async with http, Client(streamable_http_client("http://test/mcp", http_client=http)) as mcp:
            tools = await mcp.list_tools()
            result = await mcp.call_tool("list_programs", {})
    assert len(tools.tools) == 14
    assert not result.is_error, result
    assert json.loads(result.content[0].text)["programs"][0]["title"] == "Strength"  # type: ignore[union-attr]
    # The client's access token never reaches Lyfta; the server's own key does.
    assert lyfta_requests[-1].headers["Authorization"] == f"Bearer {API_KEY}"
