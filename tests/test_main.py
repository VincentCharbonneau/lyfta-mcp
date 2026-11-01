from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lyfta_mcp import config
from lyfta_mcp.__main__ import main
from lyfta_mcp.config import parse_dotenv


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty working directory that is not a clone, with no secrets in the environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "not-a-clone")
    for name in ("LYFTA_API_KEY", "MCP_AUTH_TOKEN", "LYFTA_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_first_run_creates_env_file(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert "created" in capsys.readouterr().err

    values = parse_dotenv((workdir / ".env").read_text(encoding="utf-8"))
    assert values["LYFTA_API_KEY"] == ""
    assert len(values["MCP_AUTH_TOKEN"]) >= config.MIN_TOKEN_LENGTH


def test_existing_env_file_is_not_overwritten(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = workdir / ".env"
    env.write_text("LYFTA_API_KEY=\nMCP_AUTH_TOKEN=\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main([])
    err = capsys.readouterr().err
    assert "LYFTA_API_KEY is not set" in err
    assert "created" not in err
    assert env.read_text(encoding="utf-8") == "LYFTA_API_KEY=\nMCP_AUTH_TOKEN=\n"


def test_starts_once_env_file_is_filled(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        main([])  # creates .env with a token
    env = workdir / ".env"
    env.write_text(
        env.read_text(encoding="utf-8").replace("LYFTA_API_KEY=", "LYFTA_API_KEY=my-key"),
        encoding="utf-8",
    )

    started: dict[str, Any] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: started.update(kwargs))
    main(["--port", "9000"])
    assert started["port"] == 9000
    assert started["host"] == "127.0.0.1"
