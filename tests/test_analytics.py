from __future__ import annotations

from datetime import date

import pytest

from lyfta_mcp.analytics import e1rm, lift_trend, progress_points, training_summary
from lyfta_mcp.catalog import CatalogEntry

from .fixtures import BENCH, SQUAT, performed_set, workout, workout_exercise

META = {
    1: CatalogEntry.from_api(SQUAT, performed=True),
    2: CatalogEntry.from_api(BENCH, performed=True),
}


def test_e1rm_epley() -> None:
    assert e1rm(100, 1) == 100
    assert e1rm(100, 5) == pytest.approx(116.667, rel=1e-3)
    assert e1rm(100, 10) == pytest.approx(133.333, rel=1e-3)


def test_training_summary() -> None:
    workouts = [
        workout(
            1,
            "2026-09-01",
            [
                workout_exercise(
                    SQUAT,
                    [
                        performed_set(100, 5),
                        performed_set(120, 3, record="weight"),
                        performed_set(130, ""),  # planned but never done
                    ],
                ),
                workout_exercise(BENCH, [performed_set(80, 8)]),
            ],
            total_volume=1500,
        ),
        workout(
            2,
            "2026-09-04",
            [workout_exercise(SQUAT, [performed_set(110, 5)])],
            total_volume=None,
        ),
    ]
    s = training_summary(workouts, META)
    assert s["sessions"] == 2
    assert s["training_days"] == 2
    # Workout 1 uses its reported volume, workout 2 is computed (110 * 5).
    assert s["total_volume"] == 1500 + 550
    # Squat: 2 + 1 performed sets; the 130 kg set with no reps logged is ignored, while
    # is_completed=False (what the real API returns for finished workouts) is not.
    assert s["sets_per_body_part"] == {"Quadriceps": 3, "Thighs": 3, "Chest": 1}
    assert s["sets_per_target_muscle"]["Quadriceps"] == 3
    assert s["sets_per_target_muscle"]["Pectoralis Major Sternal Head"] == 1

    squat = next(e for e in s["exercises"] if e["exercise"] == "Barbell Squat")
    assert squat["sessions"] == 2
    assert squat["sets"] == 3
    # 120x3 (e1RM 132) beats 110x5 (e1RM 128.3).
    assert squat["top_set"] == {"weight": 120, "reps": 3, "e1rm": 132, "date": "2026-09-01"}
    assert s["prs"] == [
        {
            "date": "2026-09-01",
            "exercise": "Barbell Squat",
            "record_type": "weight",
            "record_level": "1",
            "record_value": "120",
            "set": "120 x 3",
        }
    ]


def test_progress_points_sorts_and_parses() -> None:
    points = progress_points(
        {
            "data": [
                {"date": "2025-07-21", "best_weight": 110, "best_reps": 5, "estimated_rm": "128"},
                {"date": "2025-07-14", "best_weight": "100", "best_reps": "5"},
            ]
        }
    )
    assert [p["date"] for p in points] == ["2025-07-14", "2025-07-21"]
    assert points[0]["e1rm"] == pytest.approx(116.7)
    assert points[1]["e1rm"] == 128


def test_lift_trend() -> None:
    points = [
        {"date": "2026-06-01", "best_weight": 100, "best_reps": 5, "e1rm": 116.7},
        {"date": "2026-07-01", "best_weight": 110, "best_reps": 5, "e1rm": 128.3},
        {"date": "2026-08-01", "best_weight": 105, "best_reps": 5, "e1rm": 122.5},
    ]
    t = lift_trend(points, today=date(2026, 8, 29))
    assert t["current_e1rm"] == 122.5
    assert t["best_e1rm"] == 128.3
    assert t["best_e1rm_date"] == "2026-07-01"
    assert t["change_over_period"] == 5.8
    assert t["change_pct"] == 5.0
    assert t["last_top_set"] == {"date": "2026-08-01", "weight": 105, "reps": 5}
    assert t["last_pr_date"] == "2026-07-01"
    assert t["weeks_since_last_pr"] == 8.4


def test_lift_trend_without_weights() -> None:
    assert "note" in lift_trend([{"date": "2026-01-01", "best_reps": 20}], today=date.today())
