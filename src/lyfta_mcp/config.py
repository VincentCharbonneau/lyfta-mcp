"""Settings read from the environment (and an optional local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://my.lyfta.app"
# Repository root when running from a clone (src/lyfta_mcp/config.py -> repo).
REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines, comments and `export ` prefixes are allowed."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_dotenv(paths: list[Path] | None = None) -> Path | None:
    """Load the first existing .env into os.environ without overriding variables already set.

    Looked up in order: $LYFTA_ENV_FILE, ./.env, then the repository root's .env (so a clone
    works however the MCP client launches it). Empty values are ignored.
    """
    if paths is None:
        paths = [Path.cwd() / ".env", REPO_ROOT / ".env"]
        if os.environ.get("LYFTA_ENV_FILE"):
            paths.insert(0, Path(os.environ["LYFTA_ENV_FILE"]).expanduser())
    for path in paths:
        if path.is_file():
            for key, value in parse_dotenv(path.read_text(encoding="utf-8-sig")).items():
                if value and not os.environ.get(key):
                    os.environ[key] = value
            return path
    return None


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    log_level: str = "INFO"
    # SHA-256 hex digests of the Lyfta keys an HTTP deployment accepts. Empty = accept any key.
    allowlist_sha256: frozenset[str] = field(default_factory=frozenset)


def load_settings() -> Settings:
    raw_allowlist = os.environ.get("SERVER_ALLOWLIST_KEYS_SHA256", "")
    allowlist = frozenset(
        item.strip().lower() for item in raw_allowlist.replace(";", ",").split(",") if item.strip()
    )
    return Settings(
        base_url=os.environ.get("LYFTA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        allowlist_sha256=allowlist,
    )
