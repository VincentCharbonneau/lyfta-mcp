from __future__ import annotations

import os
from pathlib import Path

import pytest

from lyfta_mcp.config import load_dotenv, parse_dotenv


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
