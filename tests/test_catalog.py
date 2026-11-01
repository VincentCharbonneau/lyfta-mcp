from __future__ import annotations

import respx

from lyfta_mcp.catalog import Ambiguous, CatalogEntry, ExerciseCatalog, NotFound
from lyfta_mcp.client import LyftaClient

from .fixtures import exercise, library_response, performed_response


async def test_exact_performed_match_skips_library(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    library = lyfta.get("/exercises/library").respond(json=library_response([]))
    res = await ExerciseCatalog(client).resolve("  barbell   SQUAT ")
    assert isinstance(res, CatalogEntry)
    assert (res.id, res.name, res.performed) == (1, "Barbell Squat", True)
    assert res.equipment == ["Barbell"]
    assert res.target_muscles == ["Quadriceps", "Gluteus Maximus"]
    assert library.call_count == 0


async def test_performed_wins_over_library(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(
        json=library_response([exercise(999, "Deadlift", type="weight_reps")])
    )
    res = await ExerciseCatalog(client).resolve("Deadlift")
    assert isinstance(res, CatalogEntry)
    assert res.id == 7


async def test_library_exact_match(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    route = lyfta.get("/exercises/library").respond(
        json=library_response(
            [
                exercise(50, "Dumbbell Fly", equipment='["4"]'),
                exercise(51, "Cable Fly", equipment='["3"]'),
            ]
        )
    )
    res = await ExerciseCatalog(client).resolve("cable fly")
    assert isinstance(res, CatalogEntry)
    assert (res.id, res.equipment) == (51, ["Cable"])
    assert route.calls.last.request.url.params["search"] == "cable fly"


async def test_library_null_type_falls_back_to_performed(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    lyfta.get("/exercises").respond(
        json=performed_response([exercise(60, "Hip Thrust (old name)", type="weight_reps")])
    )
    lyfta.get("/exercises/library").respond(
        json=library_response([exercise(60, "Hip Thrust", type=None)])
    )
    res = await ExerciseCatalog(client).resolve("Hip Thrust")
    assert isinstance(res, CatalogEntry)
    assert res.exercise_type == "weight_reps"


async def test_library_null_type_unknown_is_ambiguous(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(
        json=library_response([exercise(2657, "Band Bench Press", type=None)])
    )
    res = await ExerciseCatalog(client).resolve("Band Bench Press")
    assert isinstance(res, Ambiguous)
    assert res.candidates[0].id == 2657
    assert res.note and "exercise_type" in res.note


async def test_ambiguous_returns_up_to_five_candidates(
    client: LyftaClient, lyfta: respx.MockRouter
) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(
        json=library_response([exercise(100 + i, f"Press Variation {i}") for i in range(8)])
    )
    res = await ExerciseCatalog(client).resolve("press")
    assert isinstance(res, Ambiguous)
    assert len(res.candidates) == 5
    # Performed exercises containing the query come before library results.
    assert res.candidates[0].name == "Barbell Bench Press"


async def test_not_found(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(json=library_response([]))
    res = await ExerciseCatalog(client).resolve("Underwater Basket Weaving")
    assert isinstance(res, NotFound)


async def test_resolve_by_id(client: LyftaClient, lyfta: respx.MockRouter) -> None:
    lyfta.get("/exercises").respond(json=performed_response())
    lyfta.get("/exercises/library").respond(json=library_response([exercise(321, "Zercher Squat")]))
    catalog = ExerciseCatalog(client)

    performed = await catalog.resolve(2)
    assert isinstance(performed, CatalogEntry) and performed.name == "Barbell Bench Press"

    assert isinstance(await catalog.resolve("321"), NotFound)
    await catalog.search("zercher")
    from_search = await catalog.resolve("321")
    assert isinstance(from_search, CatalogEntry) and from_search.name == "Zercher Squat"
