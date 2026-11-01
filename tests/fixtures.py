"""Response bodies shaped like the examples in the Lyfta API docs."""

from __future__ import annotations

from typing import Any

IMG = "https://apilyfta.com/static/GymvisualPNG/example.png"


def exercise(
    id: int,
    name: str,
    *,
    type: str | None = "weight_reps",
    equipment: str = '["1"]',
    body_parts: str = '["19","1"]',
    targets: str = '["27","13"]',
) -> dict[str, Any]:
    return {
        "id": str(id),
        "name": name,
        "image_name": f"{IMG}?{id}",
        "equipment_id": equipment,
        "body_part_id": body_parts,
        "Target_muscles_id": targets,
        "Synergist_muscles_id": '["3","32"]',
        "exercise_type": type,
    }


SQUAT = exercise(1, "Barbell Squat")
BENCH = exercise(2, "Barbell Bench Press", body_parts='["2"]', targets='["25"]')
DEADLIFT = exercise(7, "Deadlift", body_parts='["4"]', targets='["11"]')
PERFORMED = [SQUAT, BENCH, DEADLIFT]


def performed_response(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = PERFORMED if items is None else items
    return {
        "status": True,
        "count": len(items),
        "current_page": 1,
        "total_pages": 1,
        "limit": 500,
        "exercises": items,
    }


def library_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": True,
        "data": {
            "results": items,
            "pagination": {"limit": 10, "offset": 0, "total": len(items), "hasMore": False},
        },
    }


def progress_response(points: list[dict[str, Any]], unit: str = "kg") -> dict[str, Any]:
    return {"status": True, "weight_unit": unit, "data": points}


def workout(
    id: int,
    day: str,
    exercises: list[dict[str, Any]],
    *,
    total_volume: int | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": f"Workout {id}",
        "body_weight": 80,
        "workout_perform_date": f"{day} 18:00:00",
        "total_volume": total_volume,
        "totalLiftedWeight": total_volume,
        "user": {"username": "tester"},
        "exercises": exercises,
    }


def performed_set(
    weight: float, reps: int | str, *, completed: bool = False, record: str | None = None
) -> dict[str, Any]:
    return {
        "id": "abc",
        "weight": str(weight),
        "reps": str(reps),
        "rir": "",
        "duration": "",
        "distance": "",
        "set_type_id": "0",
        "is_completed": completed,
        "record_type": record or "",
        "record_level": "1" if record else "",
        "record_value": str(weight) if record else "",
    }


def workout_exercise(ex: dict[str, Any], sets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "exercise_id": int(ex["id"]),
        "excercise_name": ex["name"],
        "exercise_type": ex["exercise_type"],
        "exercise_image": ex["image_name"],
        "exercise_rest_time": 120,
        "sets": sets,
    }


def workouts_response(
    workouts: list[dict[str, Any]], *, page: int = 1, total_pages: int = 1
) -> dict[str, Any]:
    return {
        "status": True,
        "count": len(workouts),
        "total_records": len(workouts) * total_pages,
        "total_pages": total_pages,
        "current_page": page,
        "limit": 100,
        "workouts": workouts,
    }


def collections_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": True,
        "count": len(items),
        "total_records": len(items),
        "total_pages": 1,
        "current_page": 1,
        "limit": 100,
        "collections": items,
    }


def collection_detail(id: int, title: str, workouts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": True,
        "data": {
            "id": id,
            "title": title,
            "description": "",
            "image": "",
            "pinned": "0",
            "workout_ids": [w["id"] for w in workouts],
            "workout_count": len(workouts),
            "workouts": workouts,
        },
    }
