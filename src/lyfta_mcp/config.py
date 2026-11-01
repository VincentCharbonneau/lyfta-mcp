"""Settings read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://my.lyfta.app"
# Repository root when running from a clone (src/lyfta_mcp/config.py -> repo).
REPO_ROOT = Path(__file__).resolve().parents[2]
MIN_TOKEN_LENGTH = 24


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    auth_token: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    log_level: str = "INFO"


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

    Looked up in order: $LYFTA_ENV_FILE, ./.env, then the repository root's .env. Empty values
    are ignored. Under Docker Compose the variables come from `env_file` instead.
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


ENV_TEMPLATE = """\
# Server secrets. This file is ignored by git and never pushed.

# Your Lyfta API key (https://my.lyfta.app/community/api). It never leaves the server.
LYFTA_API_KEY=

# Access token clients send as "Authorization: Bearer <token>" (generated for you).
MCP_AUTH_TOKEN={token}

LOG_LEVEL=INFO
"""


def default_env_path() -> Path:
    """The repository's .env when running from a clone, otherwise ./.env."""
    if (REPO_ROOT / "pyproject.toml").is_file():
        return REPO_ROOT / ".env"
    return Path.cwd() / ".env"


def create_env_file(path: Path) -> Path:
    """Write a starter .env with a freshly generated access token. Never overwrites."""
    with path.open("x", encoding="utf-8") as f:
        f.write(ENV_TEMPLATE.format(token=secrets.token_urlsafe(32)))
    return path


def load_settings() -> Settings:
    api_key = os.environ.get("LYFTA_API_KEY", "").strip()
    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    problems = []
    if not api_key:
        problems.append(
            "LYFTA_API_KEY is not set. Generate one at https://my.lyfta.app/community/api."
        )
    if not auth_token:
        problems.append(
            "MCP_AUTH_TOKEN is not set. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    elif len(auth_token) < MIN_TOKEN_LENGTH:
        problems.append(f"MCP_AUTH_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
    if problems:
        raise ConfigError("\n".join(problems))
    return Settings(
        api_key=api_key,
        auth_token=auth_token,
        base_url=os.environ.get("LYFTA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
