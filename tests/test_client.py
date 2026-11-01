from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from lyfta_mcp.client import LyftaClient, LyftaError, TokenBucket

from .fixtures import SQUAT, collections_response


async def test_sends_server_key_and_params(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    route = lyfta.get("/workouts").respond(json={"status": True, "workouts": []})
    await client.workouts(limit=500, page=2, from_date="2026-09-01")
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-lyfta-key"
    assert request.url.params["limit"] == "100"  # capped by the API
    assert request.url.params["page"] == "2"
    assert request.url.params["from"] == "2026-09-01"
    assert "to" not in request.url.params


async def test_retries_429_honoring_retry_after(
    client: LyftaClient, lyfta: respx.MockRouter, sleeps: list[float]
) -> None:
    route = lyfta.get("/templates").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"status": True, "templates": []}),
        ]
    )
    data = await client.templates()
    assert data["templates"] == []
    assert route.call_count == 2
    assert sleeps == [2.0]


async def test_retries_5xx_with_backoff_then_raises(
    client: LyftaClient, lyfta: respx.MockRouter, sleeps: list[float]
) -> None:
    route = lyfta.get("/templates").respond(503)
    with pytest.raises(LyftaError) as err:
        await client.templates()
    assert err.value.status == 503
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


async def test_rate_limit_exhausted(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/templates").respond(429)
    with pytest.raises(LyftaError) as err:
        await client.templates()
    assert err.value.status == 429
    assert "rate limit" in err.value.message


async def test_invalid_api_key_is_explained(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/collections").respond(401, json={"status": False, "message": "Invalid API key"})
    with pytest.raises(LyftaError) as err:
        await client.collections()
    assert err.value.status == 401
    assert "Generate a new one" in err.value.message


async def test_status_false_body_raises(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.post("/templates").respond(200, json={"status": False, "message": "Collection not found"})
    with pytest.raises(LyftaError, match="Collection not found"):
        await client.create_template(99, {"title": "x"})


async def test_cache_hits_and_write_invalidation(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    listing = lyfta.get("/collections").respond(json=collections_response([]))
    lyfta.post("/collections").respond(json={"status": True, "data": {"id": 5, "title": "P"}})

    await client.collections()
    await client.collections()
    assert listing.call_count == 1

    await client.create_collection("P", None, None)
    await client.collections()
    assert listing.call_count == 2


def exercises_page(ids: list[str | None]) -> dict[str, object]:
    # Shaped like the real API: limit capped at 100, no page count, null-id padding rows.
    rows = [{**SQUAT, "id": i} for i in ids]
    return {
        "status": True,
        "count": len(rows),
        "current_page": None,
        "limit": 100,
        "exercises": rows,
    }


async def test_performed_exercises_stops_at_null_padding(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    pages = {
        "1": exercises_page([str(i) for i in range(100)]),
        "2": exercises_page(["100", "101", *([None] * 98)]),
        "3": exercises_page([None] * 100),
    }
    route = lyfta.get("/exercises").mock(
        side_effect=lambda req: httpx.Response(200, json=pages[req.url.params["page"]])
    )
    exercises = await client.performed_exercises()
    assert len(exercises) == 102
    assert route.call_count == 2
    assert route.calls.last.request.url.params["limit"] == "100"


async def test_performed_exercises_stops_on_repeated_page(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    route = lyfta.get("/exercises").respond(json=exercises_page([str(i) for i in range(100)]))
    exercises = await client.performed_exercises()
    assert len(exercises) == 100
    assert route.call_count == 2


async def test_create_collection_body(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    route = lyfta.post("/collections").respond(json={"status": True, "data": {"id": 1}})
    await client.create_collection("Strength", None, "strength")
    assert json.loads(route.calls.last.request.content) == {
        "collection": {"title": "Strength", "goal": "strength"}
    }


async def test_token_bucket_throttles() -> None:
    bucket = TokenBucket(rate=3, per=0.3)
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    assert time.monotonic() - start >= 0.08
