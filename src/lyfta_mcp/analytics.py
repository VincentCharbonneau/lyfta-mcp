"""Derived training stats computed from raw Lyfta data."""

from __future__ import annotations

import contextlib
from collections import Counter
from datetime import date
from typing import Any

from .catalog import CatalogEntry


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def e1rm(weight: float, reps: float) -> float:
    """Epley estimated one-rep max."""
    if reps <= 1:
        return weight
    return weight * (1 + reps / 30)


def workout_date(workout: dict[str, Any]) -> str:
    return str(workout.get("workout_perform_date") or "")[:10]


def set_performed(s: dict[str, Any]) -> bool:
    """Whether a set in a logged workout was actually done.

    `is_completed` is unreliable (the API reports false for sets in finished workouts unless
    they were ticked in the app), so a set counts when it records reps, time or distance.
    """
    return any((to_float(s.get(k)) or 0) > 0 for k in ("reps", "duration", "distance"))


def _r(value: float, digits: int = 1) -> float | int:
    rounded = round(value, digits)
    return int(rounded) if float(rounded).is_integer() else rounded


def training_summary(
    workouts: list[dict[str, Any]], meta_by_id: dict[int, CatalogEntry]
) -> dict[str, Any]:
    """Sessions, volume, sets per body part / muscle, per-exercise frequency and top sets, PRs."""
    total_volume = 0.0
    sets_by_body_part: Counter[str] = Counter()
    sets_by_muscle: Counter[str] = Counter()
    exercise_sessions: Counter[str] = Counter()
    exercise_sets: Counter[str] = Counter()
    top_sets: dict[str, dict[str, Any]] = {}
    prs: list[dict[str, Any]] = []
    dates: set[str] = set()

    for workout in workouts:
        day = workout_date(workout)
        dates.add(day)
        computed_volume = 0.0
        for ex in workout.get("exercises") or []:
            name = str(ex.get("excercise_name") or ex.get("exercise_name") or "Unknown")
            meta = _meta(meta_by_id, ex.get("exercise_id"))
            completed = [s for s in ex.get("sets") or [] if set_performed(s)]
            if not completed:
                continue
            exercise_sessions[name] += 1
            exercise_sets[name] += len(completed)
            if meta is not None:
                for part in meta.body_parts:
                    sets_by_body_part[part] += len(completed)
                for muscle in meta.target_muscles:
                    sets_by_muscle[muscle] += len(completed)
            for s in completed:
                weight = to_float(s.get("weight")) or 0.0
                reps = to_float(s.get("reps")) or 0.0
                computed_volume += weight * reps
                _update_top_set(top_sets, name, weight, reps, day)
                if s.get("record_type"):
                    prs.append(
                        {
                            "date": day,
                            "exercise": name,
                            "record_type": s.get("record_type"),
                            "record_level": s.get("record_level") or None,
                            "record_value": s.get("record_value") or None,
                            "set": f"{s.get('weight') or 0} x {s.get('reps') or 0}",
                        }
                    )
        reported = to_float(workout.get("total_volume"))
        total_volume += reported if reported is not None else computed_volume

    exercises = [
        {
            "exercise": name,
            "sessions": exercise_sessions[name],
            "sets": exercise_sets[name],
            **({"top_set": top_sets[name]} if name in top_sets else {}),
        }
        for name, _ in exercise_sessions.most_common()
    ]
    return {
        "sessions": len(workouts),
        "training_days": len(dates - {""}),
        "total_volume": _r(total_volume, 0),
        "sets_per_body_part": dict(sets_by_body_part.most_common()),
        "sets_per_target_muscle": dict(sets_by_muscle.most_common()),
        "exercises": exercises,
        "prs": sorted(prs, key=lambda p: str(p["date"])),
    }


def _meta(meta_by_id: dict[int, CatalogEntry], raw_id: Any) -> CatalogEntry | None:
    try:
        return meta_by_id.get(int(raw_id))
    except (TypeError, ValueError):
        return None


def _update_top_set(
    top_sets: dict[str, dict[str, Any]], name: str, weight: float, reps: float, day: str
) -> None:
    if reps <= 0:
        return
    current = top_sets.get(name)
    if weight > 0:
        estimate = e1rm(weight, reps)
        if current is None or estimate > current.get("e1rm", 0):
            top_sets[name] = {
                "weight": _r(weight, 2),
                "reps": _r(reps, 0),
                "e1rm": _r(estimate),
                "date": day,
            }
    elif current is None or ("e1rm" not in current and reps > current["reps"]):
        top_sets[name] = {"reps": _r(reps, 0), "date": day}


def progress_points(progress: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize /exercises/progress data into date-sorted points with a numeric e1RM."""
    points = []
    for row in progress.get("data") or []:
        weight = to_float(row.get("best_weight")) or 0.0
        reps = to_float(row.get("best_reps")) or 0.0
        estimate = to_float(row.get("estimated_rm"))
        if estimate is None and weight > 0 and reps > 0:
            estimate = e1rm(weight, reps)
        point: dict[str, Any] = {
            "date": str(row.get("date") or "")[:10],
            "best_weight": _r(weight, 2),
            "best_reps": _r(reps, 0),
            "best_volume": _r(to_float(row.get("best_volume")) or 0.0, 0),
        }
        if estimate is not None:
            point["e1rm"] = _r(estimate)
        points.append(point)
    return sorted(points, key=lambda p: str(p["date"]))


def lift_trend(points: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Current/best e1RM, change over the period, last top set, weeks since the last PR."""
    with_e1rm = [p for p in points if "e1rm" in p]
    if not with_e1rm:
        return {"sessions": len(points), "note": "No weighted sets in this period."}
    first, last = with_e1rm[0], with_e1rm[-1]
    best = max(with_e1rm, key=lambda p: (p["e1rm"], p["date"]))
    # The last PR is the most recent point that beat every earlier point.
    running_max, last_pr = -1.0, first
    for p in with_e1rm:
        if p["e1rm"] > running_max:
            running_max, last_pr = p["e1rm"], p
    change = last["e1rm"] - first["e1rm"]
    result: dict[str, Any] = {
        "sessions": len(points),
        "current_e1rm": last["e1rm"],
        "best_e1rm": best["e1rm"],
        "best_e1rm_date": best["date"],
        "change_over_period": _r(change),
        "change_pct": _r(change / first["e1rm"] * 100) if first["e1rm"] else None,
        "last_top_set": {
            "date": last["date"],
            "weight": last["best_weight"],
            "reps": last["best_reps"],
        },
        "last_pr_date": last_pr["date"],
    }
    with contextlib.suppress(ValueError):
        result["weeks_since_last_pr"] = _r((today - date.fromisoformat(last_pr["date"])).days / 7)
    return result
