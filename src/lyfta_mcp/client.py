"""Async HTTP client for the Lyfta API (https://my.lyfta.app/community/api).

The client holds the server's single Lyfta API key; it is sent only to the Lyfta API.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
import truststore

from .config import Settings

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 30.0
RATE_PER_MINUTE = 60
EXERCISE_PAGE_SIZE = 100  # server-side cap for GET /exercises

TTL_CATALOG = 600.0  # performed exercises, library searches, weight unit
TTL_DEFAULT = 60.0

INVALID_KEY_MESSAGE = (
    "Lyfta rejected the API key (Invalid API key). Generate a new one at "
    "https://my.lyfta.app/community/api — regenerating revokes the previous key."
)

Params = Mapping[str, Any]


class LyftaError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Lyfta API error {status}: {message}")
        self.status = status
        self.message = message


class TokenBucket:
    """Allows `rate` requests per `per` seconds, refilling continuously."""

    def __init__(self, rate: int = RATE_PER_MINUTE, per: float = 60.0) -> None:
        self.capacity = float(rate)
        self.tokens = float(rate)
        self.fill_rate = rate / per
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.fill_rate
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.fill_rate)


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[float, Any]] = {}

    @staticmethod
    def _params_key(params: Params | None) -> str:
        if not params:
            return ""
        return "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)

    def get(self, path: str, params: Params | None) -> Any | None:
        entry = self._data.get((path, self._params_key(params)))
        if entry is None:
            return None
        expires, value = entry
        if expires < time.monotonic():
            self._data.pop((path, self._params_key(params)), None)
            return None
        return value

    def set(self, path: str, params: Params | None, value: Any, ttl: float) -> None:
        self._data[(path, self._params_key(params))] = (time.monotonic() + ttl, value)
        if len(self._data) > 2000:
            self.prune()

    def invalidate(self, path_prefixes: tuple[str, ...]) -> None:
        for key in [k for k in self._data if k[0].startswith(path_prefixes)]:
            del self._data[key]

    def prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, (expires, _) in self._data.items() if expires < now]:
            del self._data[key]


class LyftaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            transport=transport,
            # OS trust store, so TLS-inspecting proxies and antivirus roots are honored.
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            headers={
                "Accept": "application/json",
                "User-Agent": "lyfta-mcp",
                "Authorization": f"Bearer {settings.api_key}",
            },
        )
        self._sleep = sleep
        self._bucket = TokenBucket()
        self.cache = TTLCache()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- low level -----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: Any = None,
    ) -> Any:
        clean_params = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        url = API_PREFIX + path

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._bucket.acquire()
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=clean_params,
                    json=json,
                )
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise LyftaError(0, f"Could not reach Lyfta: {type(exc).__name__}") from exc
                await self._sleep(2 ** (attempt - 1))
                continue

            log.debug("%s %s -> %s", method, path, response.status_code)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    break
                await self._sleep(_retry_delay(response, attempt))
                continue
            return _parse(response)

        if response.status_code == 429:
            raise LyftaError(429, "Lyfta rate limit exceeded (60/min, 5,000/day). Try again later.")
        raise LyftaError(response.status_code, "Lyfta server error. Try again later.")

    async def get(
        self, path: str, params: Params | None = None, *, ttl: float = TTL_DEFAULT
    ) -> Any:
        cached = self.cache.get(path, params)
        if cached is not None:
            return cached
        data = await self.request("GET", path, params=params)
        if ttl > 0:
            self.cache.set(path, params, data, ttl)
        return data

    async def post(self, path: str, body: Any) -> Any:
        data = await self.request("POST", path, json=body)
        self.cache.invalidate(("/collections", "/templates"))
        return data

    # --- endpoints -----------------------------------------------------------------

    async def workouts(
        self,
        *,
        limit: int = 20,
        page: int = 1,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        params = {"limit": min(limit, 100), "page": page, "from": from_date, "to": to_date}
        data: dict[str, Any] = await self.get("/workouts", params)
        return data

    async def workout_summaries(self, *, limit: int = 50, page: int = 1) -> dict[str, Any]:
        params = {"limit": min(limit, 1000), "page": page}
        data: dict[str, Any] = await self.get("/workouts/summary", params)
        return data

    async def performed_exercises(self) -> list[dict[str, Any]]:
        """Every exercise the user has performed, across pages.

        The endpoint caps `limit` at 100, reports no page count, and past the last exercise
        keeps returning rows with a null id, so stop at the first page that is short on
        valid rows or adds nothing new.
        """
        seen: dict[str, dict[str, Any]] = {}
        for page in range(1, 21):
            data = await self.get(
                "/exercises", {"limit": EXERCISE_PAGE_SIZE, "page": page}, ttl=TTL_CATALOG
            )
            valid = [ex for ex in data.get("exercises") or [] if ex.get("id") not in (None, "")]
            new = [ex for ex in valid if str(ex["id"]) not in seen]
            for ex in new:
                seen[str(ex["id"])] = ex
            if len(valid) < EXERCISE_PAGE_SIZE or not new:
                break
        return list(seen.values())

    async def search_library(
        self, search: str, *, limit: int = 10, offset: int = 0
    ) -> dict[str, Any]:
        params = {"search": search, "limit": limit, "offset": offset}
        data = await self.get("/exercises/library", params, ttl=TTL_CATALOG)
        results: dict[str, Any] = data.get("data") or {"results": [], "pagination": {}}
        return results

    async def exercise_progress(self, exercise_id: int, duration_days: int) -> dict[str, Any]:
        params = {"exercise_id": exercise_id, "duration": duration_days}
        data: dict[str, Any] = await self.get("/exercises/progress", params)
        return data

    async def collections(self, *, limit: int = 100, page: int = 1) -> dict[str, Any]:
        params = {"limit": limit, "page": page}
        data: dict[str, Any] = await self.get("/collections", params)
        return data

    async def all_collections(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            data = await self.collections(limit=100, page=page)
            items.extend(data.get("collections") or [])
            if page >= int(data.get("total_pages") or 1):
                break
            page += 1
        return items

    async def collection(self, collection_id: int, *, full: bool = False) -> Any:
        params = {"view": "full" if full else "summary"}
        data = await self.get(f"/collections/{collection_id}", params)
        return data.get("data")

    async def templates(self, *, limit: int = 50, page: int = 1) -> dict[str, Any]:
        params = {"limit": limit, "page": page}
        data: dict[str, Any] = await self.get("/templates", params)
        return data

    async def template(self, template_id: int) -> Any:
        data = await self.get(f"/templates/{template_id}")
        return data.get("data")

    async def schedule(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        include_completed: bool = False,
    ) -> dict[str, Any]:
        params = {"from": from_date, "to": to_date, "include_completed": include_completed}
        data: dict[str, Any] = await self.get("/schedule", params)
        return data

    async def create_collection(self, title: str, description: str | None, goal: str | None) -> Any:
        collection: dict[str, str] = {"title": title}
        if description:
            collection["description"] = description
        if goal:
            collection["goal"] = goal
        data = await self.post("/collections", {"collection": collection})
        return data.get("data")

    async def create_template(self, collection_id: int, workout: dict[str, Any]) -> Any:
        data = await self.post("/templates", {"collectionId": collection_id, "workout": workout})
        return data.get("data")


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_AFTER)
        except ValueError:
            pass
    return float(2 ** (attempt - 1))


def _parse(response: httpx.Response) -> Any:
    try:
        data = response.json()
    except ValueError:
        data = None

    message = ""
    if isinstance(data, dict):
        message = str(data.get("message") or data.get("error") or "")

    if "invalid api key" in message.lower() or response.status_code == 401:
        raise LyftaError(401, INVALID_KEY_MESSAGE)
    if response.status_code >= 400:
        raise LyftaError(
            response.status_code, message or response.reason_phrase or "Request failed"
        )
    if data is None:
        raise LyftaError(response.status_code, "Lyfta returned a non-JSON response.")
    if isinstance(data, dict) and data.get("status") is False:
        raise LyftaError(response.status_code, message or "Request failed")
    return data
