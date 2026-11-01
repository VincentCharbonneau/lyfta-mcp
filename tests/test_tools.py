from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .conftest import call
from .fixtures import (
    BENCH,
    SQUAT,
    collection_detail,
    collections_response,
    exercise,
    library_response,
    performed_response,
    performed_set,
    progress_response,
    workout,
    workout_exercise,
    workouts_response,
)


@pytest.fixture
def base_routes(lyfta: respx.MockRouter) -> respx.MockRouter:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/progress").respond(json=progress_response([], unit="lb"))
    lyfta.get("/exercises/library").respond(json=library_response([]))
    return lyfta


async def test_get_workouts_is_compact(
    server: MCPServer, base_routes: respx.MockRouter, api_key: str
) -> None:
    base_routes.get("/workouts").respond(
        json=workouts_response(
            [workout(1, "2026-09-01", [workout_exercise(SQUAT, [performed_set(100, 5)])])]
        )
    )
    out = await call(server, "get_workouts", {"start_date": "2026-09-01"})
    assert out["weight_unit"] == "lb"
    w = out["workouts"][0]
    assert "user" not in w
    ex = w["exercises"][0]
    assert ex["name"] == "Barbell Squat"
    assert "exercise_image" not in ex
    assert ex["sets"] == [{"weight": "100", "reps": "5"}]
    assert base_routes.routes[-1].calls.last.request.url.params["from"] == "2026-09-01"


