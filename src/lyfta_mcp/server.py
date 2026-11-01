"""MCP server instance and tool definitions."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .analytics import lift_trend, progress_points, training_summary
from .auth import AuthError, current_api_key
from .catalog import Ambiguous, CatalogEntry, ExerciseCatalog, NotFound, Resolution
from .client import TTL_CATALOG, LyftaClient, LyftaError
from .config import Settings
from .models import ExerciseInput, build_workout_payload, preview_lines

WORKOUT_FETCH_CAP = 500

INSTRUCTIONS = """\
Unofficial connector for the Lyfta workout tracker.

- Weights are in the user's own unit (kg or lb); every tool that shows weights includes
  `weight_unit`. Never convert silently.
- Refer to exercises by name; the server resolves catalog ids. If a name is ambiguous the tool
  returns candidates: ask the user or pick the obvious one, then retry with its exact name or id.
- Writing is limited to creating programs (collections) and workout templates with target
  sets/reps/weights/RIR. Always call the write tool first with confirm=false, show the preview
  to the user, and only call it again with confirm=true after they approve.
- The Lyfta API has no edit or delete, no scheduling, and no logging of completed workouts.
  To change a workout, create a new version (e.g. "Squat Day v2") and tell the user to delete
  the old one in the Lyfta app.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)

SET_FIELDS = ("weight", "reps", "from_reps", "to_reps", "rir", "duration", "distance")
RECORD_FIELDS = ("record_type", "record_level", "record_value")


