"""Resolve exercise names (or ids) to exact Lyfta catalog entries.

Templates only work when exercise_id, excercise_name, exercise_type and exercise_image match
the catalog exactly, so Claude refers to exercises by name and this module finds the entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .auth import hash_key
from .client import LyftaClient
from .mappings import decode_exercise_meta


@dataclass(frozen=True)
class CatalogEntry:
    id: int
    name: str
    exercise_type: str | None
    image_name: str
    equipment: list[str] = field(default_factory=list)
    body_parts: list[str] = field(default_factory=list)
    target_muscles: list[str] = field(default_factory=list)
    synergist_muscles: list[str] = field(default_factory=list)
    performed: bool = False

    @classmethod
    def from_api(cls, raw: dict[str, Any], *, performed: bool) -> CatalogEntry:
        meta = decode_exercise_meta(raw)
        return cls(
            id=int(raw["id"]),
            name=str(raw.get("name") or ""),
            exercise_type=raw.get("exercise_type") or None,
            image_name=str(raw.get("image_name") or ""),
            performed=performed,
            **meta,
        )

    def card(self) -> dict[str, Any]:
        """Compact description for tool output."""
        card: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.equipment:
            card["equipment"] = ", ".join(self.equipment)
        if self.target_muscles:
            card["target_muscles"] = ", ".join(self.target_muscles)
        if self.exercise_type:
            card["exercise_type"] = self.exercise_type
        if self.performed:
            card["performed_before"] = True
        return card


@dataclass(frozen=True)
class Ambiguous:
    query: str
    candidates: list[CatalogEntry]
    note: str | None = None


@dataclass(frozen=True)
class NotFound:
    query: str
    note: str | None = None


Resolution = CatalogEntry | Ambiguous | NotFound

MAX_CANDIDATES = 5


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


class ExerciseCatalog:
    """Name/id resolution with a per-user memory of library entries seen in searches.

    The library has no get-by-id endpoint, so ids are resolved against the user's
    performed exercises first and then against library entries returned by earlier
    searches (which is how Claude learns a library id in the first place).
    """

    def __init__(self, client: LyftaClient) -> None:
        self.client = client
        self._seen: dict[str, dict[int, CatalogEntry]] = {}

    def _remember(self, api_key: str, entries: list[CatalogEntry]) -> None:
        seen = self._seen.setdefault(hash_key(api_key), {})
        for entry in entries:
            seen[entry.id] = entry
        if len(seen) > 5000:
            for key in list(seen)[:1000]:
                del seen[key]

    async def performed(self, api_key: str) -> list[CatalogEntry]:
        raw = await self.client.performed_exercises(api_key)
        entries = []
        for item in raw:
            try:
                entries.append(CatalogEntry.from_api(item, performed=True))
            except (KeyError, ValueError, TypeError):
                continue
        return entries

    async def search(self, api_key: str, query: str, limit: int = 10) -> list[CatalogEntry]:
        data = await self.client.search_library(api_key, query, limit=limit)
        entries = []
        for item in data.get("results") or []:
            try:
                entries.append(CatalogEntry.from_api(item, performed=False))
            except (KeyError, ValueError, TypeError):
                continue
        self._remember(api_key, entries)
        return entries

    async def resolve(self, api_key: str, name_or_id: str | int) -> Resolution:
        performed = await self.performed(api_key)
        performed_by_id = {e.id: e for e in performed}

        query = str(name_or_id).strip()
        if isinstance(name_or_id, int) or query.isdigit():
            exercise_id = int(query)
            if exercise_id in performed_by_id:
                return performed_by_id[exercise_id]
            seen = self._seen.get(hash_key(api_key), {}).get(exercise_id)
            if seen is not None:
                return self._finalize(seen, performed_by_id, query)
            return NotFound(
                query,
                note="That id is not in your performed exercises or recent library searches. "
                "Search the library by name first, or pass the exercise name.",
            )

        wanted = _norm(query)
        for entry in performed:
            if _norm(entry.name) == wanted:
                return entry

        library = await self.search(api_key, query, limit=10)
        exact = [e for e in library if _norm(e.name) == wanted]
        if len(exact) == 1:
            return self._finalize(exact[0], performed_by_id, query)

        candidates: list[CatalogEntry] = []
        for entry in [*exact, *(e for e in performed if wanted in _norm(e.name)), *library]:
            if entry.id not in {c.id for c in candidates}:
                candidates.append(entry)
        if not candidates:
            return NotFound(query)
        note = "Several exercises share this exact name." if len(exact) > 1 else None
        return Ambiguous(query, candidates[:MAX_CANDIDATES], note)

    @staticmethod
    def _finalize(
        entry: CatalogEntry, performed_by_id: dict[int, CatalogEntry], query: str
    ) -> Resolution:
        if entry.exercise_type:
            return entry
        known = performed_by_id.get(entry.id)
        if known is not None and known.exercise_type:
            return known
        return Ambiguous(
            query,
            [entry],
            note="The Lyfta library does not list an exercise_type for this exercise and you "
            "have not performed it, so a template could not match the catalog exactly. "
            "Log it once in the Lyfta app, or pick an exercise you have performed.",
        )
