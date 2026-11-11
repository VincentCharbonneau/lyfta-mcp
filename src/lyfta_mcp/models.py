"""Tool input models and their mapping to Lyfta template payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import CatalogEntry


class SetInput(BaseModel):
    """One target set. Use `reps` or `rep_range`, not both."""

    model_config = ConfigDict(extra="forbid")

    reps: int | None = Field(default=None, ge=0, le=1000, description="Target reps.")
    rep_range: tuple[int, int] | None = Field(
        default=None, description="Target rep range [low, high], e.g. [8, 10]."
    )
    weight: float | None = Field(
        default=None, ge=0, description="Target weight in the user's unit (kg or lb)."
    )
    rir: float | None = Field(default=None, ge=0, le=10, description="Target reps in reserve.")
    duration_seconds: int | None = Field(
        default=None, ge=0, description="For time-based exercises (planks, cardio)."
    )
    distance: float | None = Field(default=None, ge=0, description="For distance-based exercises.")

    @model_validator(mode="after")
    def _check(self) -> SetInput:
        if self.reps is not None and self.rep_range is not None:
            raise ValueError("use either reps or rep_range, not both")
        if self.rep_range is not None:
            low, high = self.rep_range
            if low < 0 or high < low:
                raise ValueError("rep_range must be [low, high] with 0 <= low <= high")
        return self


class ExerciseInput(BaseModel):
    """One exercise in a workout template."""

    model_config = ConfigDict(extra="forbid")

    exercise: str | int = Field(
        description="Exercise name as the user calls it (e.g. 'Barbell Squat'), or a catalog id "
        "returned by list_my_exercises / search_exercise_library."
    )
    sets: list[SetInput] = Field(min_length=1, max_length=50)
    rest_seconds: int = Field(
        default=0,
        ge=0,
        le=3600,
        description="Rest between sets, in seconds (rounded to the nearest 5 s, the app's step).",
    )
    note: str = Field(default="", max_length=500, description="Per-exercise note.")
    superset_group: int = Field(
        default=0,
        ge=0,
        le=100,
        description="0 = not in a superset. Exercises sharing the same positive number form a "
        "superset.",
    )


def num(value: float | int) -> str:
    """Format a number the way the Lyfta app stores it: '140', '62.5'."""
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    return f"{as_float:g}"


# Lyfta stores `exercise_rest_time` in 5-second steps, not seconds: 24 means 2:00.
REST_UNIT_SECONDS = 5


def rest_to_lyfta(seconds: int) -> int:
    """Seconds -> Lyfta rest units, rounded to the nearest step (halves round up)."""
    return (seconds + REST_UNIT_SECONDS // 2) // REST_UNIT_SECONDS


def rest_from_lyfta(value: Any) -> int:
    """Lyfta rest units (int or numeric string) -> seconds. Invalid or empty -> 0."""
    try:
        return int(float(value)) * REST_UNIT_SECONDS
    except (TypeError, ValueError):
        return 0


def build_exercise_payload(item: ExerciseInput, entry: CatalogEntry) -> dict[str, Any]:
    range_active = any(s.rep_range is not None for s in item.sets)
    sets: list[dict[str, Any]] = []
    for s in item.sets:
        out: dict[str, Any] = {"set_type_id": 0}
        if s.rep_range is not None:
            out["reps"] = ""
            out["from_reps"] = num(s.rep_range[0])
            out["to_reps"] = num(s.rep_range[1])
        elif s.reps is not None:
            out["reps"] = num(s.reps)
            if range_active:
                out["from_reps"] = out["to_reps"] = out["reps"]
        else:
            out["reps"] = ""
        out["weight"] = num(s.weight) if s.weight is not None else ""
        out["rir"] = num(s.rir) if s.rir is not None else ""
        out["duration"] = num(s.duration_seconds) if s.duration_seconds is not None else ""
        out["distance"] = num(s.distance) if s.distance is not None else ""
        sets.append(out)
    return {
        "exercise_id": entry.id,
        "excercise_name": entry.name,  # legacy misspelling required by the API
        "exercise_type": entry.exercise_type or "",
        "exercise_image": entry.image_name,
        "exercise_note": item.note,
        "exercise_rest_time": rest_to_lyfta(item.rest_seconds),
        "exercise_superset_id": item.superset_group,
        "is_rep_range_active": range_active,
        "sets": sets,
    }


def build_workout_payload(
    title: str,
    description: str | None,
    note: str | None,
    resolved: list[tuple[ExerciseInput, CatalogEntry]],
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description or "",
        "note": note or "",
        "exercises": [build_exercise_payload(item, entry) for item, entry in resolved],
    }


def _fmt_rest(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def describe_set(s: dict[str, Any], unit: str) -> str:
    parts: list[str] = []
    if s.get("from_reps") and s.get("to_reps") and s["from_reps"] != s["to_reps"]:
        parts.append(f"{s['from_reps']}-{s['to_reps']} reps")
    elif s.get("reps"):
        parts.append(f"{s['reps']} reps")
    if s.get("duration"):
        parts.append(f"{s['duration']} s")
    if s.get("distance"):
        parts.append(f"distance {s['distance']}")
    text = " ".join(parts) or "(no target)"
    if s.get("weight"):
        text += f" @ {s['weight']} {unit}"
    if s.get("rir"):
        text += f", RIR {s['rir']}"
    return text


def preview_lines(workout: dict[str, Any], unit: str) -> list[str]:
    """Human-readable lines describing a template payload."""
    lines = [f"Workout: {workout['title']}"]
    if workout.get("description"):
        lines.append(f"Description: {workout['description']}")
    if workout.get("note"):
        lines.append(f"Note: {workout['note']}")
    for i, ex in enumerate(workout["exercises"], start=1):
        header = f"{i}. {ex['excercise_name']}"
        extras = []
        if ex.get("exercise_superset_id"):
            extras.append(f"superset {ex['exercise_superset_id']}")
        rest = rest_from_lyfta(ex.get("exercise_rest_time"))
        if rest:
            extras.append(f"rest {_fmt_rest(rest)}")
        if extras:
            header += f" ({', '.join(extras)})"
        lines.append(header)
        if ex.get("exercise_note"):
            lines.append(f"   note: {ex['exercise_note']}")
        for n, s in enumerate(ex["sets"], start=1):
            lines.append(f"   set {n}: {describe_set(s, unit)}")
    return lines
