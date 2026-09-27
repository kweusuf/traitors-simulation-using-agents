"""Unit tests for the experiment runner and run artifacts (spec 26, 27, 34, 43)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simulation.experiments.config import GameConfig, LLMSettings, load_config
from simulation.experiments.runner import (
    ARTIFACTS,
    GameRunner,
    build_provider,
    model_identity,
)
from simulation.models.fake import PromptScriptProvider
from simulation.persistence.database import Database
from simulation.persistence.repositories import (
    ExperimentRepository,
    GameRepository,
)

PERSONAS_DIR = Path("configs/personas")


def fake_config(**overrides) -> GameConfig:
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def make_runner(tmp_path: Path, config: GameConfig | None = None, **kwargs) -> GameRunner:
    kwargs.setdefault("runs_dir", tmp_path / "runs")
    kwargs.setdefault("db", Database())
    kwargs.setdefault("personas_dir", PERSONAS_DIR)
    return GameRunner(config or fake_config(), **kwargs)


# ----------------------------------------------------------------------
# Artifacts and identity
# ----------------------------------------------------------------------


def test_run_writes_all_artifacts(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.run(game_id="game-001", seed=42)

    assert result.winner in ("faithful", "traitor")
    assert result.rounds >= 1
    for name in ARTIFACTS:
        assert (result.run_dir / name).exists(), f"missing artifact {name}"

    events = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "GAME_ENDED"
    assert len(events) == len(result.events)

    saved_config = yaml.safe_load((result.run_dir / "config.yaml").read_text())
    assert saved_config["seed"] == 42
    assert saved_config["llm"]["provider"] == "fake"

    state = json.loads((result.run_dir / "game.json").read_text())
    assert state["winner"] == result.winner
    assert len(state["players"]) == 6


def test_metrics_carry_experiment_identity(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.run(game_id="game-001", seed=7)
    metrics = json.loads((result.run_dir / "metrics.json").read_text())

    assert metrics["experiment_id"] == result.experiment_id
    assert metrics["game_id"] == "game-001"
    assert metrics["random_seed"] == 7
    # Model id comes from config (not hardcoded), provider prefix from code.
    assert metrics["model"].startswith("fake/")
    assert metrics["model_parameters"]["temperature"] > 0
    assert metrics["model_parameters"]["max_tokens"] >= 1
    assert metrics["prompt_version"]
    assert metrics["persona_version"]
    assert metrics["game_rules_version"]
    assert metrics["memory_strategy"]
    assert metrics["events"]["by_type"]["PUBLIC_MESSAGE"] >= 1
    assert metrics["messages"]["public"] >= 1
    assert metrics["llm"]["calls"] >= 1
    assert metrics["winner"] == result.winner


def test_metrics_written_even_for_truncated_game(tmp_path) -> None:
    config = fake_config()
    config.game.max_rounds = 1
    result = make_runner(tmp_path, config).run(game_id="game-001", seed=1)
    metrics = json.loads((result.run_dir / "metrics.json").read_text())
    assert metrics["rounds"] == 1
    assert metrics["status"] == "completed"


def test_run_records_identity_in_database(tmp_path) -> None:
    db = Database()
    result = make_runner(tmp_path, db=db).run(game_id="game-001", seed=11)

    experiment = ExperimentRepository(db).get(result.experiment_id)
    assert experiment is not None
    assert experiment["model"].startswith("fake/")
    assert experiment["random_seed"] == 11
    assert experiment["prompt_version"]
    assert experiment["memory_strategy"]

    game = GameRepository(db).get("game-001")
    assert game is not None
    assert game["seed"] == 11
    assert game["status"] == "completed"
    assert game["winner"] == result.winner


# ----------------------------------------------------------------------
# Determinism (spec sections 26 and 33)
# ----------------------------------------------------------------------


def _without_volatile(metrics: dict) -> dict:
    return {
        k: v for k, v in metrics.items() if k not in {"experiment_id", "duration_seconds"}
    }


def test_same_seed_reproduces_same_game(tmp_path) -> None:
    first = make_runner(tmp_path / "a").run(game_id="game-001", seed=42)
    second = make_runner(tmp_path / "b").run(game_id="game-001", seed=42)
    assert first.events == second.events
    assert _without_volatile(first.metrics) == _without_volatile(second.metrics)


def test_batch_uses_one_deterministic_seed_per_game(tmp_path) -> None:
    runner = make_runner(tmp_path)
    results = runner.run_batch(3, base_seed=100)

    assert [r.seed for r in results] == [100, 101, 102]
    assert [r.game_id for r in results] == ["game-001", "game-002", "game-003"]
    assert len({r.experiment_id for r in results}) == 1  # one experiment, 3 games
    for result in results:
        assert (result.run_dir / "metrics.json").exists()


def test_batch_rejects_non_positive_count(tmp_path) -> None:
    with pytest.raises(ValueError, match="batch size"):
        make_runner(tmp_path).run_batch(0)


def test_next_game_id_skips_existing(tmp_path) -> None:
    runner = make_runner(tmp_path)
    runner.run(game_id="game-001", seed=1)
    runner.run(game_id="game-002", seed=2)
    assert runner.next_game_id() == "game-003"


# ----------------------------------------------------------------------
# Personas and providers
# ----------------------------------------------------------------------


def test_personas_are_loaded_from_config(tmp_path) -> None:
    config = fake_config()
    config.game.personas = ["analytical", "contrarian"]
    provider = PromptScriptProvider()
    runner = make_runner(tmp_path, config, provider=provider)
    runner.run(game_id="game-001", seed=3)

    system_prompts = [
        m.content for call in provider.calls for m in call if m.role == "system"
    ]
    assert any("Methodical reasoner" in prompt for prompt in system_prompts)
    # Six players, two persona files: both personas are in use.
    assert any("Challenges consensus" in prompt for prompt in system_prompts)


def test_missing_persona_fails_loudly(tmp_path) -> None:
    config = fake_config()
    config.game.personas = ["nonexistent"]
    runner = make_runner(tmp_path, config)
    with pytest.raises(ValueError, match="persona 'nonexistent' not found"):
        runner.run(game_id="game-001", seed=1)


def test_personas_without_directory_fail_loudly(tmp_path) -> None:
    config = fake_config()
    config.game.personas = ["analytical"]
    runner = make_runner(tmp_path, config, personas_dir=None)
    with pytest.raises(ValueError, match="no personas directory"):
        runner.run(game_id="game-001", seed=1)


def test_build_provider_honours_config() -> None:
    assert isinstance(build_provider(LLMSettings(provider="fake")), PromptScriptProvider)
    assert build_provider(LLMSettings(provider="ollama")).__class__.__name__ == (
        "OllamaProvider"
    )
    with pytest.raises(ValueError, match="unknown llm provider"):
        build_provider(LLMSettings(provider="grok"))


def test_model_identity_shape() -> None:
    label, parameters = model_identity(LLMSettings(provider="ollama", model="m:1"))
    assert label == "ollama/m:1"
    assert parameters["max_tokens"] == 512
    assert parameters["options"] == {}


# ----------------------------------------------------------------------
# LLM telemetry and quality metrics (Phase 15)
# ----------------------------------------------------------------------


def test_metrics_carry_per_call_telemetry(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.run(game_id="game-001", seed=42)
    llm = result.metrics["llm"]

    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "token_reported_calls",
        "prompt_chars_total",
        "latency_ms",
        "by_action_type",
        "failed_calls",
        "transport_retries",
        "failures",
        "provider_calls",
        "retries",
        "model",
    ):
        assert key in llm, f"missing llm metric {key}"
    assert llm["calls"] > 0
    assert llm["provider_calls"] == llm["calls"]  # the fake provider never fails
    assert llm["prompt_chars_total"] > 0
    assert llm["total_tokens"] == llm["input_tokens"] + llm["output_tokens"]
    assert "public_message" in llm["by_action_type"]

    # The per-call log lives inside the run and matches the summary.
    lines = (result.run_dir / "llm_calls.jsonl").read_text().splitlines()
    assert len(lines) == llm["calls"]
    first = json.loads(lines[0])
    assert first["agent_id"]
    assert first["action_type"]
    assert first["ok"] is True
    assert first["attempt"] == 1


def test_metrics_carry_quality_signals(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.run(game_id="game-001", seed=42)
    quality = result.metrics["quality"]

    assert quality["messages_checked"] > 0
    assert 0.0 <= quality["hallucination_score"] <= 1.0
    for section in ("hallucination", "secrecy", "diversity", "parsing"):
        assert section in quality
    # The fake provider writes templated messages: no contradictions and
    # no role declarations are expected here.
    assert quality["hallucination_score"] == 0.0
    assert quality["secrecy"]["flags_total"] == 0
