"""ID tables from the Lyfta API docs (https://my.lyfta.app/community/api#id-mappings)."""

from __future__ import annotations

import json
from typing import Any

EQUIPMENT: dict[int, str] = {
    1: "Barbell",
    2: "Body weight",
    3: "Cable",
    4: "Dumbbell",
    5: "EZ Barbell",
    6: "Leverage machine",
    7: "Sled machine",
    8: "Smith machine",
    9: "Weighted",
    10: "Assisted",
    11: "Band",
    12: "Battling Rope",
    13: "Bosu ball",
    14: "Hammer",
    15: "Kettlebell",
    16: "Medicine Ball",
    17: "Olympic barbell",
    18: "Power Sled",
    19: "Resistance Band",
    20: "Roll",
    21: "Rollball",
    22: "Rope",
    23: "Stability ball",
    24: "Stick",
    25: "Suspension",
    26: "Trap bar",
    27: "Vibrate Plate",
    28: "Wheel roller",
}

BODY_PARTS: dict[int, str] = {
    1: "Thighs",
    2: "Chest",
    3: "Hips",
    4: "Back",
    5: "Upper Arms",
    6: "Shoulders",
    7: "Forearms",
    8: "Calves",
    9: "Neck",
    10: "Cardio",
    11: "Full body",
    12: "Waist",
    13: "Plyometrics",
    14: "Weightlifting",
    15: "Yoga",
    16: "Stretching",
    17: "Biceps",
    18: "Triceps",
    19: "Quadriceps",
    20: "Hamstrings",
}

MUSCLES: dict[int, str] = {
    2: "Adductor Longus",
    3: "Adductor Magnus",
    4: "Biceps Brachii",
    5: "Brachialis",
    6: "Brachioradialis",
    7: "Deep Hip External Rotators",
    8: "Deltoid Anterior",
    9: "Deltoid Lateral",
    10: "Deltoid Posterior",
    11: "Erector Spinae",
    12: "Gastrocnemius",
    13: "Gluteus Maximus",
    14: "Gluteus Medius",
    15: "Gluteus Minimus",
    16: "Gracilis",
    17: "Hamstrings",
    18: "Iliopsoas",
    19: "Infraspinatus",
    20: "Latissimus Dorsi",
    21: "Levator Scapulae",
    22: "Obliques",
    23: "Pectineous",
    24: "Pectoralis Major Clavicular Head",
    25: "Pectoralis Major Sternal Head",
    26: "Popliteus",
    27: "Quadriceps",
    28: "Rectus Abdominis",
    29: "Sartorius",
    30: "Serratus Ante",
    31: "Serratus Anterior",
    32: "Soleus",
    33: "Splenius",
    34: "Sternocleidomastoid",
    35: "Subscapularis",
    36: "Tensor Fasciae Latae",
    37: "Teres Major",
    38: "Teres Minor",
    39: "Tibialis Anterior",
    40: "Transverse Abdominis",
    41: "Trapezius Lower Fibers",
    42: "Trapezius Middle Fibers",
    43: "Trapezius Upper Fibers",
    44: "Triceps Brachii",
    45: "Wrist Extensors",
    46: "Wrist Flexors",
}


def parse_ids(raw: Any) -> list[int]:
    """Parse an ID list that may be a JSON-encoded string ('["1","19"]'), a list, or empty."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [part for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        raw = [raw]
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(str(item).strip()))
        except ValueError:
            continue
    return ids


def decode(raw: Any, table: dict[int, str]) -> list[str]:
    """Decode an ID list into names; unknown IDs are kept as 'id:<n>'."""
    return [table.get(i, f"id:{i}") for i in parse_ids(raw)]


def decode_exercise_meta(exercise: dict[str, Any]) -> dict[str, list[str]]:
    """Decoded equipment / body part / muscle names for a catalog or performed exercise."""
    return {
        "equipment": decode(exercise.get("equipment_id"), EQUIPMENT),
        "body_parts": decode(exercise.get("body_part_id"), BODY_PARTS),
        "target_muscles": decode(exercise.get("Target_muscles_id"), MUSCLES),
        "synergist_muscles": decode(exercise.get("Synergist_muscles_id"), MUSCLES),
    }
