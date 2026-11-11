from __future__ import annotations

import pytest
from pydantic import ValidationError

from lyfta_mcp.catalog import CatalogEntry
from lyfta_mcp.models import (
    ExerciseInput,
    build_workout_payload,
    num,
    preview_lines,
    rest_from_lyfta,
    rest_to_lyfta,
)

SQUAT = CatalogEntry(1, "Barbell Squat", "weight_reps", "https://img/squat.png")
CURL = CatalogEntry(4, "Dumbbell Curl", "weight_reps", "https://img/curl.png")
TRICEPS = CatalogEntry(5, "Cable Pushdown", "weight_reps", "https://img/push.png")


def test_num_formatting() -> None:
    assert num(140) == "140"
    assert num(140.0) == "140"
    assert num(62.5) == "62.5"
    assert num(2) == "2"


def test_payload_strings_and_catalog_fields() -> None:
    item = ExerciseInput.model_validate(
        {
            "exercise": "Barbell Squat",
            "rest_seconds": 180,
            "note": "belt on top sets",
            "sets": [{"reps": 5, "weight": 140, "rir": 2}, {"reps": 5, "weight": 142.5}],
        }
    )
    payload = build_workout_payload("Squat Day", "Heavy", None, [(item, SQUAT)])
    ex = payload["exercises"][0]
    assert ex["exercise_id"] == 1
    assert ex["excercise_name"] == "Barbell Squat"
    assert ex["exercise_type"] == "weight_reps"
    assert ex["exercise_image"] == "https://img/squat.png"
    assert ex["exercise_rest_time"] == 36  # 5-second units: 36 * 5 s = 3:00
    assert ex["exercise_note"] == "belt on top sets"
    assert ex["exercise_superset_id"] == 0
    assert ex["is_rep_range_active"] is False
    assert ex["sets"][0] == {
        "set_type_id": 0,
        "reps": "5",
        "weight": "140",
        "rir": "2",
        "duration": "",
        "distance": "",
    }
    assert ex["sets"][1]["weight"] == "142.5"
    assert ex["sets"][1]["rir"] == ""
    assert payload["title"] == "Squat Day"
    assert payload["description"] == "Heavy"
    assert payload["note"] == ""


def test_rep_ranges() -> None:
    item = ExerciseInput.model_validate(
        {"exercise": 1, "sets": [{"rep_range": [8, 10], "weight": 110}, {"reps": 12}]}
    )
    ex = build_workout_payload("A", None, None, [(item, SQUAT)])["exercises"][0]
    assert ex["is_rep_range_active"] is True
    assert ex["sets"][0]["from_reps"] == "8"
    assert ex["sets"][0]["to_reps"] == "10"
    assert ex["sets"][0]["reps"] == ""
    assert ex["sets"][1]["from_reps"] == ex["sets"][1]["to_reps"] == ex["sets"][1]["reps"] == "12"


def test_supersets() -> None:
    curl = ExerciseInput.model_validate(
        {"exercise": "c", "superset_group": 1, "sets": [{"reps": 10}]}
    )
    push = ExerciseInput.model_validate(
        {"exercise": "p", "superset_group": 1, "sets": [{"reps": 12}]}
    )
    payload = build_workout_payload("Arms", None, None, [(curl, CURL), (push, TRICEPS)])
    assert [e["exercise_superset_id"] for e in payload["exercises"]] == [1, 1]


@pytest.mark.parametrize(
    "bad_set",
    [
        {"reps": 5, "rep_range": [5, 8]},
        {"rep_range": [10, 8]},
        {"reps": -1},
        {"reps": 5, "bogus": 1},
    ],
)
def test_set_validation(bad_set: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExerciseInput.model_validate({"exercise": "x", "sets": [bad_set]})


def test_exercise_needs_sets() -> None:
    with pytest.raises(ValidationError):
        ExerciseInput.model_validate({"exercise": "x", "sets": []})


def test_preview_lines() -> None:
    item = ExerciseInput.model_validate(
        {
            "exercise": "Barbell Squat",
            "rest_seconds": 180,
            "sets": [{"reps": 5, "weight": 140, "rir": 2}, {"rep_range": [8, 10], "weight": 110}],
        }
    )
    lines = preview_lines(build_workout_payload("Squat Day", None, None, [(item, SQUAT)]), "kg")
    assert lines == [
        "Workout: Squat Day",
        "1. Barbell Squat (rest 3:00)",
        "   set 1: 5 reps @ 140 kg, RIR 2",
        "   set 2: 8-10 reps @ 110 kg",
    ]


@pytest.mark.parametrize(
    ("seconds", "units"), [(0, 0), (90, 18), (120, 24), (180, 36), (92, 18), (93, 19), (3600, 720)]
)
def test_rest_to_lyfta_uses_5_second_units(seconds: int, units: int) -> None:
    assert rest_to_lyfta(seconds) == units


@pytest.mark.parametrize(("value", "seconds"), [(24, 120), ("48", 240), (None, 0), ("", 0), (0, 0)])
def test_rest_from_lyfta(value: object, seconds: int) -> None:
    assert rest_from_lyfta(value) == seconds


def test_compact_exercise_reports_rest_in_seconds() -> None:
    from lyfta_mcp.server import compact_exercise

    assert compact_exercise({"exercise_rest_time": "24", "sets": []})["rest_seconds"] == 120
    assert compact_exercise({"exercise_rest_time": 0, "sets": []})["rest_seconds"] is None
