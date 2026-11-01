from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import respx
from mcp.server.mcpserver import MCPServer

from lyfta_mcp.client import LyftaClient
from lyfta_mcp.config import Settings
from lyfta_mcp.server import create_server

BASE = "https://lyfta.test"
API = f"{BASE}/api/v1"
API_KEY = "test-lyfta-key"
AUTH_TOKEN = "test-access-token-0123456789abcdef"


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key=API_KEY, auth_token=AUTH_TOKEN, base_url=BASE)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def client(settings: Settings, sleeps: list[float]) -> AsyncIterator[LyftaClient]:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    c = LyftaClient(settings, sleep=fake_sleep)
    yield c
    await c.aclose()


@pytest.fixture
def lyfta() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
def server(settings: Settings, client: LyftaClient) -> MCPServer:
    return create_server(settings, client)


async def call(server: MCPServer, tool: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.call_tool(tool, args or {})
    assert not getattr(result, "is_error", False), result
    return json.loads(result.content[0].text)  # type: ignore[union-attr]
