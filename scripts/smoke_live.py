"""Opt-in live test against the real Lyfta API.

    uv run python scripts/smoke_live.py            # read checks only
    uv run python scripts/smoke_live.py --write    # also creates test data

The key is read from LYFTA_API_KEY, or from the .env file in the repository root. Tools are
called in-process, so the server does not need to be running and MCP_AUTH_TOKEN is not used.

--write creates one program titled "lyfta-mcp smoke test <timestamp>" containing one template.
The API cannot delete, so remove it in the Lyfta app afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any

from lyfta_mcp.client import LyftaClient
from lyfta_mcp.config import DEFAULT_BASE_URL, Settings, load_dotenv
from lyfta_mcp.server import create_server


async def run_tool(server: Any, name: str, args: dict[str, Any] | None = None) -> Any:
    result = await server.call_tool(name, args or {})
    text = result.content[0].text
    if result.is_error:
        raise RuntimeError(f"{name} failed: {text}")
    return json.loads(text)


def ok(label: str, detail: str = "") -> None:
    print(f"  ok  {label}{f' - {detail}' if detail else ''}")


async def main(write: bool) -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    key = os.environ.get("LYFTA_API_KEY", "").strip()
    if not key:
        print("Set LYFTA_API_KEY in .env (see .env.example) or the environment.", file=sys.stderr)
        return 2
    settings = Settings(
        api_key=key,
        auth_token="",  # unused: no HTTP layer in-process
        base_url=os.environ.get("LYFTA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
    )
    client = LyftaClient(settings)
    server = create_server(settings, client)
    today = date.today()

    try:
        print("Read checks")
        summaries = await run_tool(server, "get_workout_summaries", {"limit": 5})
        ok("get_workout_summaries", f"{summaries.get('total_records')} workouts total")

        workouts = await run_tool(server, "get_workouts", {"limit": 2})
        unit = workouts.get("weight_unit")
        ok("get_workouts", f"{len(workouts.get('workouts', []))} returned, unit={unit}")

        exercises = await run_tool(server, "list_my_exercises")
        ok("list_my_exercises", f"{exercises['count']} performed exercises")
        no_type = [e["name"] for e in exercises["exercises"] if "exercise_type" not in e]
        if no_type:
            print(f"      note: {len(no_type)} performed exercises have no exercise_type")

        library = await run_tool(server, "search_exercise_library", {"query": "bench press"})
        typed = sum(1 for r in library["results"] if r.get("exercise_type"))
        ok("search_exercise_library", f"{len(library['results'])} results, {typed} with type")

        if exercises["exercises"]:
            first = exercises["exercises"][0]["name"]
            progress = await run_tool(server, "get_exercise_progress", {"exercise": first})
            ok("get_exercise_progress", f"{first}: {len(progress['points'])} points")
            trends = await run_tool(server, "get_lift_trends", {"exercises": [first]})
            ok("get_lift_trends", json.dumps(trends["lifts"][0])[:120])

        programs = await run_tool(server, "list_programs")
        ok("list_programs", f"{programs['count']} programs")
        with_workouts = [p for p in programs["programs"] if p.get("workout_count")]
        if with_workouts:
            pid = with_workouts[0]["id"]
            program = await run_tool(server, "get_program", {"program_id": pid, "full": True})
            ok(
                "get_program",
                f"{program.get('title')}: {len(program.get('workouts', []))} workouts",
            )

        templates = await run_tool(server, "list_workout_templates", {"limit": 5})
        ok("list_workout_templates", f"{templates.get('total_records')} templates")
        warmup_seen: set[str] = set()
        if templates["templates"]:
            tid = templates["templates"][0]["id"]
            template = await run_tool(server, "get_workout_template", {"template_id": tid})
            ok("get_workout_template", f"{template.get('title')}")
            for ex in template.get("exercises", []):
                for s in ex.get("sets", []):
                    if "set_type_id" in s:
                        warmup_seen.add(str(s["set_type_id"]))
        if warmup_seen:
            print(f"      note: non-normal set_type_id values seen: {sorted(warmup_seen)}")

        schedule = await run_tool(server, "get_schedule")
        ok("get_schedule", f"{len(schedule.get('workouts', []))} upcoming")

        summary = await run_tool(
            server,
            "get_training_summary",
            {
                "start_date": (today - timedelta(days=56)).isoformat(),
                "end_date": today.isoformat(),
            },
        )
        ok("get_training_summary", f"{summary['sessions']} sessions in the last 8 weeks")

        if exercises["exercises"] and programs["programs"]:
            preview = await run_tool(
                server,
                "create_workout",
                {
                    "program_id": programs["programs"][0]["id"],
                    "title": "lyfta-mcp preview check",
                    "exercises": [
                        {
                            "exercise": exercises["exercises"][0]["name"],
                            "sets": [{"reps": 5, "weight": 20}],
                        }
                    ],
                },
            )
            ok("create_workout (preview only, nothing written)", preview["status"])

        if write:
            print("Write checks")
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            title = f"lyfta-mcp smoke test {stamp}"
            created = await run_tool(server, "create_program", {"title": title, "confirm": True})
            pid = created["program_id"]
            ok("create_program", f"id {pid}")
            ex_name = exercises["exercises"][0]["name"]
            made = await run_tool(
                server,
                "create_workout",
                {
                    "program_id": pid,
                    "title": "Smoke test workout",
                    "exercises": [
                        {
                            "exercise": ex_name,
                            "rest_seconds": 90,
                            "sets": [
                                {"reps": 5, "weight": 20, "rir": 2},
                                {"rep_range": [8, 10], "weight": 15},
                            ],
                        }
                    ],
                    "confirm": True,
                },
            )
            ok("create_workout", f"template id {made['template_id']}")
            print(json.dumps(made["template"], indent=2))
            print(
                f"\nCreated program '{title}' (id {pid}). The API cannot delete it: "
                "remove it in the Lyfta app."
            )
    finally:
        await client.aclose()
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="also create a test program")
    sys.exit(asyncio.run(main(parser.parse_args().write)))