async def test_training_summary_paginates(
    server: MCPServer, base_routes: respx.MockRouter, api_key: str
) -> None:
    def page(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params["page"])
        w = workout(n, f"2026-09-0{n}", [workout_exercise(BENCH, [performed_set(80, 8)])])
        return httpx.Response(200, json=workouts_response([w], page=n, total_pages=3))

    route = base_routes.get("/workouts").mock(side_effect=page)
    out = await call(
        server, "get_training_summary", {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    )
    assert route.call_count == 3
    assert out["sessions"] == 3
    assert out["weight_unit"] == "lb"
    assert "truncated" not in out
    assert route.calls.last.request.url.params["to"] == "2026-09-30"


async def test_training_summary_truncates_at_cap(
    server: MCPServer, base_routes: respx.MockRouter, api_key: str
) -> None:
    hundred = [workout(i, "2026-09-01", []) for i in range(100)]
    route = base_routes.get("/workouts").respond(json=workouts_response(hundred, total_pages=10))
    out = await call(
        server, "get_training_summary", {"start_date": "2026-01-01", "end_date": "2026-09-30"}
    )
    assert route.call_count == 5
    assert out["sessions"] == 500
    assert "500" in out["truncated"]


async def test_lift_trends(server: MCPServer, lyfta: respx.MockRouter, api_key: str) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(json=library_response([]))
    lyfta.get("/exercises/progress").respond(
        json=progress_response(
            [
                {"date": "2026-08-01", "best_weight": 100, "best_reps": 5, "estimated_rm": "117"},
                {"date": "2026-09-01", "best_weight": 105, "best_reps": 5, "estimated_rm": "122"},
            ]
        )
    )
    out = await call(server, "get_lift_trends", {"exercises": ["barbell squat", "Nope"]})
    assert out["weight_unit"] == "kg"
    squat, nope = out["lifts"]
    assert squat["exercise"] == "Barbell Squat"
    assert squat["current_e1rm"] == 122
    assert squat["change_over_period"] == 5
    assert nope == {"exercise": "Nope", "status": "not_found"}


async def test_lyfta_errors_become_tool_errors(
    server: MCPServer, lyfta: respx.MockRouter, api_key: str
) -> None:
    lyfta.get("/collections").respond(401, json={"status": False, "message": "Invalid API key"})
    with pytest.raises(ToolError, match="Invalid API key"):
        await server.call_tool("list_programs", {})


async def test_create_program_preview_then_confirm(
    server: MCPServer, lyfta: respx.MockRouter, api_key: str
) -> None:
    lyfta.get("/collections").respond(
        json=collections_response([{"id": 3, "title": "Strength Block", "workout_count": 2}])
    )
    post = lyfta.post("/collections").respond(
        json={"status": True, "data": {"id": 77, "title": "strength block"}}
    )

    preview = await call(server, "create_program", {"title": "strength block"})
    assert preview["status"] == "preview"
    assert "already exists" in preview["warning"]
    assert post.call_count == 0

    created = await call(server, "create_program", {"title": "strength block", "confirm": True})
    assert created["status"] == "created"
    assert created["program_id"] == 77
    assert post.call_count == 1


SQUAT_DAY = {
    "program_id": 10,
    "title": "Squat Day",
    "exercises": [
        {
            "exercise": "Barbell Squat",
            "rest_seconds": 180,
            "note": "belt on top sets",
            "sets": [{"reps": 5, "weight": 140, "rir": 2}, {"rep_range": [8, 10], "weight": 110}],
        },
        {"exercise": "Cable Fly", "superset_group": 1, "sets": [{"reps": 12, "weight": 20}]},
    ],
}


@pytest.fixture
def workout_routes(base_routes: respx.MockRouter) -> respx.MockRouter:
    base_routes.get("/exercises/library").respond(
        json=library_response([exercise(51, "Cable Fly", equipment='["3"]')])
    )
    base_routes.get("/collections/10").respond(
        json=collection_detail(10, "Lower", [{"id": 500, "title": "Deadlift Day"}])
    )
    return base_routes


async def test_create_workout_preview_writes_nothing(
    server: MCPServer, workout_routes: respx.MockRouter, api_key: str
) -> None:
    post = workout_routes.post("/templates").respond(json={"status": True, "data": {"id": 1}})
    out = await call(server, "create_workout", SQUAT_DAY)
    assert out["status"] == "preview"
    assert out["weight_unit"] == "lb"
    assert out["preview"].splitlines() == [
        "Workout: Squat Day",
        "1. Barbell Squat (rest 3:00)",
        "   note: belt on top sets",
        "   set 1: 5 reps @ 140 lb, RIR 2",
        "   set 2: 8-10 reps @ 110 lb",
        "2. Cable Fly (superset 1)",
        "   set 1: 12 reps @ 20 lb",
    ]
    assert out["resolved_exercises"][1] == {"input": "Cable Fly", "matched": "Cable Fly", "id": 51}
    assert post.call_count == 0


async def test_create_workout_confirm_posts_and_refetches(
    server: MCPServer, workout_routes: respx.MockRouter, api_key: str
) -> None:
    post = workout_routes.post("/templates").respond(
        json={"status": True, "data": {"id": 9812, "title": "Squat Day", "exercises": []}}
    )
    fetched = workout_routes.get("/templates/9812").respond(
        json={
            "status": True,
            "data": {
                "id": 9812,
                "title": "Squat Day",
                "exercises": [
                    {
                        "exercise_id": 1,
                        "excercise_name": "Barbell Squat",
                        "exercise_image": "x",
                        "sets": [{"set_type_id": 0, "reps": "5", "weight": "140"}],
                    }
                ],
            },
        }
    )
    out = await call(server, "create_workout", {**SQUAT_DAY, "confirm": True})
    assert out["status"] == "created"
    assert out["template_id"] == 9812
    assert out["template"]["exercises"][0]["sets"] == [{"weight": "140", "reps": "5"}]
    assert fetched.call_count == 1

    body = json.loads(post.calls.last.request.content)
    assert body["collectionId"] == 10
    squat, fly = body["workout"]["exercises"]
    assert squat["exercise_id"] == 1
    assert squat["excercise_name"] == "Barbell Squat"
    assert squat["exercise_type"] == "weight_reps"
    assert squat["exercise_image"] == SQUAT["image_name"]
    assert squat["is_rep_range_active"] is True
    assert squat["sets"][1]["from_reps"] == "8"
    assert fly["exercise_id"] == 51
    assert fly["exercise_superset_id"] == 1
    assert all(
        isinstance(v, str) for s in squat["sets"] for k, v in s.items() if k != "set_type_id"
    )


async def test_create_workout_unresolved_writes_nothing(
    server: MCPServer, base_routes: respx.MockRouter, api_key: str
) -> None:
    base_routes.get("/collections/10").respond(json=collection_detail(10, "Lower", []))
    base_routes.get("/exercises/library").respond(
        json=library_response([exercise(80, "Leg Press"), exercise(81, "Single Leg Press")])
    )
    post = base_routes.post("/templates").respond(json={"status": True, "data": {"id": 1}})
    args = {
        "program_id": 10,
        "title": "Legs",
        "exercises": [
            {"exercise": "Barbell Squat", "sets": [{"reps": 5}]},
            {"exercise": "leg", "sets": [{"reps": 10}]},
        ],
        "confirm": True,
    }
    out = await call(server, "create_workout", args)
    assert out["status"] == "unresolved_exercises"
    assert out["exercises"][0]["status"] == "ambiguous"
    assert {c["id"] for c in out["exercises"][0]["candidates"]} >= {80, 81}
    assert post.call_count == 0


async def test_create_workout_duplicate_title_guard(
    server: MCPServer, workout_routes: respx.MockRouter, api_key: str
) -> None:
    post = workout_routes.post("/templates").respond(json={"status": True, "data": {"id": 2}})
    workout_routes.get("/templates/2").respond(json={"status": True, "data": {"id": 2}})
    args = {**SQUAT_DAY, "title": "deadlift day", "confirm": True}

    refused = await call(server, "create_workout", args)
    assert refused["status"] == "duplicate_title"
    assert post.call_count == 0

    allowed = await call(server, "create_workout", {**args, "allow_duplicate_title": True})
    assert allowed["status"] == "created"
    assert post.call_count == 1


async def test_create_workout_unknown_program(
    server: MCPServer, base_routes: respx.MockRouter, api_key: str
) -> None:
    base_routes.get("/collections/404").respond(
        200, json={"status": False, "message": "Collection not found"}
    )
    with pytest.raises(ToolError, match="Program 404 not found"):
        await server.call_tool("create_workout", {**SQUAT_DAY, "program_id": 404})
