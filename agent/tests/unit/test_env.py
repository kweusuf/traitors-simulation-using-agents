from __future__ import annotations

from pathlib import Path

from simulation.env import load_dotenv, parse_env


def test_parse_env_ignores_comments_blanks_and_export() -> None:
    text = """
    # comment
    export OPENAI_COMPAT_API_KEY=abc123
    OTHER = "quoted value"
    SINGLE='single'

    nonsense line
    """
    assert parse_env(text) == {
        "OPENAI_COMPAT_API_KEY": "abc123",
        "OTHER": "quoted value",
        "SINGLE": "single",
    }


def test_load_dotenv_does_not_override_existing(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENAI_COMPAT_API_KEY=from-file\nNEW_KEY=new\n", encoding="utf-8")
    env = {"OPENAI_COMPAT_API_KEY": "from-os"}

    loaded = load_dotenv(path, env)

    assert env["OPENAI_COMPAT_API_KEY"] == "from-os"
    assert env["NEW_KEY"] == "new"
    assert loaded == {"NEW_KEY": "new"}


def test_load_dotenv_missing_file_is_noop(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    assert load_dotenv(tmp_path / "absent.env", env) == {}
    assert env == {}


def test_load_dotenv_uses_simulation_env_file(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "custom.env"
    path.write_text("OPENAI_COMPAT_API_KEY=custom\n", encoding="utf-8")
    monkeypatch.setenv("SIMULATION_ENV_FILE", str(path))
    env: dict[str, str] = {}

    load_dotenv(env=env)

    assert env == {"OPENAI_COMPAT_API_KEY": "custom"}
