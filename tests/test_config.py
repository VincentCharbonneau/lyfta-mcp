from __future__ import annotations

import os
from pathlib import Path

import pytest

from lyfta_mcp.config import ConfigError, load_dotenv, load_settings, parse_dotenv


def test_parse_dotenv() -> None:
    text = """
# comment
LYFTA_API_KEY=abc123
export LOG_LEVEL = debug
QUOTED="has # hash"
SINGLE='x'
TRAILING=value # note
EMPTY=
not a pair
"""
    assert parse_dotenv(text) == {
        "LYFTA_API_KEY": "abc123",
        "LOG_LEVEL": "debug",
        "QUOTED": "has # hash",
        "SINGLE": "x",
        "TRAILING": "value",
        "EMPTY": "",
    }


def test_load_dotenv_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("LYFTA_API_KEY=from-file\nLOG_LEVEL=DEBUG\nLYFTA_BASE_URL=\n", encoding="utf-8")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.delenv("LYFTA_API_KEY", raising=False)
    monkeypatch.delenv("LYFTA_BASE_URL", raising=False)

    assert load_dotenv([tmp_path / "missing.env", env]) == env
    assert os.environ["LYFTA_API_KEY"] == "from-file"
    assert os.environ["LOG_LEVEL"] == "WARNING"
    assert "LYFTA_BASE_URL" not in os.environ


def test_load_dotenv_missing(tmp_path: Path) -> None:
    assert load_dotenv([tmp_path / ".env"]) is None


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("LYFTA_API_KEY", "MCP_AUTH_TOKEN", "LYFTA_BASE_URL", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_load_settings(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LYFTA_API_KEY", " lyfta-key ")
    clean_env.setenv("MCP_AUTH_TOKEN", "t" * 32)
    clean_env.setenv("LYFTA_BASE_URL", "https://example.test/")
    settings = load_settings()
    assert settings.api_key == "lyfta-key"
    assert settings.auth_token == "t" * 32
    assert settings.base_url == "https://example.test"


def test_load_settings_requires_both_secrets(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert "LYFTA_API_KEY" in str(err.value)
    assert "MCP_AUTH_TOKEN" in str(err.value)


def test_load_settings_rejects_short_token(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LYFTA_API_KEY", "k")
    clean_env.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(ConfigError, match="at least"):
        load_settings()
