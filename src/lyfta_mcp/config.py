"""Settings read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://my.lyfta.app"


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
