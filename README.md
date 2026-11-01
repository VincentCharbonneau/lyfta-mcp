# lyfta-mcp

A self-hosted [MCP](https://modelcontextprotocol.io) server for the [Lyfta](https://lyfta.app) workout tracker. Run it on your own machine or server, connect Claude (or any MCP client), and it can read your training history, analyze your progress, and create programs and workouts with target sets, reps, weights and RIR directly in your Lyfta account.

> **Unofficial.** This project is not affiliated with, endorsed by, or supported by Lyfta or Lindberg Development AS. It uses the public [Lyfta API](https://my.lyfta.app/community/api) with your own API key.

Lyfta also offers its own read-only MCP connector. This one adds program and workout creation and training analytics, and runs on infrastructure you control.

## How it works

```
Claude ──HTTPS + access token──▶ your reverse proxy ──▶ lyfta-mcp ──Lyfta API key──▶ my.lyfta.app
```

- The server holds **one Lyfta API key** (yours) in its `.env`. It never leaves the server.
- Clients authenticate with a separate **access token** (`MCP_AUTH_TOKEN`). Anyone without it gets `401`.
- If the token leaks, change it in `.env` and restart; your Lyfta key stays the same.

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
- Rate limits: 60 requests/minute and 5,000/day. The server throttles itself to stay under the per-minute limit.

## Setup

### 1. Get a Lyfta API key

Go to [my.lyfta.app/community/api](https://my.lyfta.app/community/api), click **Generate API Key**, and copy it right away (it is shown only once; generating a new one revokes the old one).

### 2. Configure `.env`

```bash
git clone https://github.com/VincentCharbonneau/lyfta-mcp && cd lyfta-mcp
uv run lyfta-mcp
```

On the first run there is no `.env` yet, so the server creates one with a freshly generated access token (`MCP_AUTH_TOKEN`) and exits, asking for your Lyfta key. Open `.env` and fill it in:

```
LYFTA_API_KEY=<your Lyfta key>
```

You can also create it by hand: `cp .env.example .env`, then set both values (generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`). An existing `.env` is never overwritten.

`.env` is ignored by git and Docker builds, so neither secret is ever committed or baked into an image. `.env.example` is the committed template; keep it empty. The server refuses to start if either value is missing or the token is shorter than 24 characters.

### 3. Run it

With Docker (Compose reads the `.env` from step 2, so create it first):

```bash
docker compose up -d
curl http://localhost:8000/health   # {"status":"ok"}
```

Or directly with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run lyfta-mcp                          # http://127.0.0.1:8000
uv run lyfta-mcp --host 0.0.0.0 --port 8000
```

The MCP endpoint is `/mcp` (Streamable HTTP). `GET /health` needs no token.

### 4. Put it behind HTTPS

Requests carry your access token, so expose the server only over HTTPS. The compose file binds to `127.0.0.1:8000` so it is not reachable directly from the network. With [Caddy](https://caddyserver.com) on the same host:

```
lyfta.example.com {
    reverse_proxy localhost:8000
}
```

Any setup that terminates TLS works (nginx, Traefik, Cloudflare Tunnel, Tailscale Serve, Fly.io, Railway). For use on your own computer only, plain `http://localhost:8000/mcp` is fine.

## Connect a client

**Claude Code**

```bash
claude mcp add --transport http --scope user lyfta https://lyfta.example.com/mcp --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Run `/mcp` in Claude Code to check that `lyfta` is connected.

**Claude Desktop**, via [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (requires Node.js). Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config) and restart:

```json
{
  "mcpServers": {
    "lyfta": {
      "command": "npx",
      "args": [
        "mcp-remote", "https://lyfta.example.com/mcp",
        "--header", "Authorization:${LYFTA_MCP_AUTH}"
      ],
      "env": { "LYFTA_MCP_AUTH": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

**claude.ai**: custom connectors work where the request headers option is available. OAuth sign-in is planned.

## Example prompts

- "Analyze my last 8 weeks of squat, bench and deadlift. Am I still progressing?"
- "Which muscle groups am I under-training this month?"
- "Build a 4-day upper/lower program from my current e1RMs. Show it to me before creating it."
- "Add a 'Squat Day v2' to my Strength program: 5x5 at 80% of my squat e1RM, then 3x8-10 leg press."

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `LYFTA_API_KEY` | yes | Your Lyfta API key. |
| `MCP_AUTH_TOKEN` | yes | Access token clients send as `Authorization: Bearer <token>` (24+ characters). |
| `LOG_LEVEL` | no | `INFO` by default. |
| `HOST`, `PORT` | no | Bind address when running without Docker (default `127.0.0.1:8000`). |
| `LYFTA_ENV_FILE` | no | Path to an env file other than `./.env` or the repository's `.env`. |

Variables already set in the environment take precedence over `.env`. Docker images are published to `ghcr.io/vincentcharbonneau/lyfta-mcp` for each release tag; run them with `--env-file .env`.

## Privacy

- No database and nothing written to disk. Responses are cached in memory for a short time (60 s, or 10 min for the exercise catalog).
- Neither the Lyfta key nor the access token is logged.
- All data goes directly between your server and `my.lyfta.app`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
```

`scripts/smoke_live.py` runs read-only checks against the real API using the key in `.env` (the server does not need to be running). `--write` also creates a program titled `lyfta-mcp smoke test <timestamp>`, which you then need to delete in the app.

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE): free for personal and noncommercial use; commercial use prohibited. This is not an open-source license as defined by the OSI.