def dump(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def prune(obj: Any) -> Any:
    """Drop None / empty values recursively to save tokens."""
    if isinstance(obj, dict):
        cleaned = {k: prune(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [prune(v) for v in obj]
    return obj


def compact_set(s: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: s.get(k) for k in SET_FIELDS}
    set_type = s.get("set_type_id")
    if set_type not in (None, "", 0, "0"):
        out["set_type_id"] = set_type
    if s.get("is_completed") in (False, "0", 0, "false"):
        out["completed"] = False
    if s.get("record_type"):
        out.update({k: s.get(k) for k in RECORD_FIELDS})
    return out


def compact_exercise(ex: dict[str, Any]) -> dict[str, Any]:
    return {
        "exercise_id": ex.get("exercise_id"),
        "name": ex.get("excercise_name") or ex.get("exercise_name"),
        "rest_seconds": ex.get("exercise_rest_time") or None,
        "note": ex.get("exercise_note"),
        "superset_group": ex.get("exercise_superset_id") or None,
        "rep_range": True if ex.get("is_rep_range_active") in (True, "1", 1, "true") else None,
        "sets": [compact_set(s) for s in ex.get("sets") or []],
    }


def compact_workout(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": w.get("id"),
        "title": w.get("title"),
        "date": w.get("workout_perform_date"),
        "duration": w.get("workout_duration"),
        "body_weight": w.get("body_weight"),
        "total_volume": w.get("total_volume"),
        "exercises": [compact_exercise(ex) for ex in w.get("exercises") or []],
    }


def compact_template(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "description": t.get("description"),
        "note": t.get("note"),
        "exercises": [compact_exercise(ex) for ex in t.get("exercises") or []],
    }


def resolution_error(res: Ambiguous | NotFound) -> dict[str, Any]:
    result: dict[str, Any]
    if isinstance(res, Ambiguous):
        result = prune(
            {
                "exercise": res.query,
                "status": "ambiguous",
                "note": res.note,
                "candidates": [c.card() for c in res.candidates],
            }
        )
    else:
        result = prune({"exercise": res.query, "status": "not_found", "note": res.note})
    return result


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


@contextmanager
def lyfta_errors() -> Iterator[None]:
    """Turn API and auth failures into tool errors Claude can read."""
    try:
        yield
    except LyftaError as exc:
        raise ToolError(exc.message) from exc
    except AuthError as exc:
        raise ToolError(str(exc)) from exc


def create_server(settings: Settings, client: LyftaClient | None = None) -> MCPServer:
    client = client or LyftaClient(settings)
    catalog = ExerciseCatalog(client)
    mcp: MCPServer = MCPServer(
        name="lyfta",
        title="Lyfta (unofficial)",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # --- helpers --------------------------------------------------------------------

    async def weight_unit(key: str) -> str:
        performed = await client.performed_exercises(key)
        if not performed:
            return "unknown"
        data = await client.get(
            key,
            "/exercises/progress",
            {"exercise_id": int(performed[0]["id"]), "duration": 30},
            ttl=TTL_CATALOG,
        )
        return str(data.get("weight_unit") or "unknown")

    async def fetch_workouts(
        key: str, start: date | None, end: date | None
    ) -> tuple[list[dict[str, Any]], bool]:
        workouts: list[dict[str, Any]] = []
        page = 1
        while len(workouts) < WORKOUT_FETCH_CAP:
            data = await client.workouts(
                key, limit=100, page=page, from_date=iso(start), to_date=iso(end)
            )
            batch = data.get("workouts") or []
            workouts.extend(batch)
            if not batch or page >= int(data.get("total_pages") or 1):
                return workouts[:WORKOUT_FETCH_CAP], False
            page += 1
        return workouts[:WORKOUT_FETCH_CAP], True

    # --- read tools -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def get_workouts(
        start_date: Annotated[
            date | None, Field(description="Inclusive UTC start date (YYYY-MM-DD).")
        ] = None,
        end_date: Annotated[
            date | None, Field(description="Inclusive UTC end date (YYYY-MM-DD).")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Completed workouts, newest first, with every exercise and set (weight, reps, RIR,
        PR records). Use for detailed session history; for many sessions at once prefer
        get_workout_summaries or get_training_summary."""
        with lyfta_errors():
            key = current_api_key()
            data = await client.workouts(
                key, limit=limit, page=page, from_date=iso(start_date), to_date=iso(end_date)
            )
            unit = await weight_unit(key)
        return dump(
            prune(
                {
                    "weight_unit": unit,
                    "page": data.get("current_page"),
                    "total_pages": data.get("total_pages"),
                    "total_records": data.get("total_records"),
                    "workouts": [compact_workout(w) for w in data.get("workouts") or []],
                }
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_workout_summaries(
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Workout headers only (id, date, title, duration, volume), newest first. Cheap way to
        see training frequency over a long period."""
        with lyfta_errors():
            key = current_api_key()
            data = await client.workout_summaries(key, limit=limit, page=page)
            unit = await weight_unit(key)
        rows = [
            {
                "id": w.get("id"),
                "date": w.get("workout_perform_date"),
                "title": w.get("title"),
                "duration": w.get("workout_duration"),
                "volume": w.get("total_volume"),
            }
            for w in data.get("workouts") or []
        ]
        return dump(
            prune(
                {
                    "weight_unit": unit,
                    "page": data.get("current_page"),
                    "total_pages": data.get("total_pages"),
                    "total_records": data.get("total_records"),
                    "workouts": rows,
                }
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_my_exercises() -> str:
        """Every exercise the user has performed, with id, equipment, body parts and target
        muscles. These are the exercises they actually do; prefer them when building
        programs."""
        with lyfta_errors():
            key = current_api_key()
            entries = await catalog.performed(key)
        rows = []
        for e in sorted(entries, key=lambda e: e.name.casefold()):
            card = e.card()
            card.pop("performed_before", None)
            if e.body_parts:
                card["body_parts"] = ", ".join(e.body_parts)
            rows.append(card)
        return dump({"count": len(rows), "exercises": rows})

    @mcp.tool(annotations=READ_ONLY)
    async def search_exercise_library(
        query: Annotated[str, Field(min_length=1, description="Search text, e.g. 'bench press'.")],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> str:
        """Search the full Lyfta exercise catalog by name. Returned ids can be passed to
        create_workout. Entries marked performed_before are ones the user already does."""
        with lyfta_errors():
            key = current_api_key()
            results = await catalog.search(key, query, limit=limit)
            performed_ids = {e.id for e in await catalog.performed(key)}
        cards = []
        for e in results:
            card = e.card()
            if e.id in performed_ids:
                card["performed_before"] = True
            cards.append(card)
        return dump({"query": query, "results": cards})

    @mcp.tool(annotations=READ_ONLY)
    async def get_exercise_progress(
        exercise: Annotated[
            str, Field(description="Exercise name (e.g. 'Barbell Squat') or numeric id.")
        ],
        days: Annotated[int, Field(ge=1, le=3650)] = 180,
    ) -> str:
        """Best set per training day for one exercise: best weight, reps, volume and estimated
        1RM (e1rm). Only exercises the user has performed have progress."""
        with lyfta_errors():
            key = current_api_key()
            res = await catalog.resolve(key, exercise)
            if not isinstance(res, CatalogEntry):
                return dump(resolution_error(res))
            data = await client.exercise_progress(key, res.id, days)
        return dump(
            {
                "exercise": res.name,
                "exercise_id": res.id,
                "days": days,
                "weight_unit": data.get("weight_unit") or "unknown",
                "points": progress_points(data),
            }
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_programs() -> str:
        """The user's programs (Lyfta collections) with id, title and number of workouts."""
        with lyfta_errors():
            items = await client.all_collections(current_api_key())
        rows = [
            prune(
                {
                    "id": c.get("id"),
                    "title": c.get("title"),
                    "workout_count": c.get("workout_count"),
                    "pinned": True if str(c.get("pinned")) == "1" else None,
                }
            )
            for c in items
        ]
        return dump({"count": len(rows), "programs": rows})

    @mcp.tool(annotations=READ_ONLY)
    async def get_program(
        program_id: Annotated[int, Field(description="Program id from list_programs.")],
        full: Annotated[
            bool, Field(description="Include every template's exercises and sets.")
        ] = False,
    ) -> str:
        """One program with its workout templates. full=false gives template cards
        (id, title, exercise count); full=true includes all exercises and target sets."""
        with lyfta_errors():
            key = current_api_key()
            data = await client.collection(key, program_id, full=full)
            if not data:
                raise ToolError(f"Program {program_id} not found. Use list_programs.")
            unit = await weight_unit(key) if full else None
        workouts = []
        for w in data.get("workouts") or []:
            if full and w.get("exercises") is not None:
                workouts.append(compact_template(w))
            elif full and isinstance(w.get("data"), dict):
                workouts.append(compact_template({"id": w.get("id"), **w["data"]}))
            else:
                workouts.append(
                    {
                        "id": w.get("id"),
                        "title": w.get("title"),
                        "exercises": w.get("exercise_count"),
                    }
                )
        return dump(
            prune(
                {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "description": data.get("description"),
                    "goal": data.get("goal"),
                    "weight_unit": unit,
                    "workouts": workouts,
                }
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_workout_templates(
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> str:
        """Saved workout templates as cards: id, title, exercise count and parent program."""
        with lyfta_errors():
            data = await client.templates(current_api_key(), limit=limit, page=page)
        rows = [
            prune(
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "exercises": t.get("exercise_count"),
                    "program_id": t.get("collection_id"),
                    "program": t.get("collection_title"),
                    "updated": t.get("date_updated"),
                }
            )
            for t in data.get("templates") or []
        ]
        return dump(
            prune(
                {
                    "page": data.get("current_page"),
                    "total_pages": data.get("total_pages"),
                    "total_records": data.get("total_records"),
                    "templates": rows,
                }
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_workout_template(
        template_id: Annotated[int, Field(description="Template id.")],
    ) -> str:
        """One workout template with every exercise and its target sets."""
        with lyfta_errors():
            key = current_api_key()
            data = await client.template(key, template_id)
            if not data:
                raise ToolError(f"Template {template_id} not found.")
            unit = await weight_unit(key)
        return dump(prune({"weight_unit": unit, **compact_template(data)}))

    @mcp.tool(annotations=READ_ONLY)
    async def get_schedule(
        start_date: Annotated[
            date | None, Field(description="Inclusive UTC start date. Defaults to today.")
        ] = None,
        end_date: Annotated[
            date | None, Field(description="Inclusive UTC end date. Defaults to today + 90 days.")
        ] = None,
        include_completed: bool = False,
    ) -> str:
        """Planned workouts from the user's calendar and followed plans (read-only: this
        server cannot schedule workouts)."""
        with lyfta_errors():
            data = await client.schedule(
                current_api_key(),
                from_date=iso(start_date),
                to_date=iso(end_date),
                include_completed=include_completed,
            )
        rows = [
            prune(
                {
                    "date": w.get("scheduled_date"),
                    "time": w.get("scheduled_time"),
                    "title": w.get("title"),
                    "template_id": w.get("template_id"),
                    "source": w.get("source"),
                    "status": w.get("status"),
                    "completed": True if w.get("is_completed") else None,
                    "note": w.get("note"),
                }
            )
            for w in data.get("workouts") or []
        ]
        return dump(prune({"from": data.get("from"), "to": data.get("to"), "workouts": rows}))

    # --- analytics ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def get_training_summary(
        start_date: Annotated[date, Field(description="Inclusive UTC start date (YYYY-MM-DD).")],
        end_date: Annotated[date, Field(description="Inclusive UTC end date (YYYY-MM-DD).")],
    ) -> str:
        """Training overview for a date range: session count, total volume, completed sets per
        body part and per target muscle, sessions and top set (by e1RM) per exercise, and PRs
        recorded in the range. Reads up to 500 workouts."""
        if end_date < start_date:
            raise ToolError("end_date must be on or after start_date.")
        with lyfta_errors():
            key = current_api_key()
            workouts, truncated = await fetch_workouts(key, start_date, end_date)
            meta = {e.id: e for e in await catalog.performed(key)}
            unit = await weight_unit(key)
        summary = training_summary(workouts, meta)
        result: dict[str, Any] = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "weight_unit": unit,
            **summary,
        }
        if truncated:
            result["truncated"] = (
                f"Only the {WORKOUT_FETCH_CAP} most recent workouts in the range were analyzed. "
                "Use a shorter range for complete numbers."
            )
        return dump(result)

    @mcp.tool(annotations=READ_ONLY)
    async def get_lift_trends(
        exercises: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=10,
                description="Exercise names, e.g. ['Barbell Squat', 'Bench Press'].",
            ),
        ],
        days: Annotated[int, Field(ge=7, le=3650)] = 180,
    ) -> str:
        """Strength trend per lift over the last `days`: current e1RM, best e1RM, change over
        the period, last top set, and weeks since the last PR."""
        with lyfta_errors():
            key = current_api_key()
            unit = await weight_unit(key)
            lifts = []
            for name in exercises:
                res = await catalog.resolve(key, name)
                if not isinstance(res, CatalogEntry):
                    lifts.append(resolution_error(res))
                    continue
                data = await client.exercise_progress(key, res.id, days)
                lifts.append(
                    {"exercise": res.name, **lift_trend(progress_points(data), date.today())}
                )
        return dump({"days": days, "weight_unit": unit, "lifts": lifts})

    # --- write tools ----------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    async def create_program(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        description: Annotated[str | None, Field(max_length=2000)] = None,
        goal: Annotated[
            str | None, Field(max_length=200, description="e.g. 'strength', 'hypertrophy'.")
        ] = None,
        confirm: Annotated[
            bool, Field(description="false = preview only. true = create it (after approval).")
        ] = False,
    ) -> str:
        """Create a program (Lyfta collection) to hold workout templates.

        Call with confirm=false first and show the preview to the user; only call again with
        confirm=true once they approve. Programs cannot be edited or deleted through the API
        (the user must do that in the Lyfta app)."""
        title = title.strip()
        if not title:
            raise ToolError("title must not be blank.")
        with lyfta_errors():
            key = current_api_key()
            existing = [
                c
                for c in await client.all_collections(key)
                if str(c.get("title") or "").strip().casefold() == title.casefold()
            ]
            warning = (
                f"A program titled '{title}' already exists (id {existing[0].get('id')})."
                if existing
                else None
            )
            if not confirm:
                return dump(
                    prune(
                        {
                            "status": "preview",
                            "program": {"title": title, "description": description, "goal": goal},
                            "warning": warning,
                            "next_step": "Nothing was created. Show this to the user and call "
                            "create_program again with confirm=true once they approve.",
                        }
                    )
                )
            created = await client.create_collection(key, title, description, goal)
        return dump(
            prune(
                {
                    "status": "created",
                    "program_id": (created or {}).get("id"),
                    "title": (created or {}).get("title", title),
                    "warning": warning,
                }
            )
        )

    @mcp.tool(annotations=WRITE)
    async def create_workout(
        program_id: Annotated[int, Field(description="Program id from list_programs.")],
        title: Annotated[str, Field(min_length=1, max_length=200)],
        exercises: Annotated[list[ExerciseInput], Field(min_length=1, max_length=40)],
        description: Annotated[str | None, Field(max_length=2000)] = None,
        note: Annotated[str | None, Field(max_length=2000)] = None,
        allow_duplicate_title: bool = False,
        confirm: Annotated[
            bool, Field(description="false = preview only. true = create it (after approval).")
        ] = False,
    ) -> str:
        """Create a workout template with target sets inside an existing program.

        Each exercise: {"exercise": "<name or id>", "sets": [...], "rest_seconds", "note",
        "superset_group"}. Each set: {"reps": 5, "weight": 140, "rir": 2} or
        {"rep_range": [8, 10], "weight": 110}; time/distance exercises use duration_seconds /
        distance. Weights are in the user's unit. Only normal sets are supported (no warmup
        set type yet).

        Workflow: call with confirm=false, show the returned preview to the user, and only
        call again with identical arguments and confirm=true after they approve. If any
        exercise name is ambiguous or unknown, nothing is written and candidates are returned;
        retry with the exact name or id.

        There is no edit or delete in the Lyfta API. To change a workout, create a new version
        (e.g. "Squat Day v2") and tell the user to delete the old one in the Lyfta app."""
        title = title.strip()
        if not title:
            raise ToolError("title must not be blank.")
        with lyfta_errors():
            key = current_api_key()
            try:
                program = await client.collection(key, program_id)
            except LyftaError as exc:
                if exc.status in (400, 404) or "not found" in exc.message.lower():
                    raise ToolError(f"Program {program_id} not found. Use list_programs.") from exc
                raise
            if not program:
                raise ToolError(f"Program {program_id} not found. Use list_programs.")

            resolved: list[tuple[ExerciseInput, CatalogEntry]] = []
            problems: list[dict[str, Any]] = []
            for item in exercises:
                res: Resolution = await catalog.resolve(key, item.exercise)
                if isinstance(res, CatalogEntry):
                    resolved.append((item, res))
                else:
                    problems.append(resolution_error(res))
            if problems:
                return dump(
                    {
                        "status": "unresolved_exercises",
                        "message": "Nothing was written. Retry with an exact name or id for each "
                        "exercise below.",
                        "exercises": problems,
                    }
                )

            duplicate = next(
                (
                    w
                    for w in program.get("workouts") or []
                    if str(w.get("title") or "").strip().casefold() == title.casefold()
                ),
                None,
            )
            if duplicate and not allow_duplicate_title:
                return dump(
                    {
                        "status": "duplicate_title",
                        "message": f"Program '{program.get('title')}' already has a workout "
                        f"titled '{title}' (id {duplicate.get('id')}). Nothing was written. "
                        "Use a new title such as "
                        f"'{title} v2', or pass allow_duplicate_title=true.",
                    }
                )

            unit = await weight_unit(key)
            workout = build_workout_payload(title, description, note, resolved)
            warnings = _set_warnings(resolved)
            if not confirm:
                return dump(
                    prune(
                        {
                            "status": "preview",
                            "program": {"id": program_id, "title": program.get("title")},
                            "weight_unit": unit,
                            "preview": "\n".join(preview_lines(workout, unit)),
                            "resolved_exercises": [
                                {"input": str(item.exercise), "matched": entry.name, "id": entry.id}
                                for item, entry in resolved
                            ],
                            "warnings": warnings,
                            "next_step": "Nothing was created. Show the preview to the user and "
                            "call create_workout again with the same arguments and confirm=true "
                            "once they approve.",
                        }
                    )
                )

            created = await client.create_template(key, program_id, workout)
            template_id = (created or {}).get("id")
            fetched = await client.template(key, int(template_id)) if template_id else None
        return dump(
            prune(
                {
                    "status": "created",
                    "program_id": program_id,
                    "template_id": template_id,
                    "weight_unit": unit,
                    "template": compact_template(fetched or created or {}),
                    "warnings": warnings,
                }
            )
        )

    return mcp


def _set_warnings(resolved: list[tuple[ExerciseInput, CatalogEntry]]) -> list[str]:
    warnings = []
    for item, entry in resolved:
        kind = entry.exercise_type or ""
        if "reps" in kind and any(s.reps is None and s.rep_range is None for s in item.sets):
            warnings.append(f"{entry.name}: some sets have no reps or rep_range.")
        if "duration" in kind and any(s.duration_seconds is None for s in item.sets):
            warnings.append(f"{entry.name}: time-based exercise, but some sets have no duration.")
        if "weight" in kind and any(s.weight is None for s in item.sets):
            warnings.append(f"{entry.name}: some sets have no target weight.")
    return warnings
