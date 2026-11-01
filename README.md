# lyfta-mcp

An [MCP](https://modelcontextprotocol.io) server for the [Lyfta](https://lyfta.app) workout tracker. Claude (or any MCP client) can read your training history, analyze your progress, and create programs and workouts with target sets, reps, weights and RIR directly in your Lyfta account.

> **Unofficial.** This project is not affiliated with, endorsed by, or supported by Lyfta or Lindberg Development AS. It uses the public [Lyfta API](https://my.lyfta.app/community/api) with your own API key.

Lyfta also offers its own read-only MCP connector. This one adds program and workout creation, training analytics, and self-hosting.

## Capabilities

**Read**
- Completed workouts with every exercise and set, or lightweight summaries
- Your performed exercises and the full exercise library, with decoded equipment and muscles
- Per-exercise progress (best set and estimated 1RM per day)
- Programs (Lyfta collections), workout templates, and your upcoming schedule

**Analyze**
- `get_training_summary`: sessions, volume, sets per body part and target muscle, top set per exercise, PRs in a date range
- `get_lift_trends`: current and best e1RM, change over the period, last top set, weeks since last PR

**Create**
- `create_program`: a new program
- `create_workout`: a workout template inside a program, with target sets (reps or rep ranges, weight, RIR), rest times, notes and supersets. Exercises are given by name and resolved to the exact catalog entry.

Both write tools return a preview first and only write once called again with `confirm=true`, so Claude shows you the plan before anything lands in your account.

### Limitations

These come from the Lyfta API itself:

- **No editing or deleting** programs or workouts. To change a workout, create a new version (e.g. "Squat Day v2") and delete the old one in the Lyfta app.
- **No scheduling.** The schedule is read-only.
- **No logging of completed workouts.** Weights you give are *targets* in templates, not performed sets.
- **Normal sets only.** The API documents set type `0` (normal); warmup and other set types are not supported yet.
- Exercises you have never performed and whose library entry has no `exercise_type` cannot be added to a template (the API requires an exact catalog match). Log the exercise once in the app, or choose one you have done.
- Rate limits: 60 requests/minute and 5,000/day per key. The server throttles itself to stay under the per-minute limit.

## Get a Lyfta API key

1. Go to [my.lyfta.app/community/api](https://my.lyfta.app/community/api).
2. Click **Generate API Key** and copy it right away. It is shown only once.
3. Generating a new key revokes the previous one.

## Local use with Claude Code

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/VincentCharbonneau/lyfta-mcp && cd lyfta-mcp && uv sync
```

Put your key in a `.env` file at the root of the clone. Git ignores `.env`, so the key is never committed or pushed (`.env.example` is the committed template; leave it empty):

```bash
cp .env.example .env   # then set LYFTA_API_KEY=<key> in .env
```

```bash
claude mcp add --transport stdio --scope user lyfta -- uv run --directory /abs/path/lyfta-mcp lyfta-mcp
```

The server reads `.env` from the clone's root on startup (or from the path in `LYFTA_ENV_FILE`). A `LYFTA_API_KEY` set in the environment, e.g. with `claude mcp add --env LYFTA_API_KEY=<key> ...`, takes precedence.

Or, without cloning:

```bash
claude mcp add --transport stdio --scope user --env LYFTA_API_KEY=<key> lyfta -- uvx --from git+https://github.com/VincentCharbonneau/lyfta-mcp lyfta-mcp
```

Run `/mcp` in Claude Code to check that `lyfta` is connected.

## Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "lyfta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/VincentCharbonneau/lyfta-mcp", "lyfta-mcp"],
      "env": { "LYFTA_API_KEY": "<key>" }
    }
  }
}
```

If you cloned the repo, use `"command": "uv"` and `"args": ["run", "--directory", "/abs/path/lyfta-mcp", "lyfta-mcp"]` instead, and you can drop `env` if your key is in the clone's `.env`.

## Connecting to a hosted server

A hosted instance serves Streamable HTTP at `/mcp`. Each user sends their own Lyfta key:

```bash
claude mcp add --transport http lyfta https://<host>/mcp --header "Authorization: Bearer <your Lyfta key>"
```

On claude.ai, custom connectors can use this where the request headers option is available. OAuth sign-in for claude.ai is planned.

## Self-hosting with Docker

```bash
docker compose up -d
curl http://localhost:8000/health   # {"status":"ok"}
```

No Lyfta key is configured on the server. Every request carries the caller's key in `Authorization: Bearer <key>`, so one deployment can serve several people.

| Variable | Purpose |
|---|---|
| `SERVER_ALLOWLIST_KEYS_SHA256` | Optional. Comma-separated SHA-256 hex digests of the keys the server accepts (restrict it to yourself). Compute one with `python -c "import hashlib; print(hashlib.sha256(b'YOUR_KEY').hexdigest())"`. |
| `LOG_LEVEL` | `INFO` by default. |
| `PORT` | Port when not using the Docker default of 8000. |

Images are published to `ghcr.io/vincentcharbonneau/lyfta-mcp` for each release tag.

**Always put the server behind HTTPS**, since requests carry API keys. With [Caddy](https://caddyserver.com):

```
lyfta.example.com {
    reverse_proxy localhost:8000
}
```

Any platform that terminates TLS works too (Fly.io, Railway, Render, a VPS with nginx).

Without Docker: `uv run lyfta-mcp --transport http --host 0.0.0.0 --port 8000`.

## Example prompts

- "Analyze my last 8 weeks of squat, bench and deadlift. Am I still progressing?"
- "Which muscle groups am I under-training this month?"
- "Build a 4-day upper/lower program from my current e1RMs. Show it to me before creating it."
- "Add a 'Squat Day v2' to my Strength program: 5x5 at 80% of my squat e1RM, then 3x8-10 leg press."

## Privacy

- The server is stateless: no database, nothing written to disk.
- API keys are never stored or logged. In HTTP mode a key lives only in memory for the duration of the request that carried it.
- Responses are cached in memory for a short time (60 s, or 10 min for the exercise catalog), separately for each key.
- All data goes directly between the server and `my.lyfta.app`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
```

`scripts/smoke_live.py` runs read-only checks against the real API (key from `.env` or `LYFTA_API_KEY`). `--write` also creates a program titled `lyfta-mcp smoke test <timestamp>`, which you then need to delete in the app.

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE): free for personal and noncommercial use; commercial use prohibited. This is not an open-source license as defined by the OSI.
