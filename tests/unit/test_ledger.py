"""Unit tests for the score ledger (plan wave A, A1)."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.experiments.config import load_config
from simulation.experiments.ledger import is_holdout_seed
from simulation.experiments.runner import ARTIFACTS, GameRunner
from simulation.persistence.database import Database

EXPECTED_KEYS = {
    "game_id",
    "experiment_id",
    "seed",
    "holdout",
    "prompt_version",
    "model",
    "winner",
    "winning_team",
    "finale",
    "solo_traitor_win",
    "rounds",
    "calls",
    "input_tokens",
    "output_tokens",
    "latency_ms_p50",
    "latency_ms_p95",
    "rejected_actions",
    "quality",
}

EXPECTED_QUALITY_KEYS = {
    "hallucination_score",
    "duplication_score",
    "duplicate_rate",
    "speech_similarity",
    "secrecy_flags_total",
    "messages_checked",
}


def make_runner(tmp_path: Path) -> GameRunner:
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    return GameRunner(
        config,
        runs_dir=tmp_path / "runs",
        db=Database(),
        personas_dir=Path("configs/personas"),
    )


def read_ledger(tmp_path: Path) -> list[dict]:
    path = tmp_path / "runs" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_run_appends_one_ledger_line_with_expected_keys(tmp_path) -> None:
    result = make_runner(tmp_path).run(game_id="game-001", seed=7)
    lines = read_ledger(tmp_path)

    assert len(lines) == 1
    entry = lines[0]
    assert set(entry) == EXPECTED_KEYS
    assert entry["game_id"] == "game-001"
    assert entry["experiment_id"] == result.experiment_id
    assert entry["seed"] == 7
    assert entry["holdout"] is True  # 7 % 10 >= 7
    assert entry["prompt_version"] == result.metrics["prompt_version"]
    assert entry["model"].startswith("fake/")
    assert entry["winner"] in ("faithful", "traitor")
    assert entry["rounds"] >= 1
    assert isinstance(entry["calls"], int)
    assert set(entry["quality"]) == EXPECTED_QUALITY_KEYS
    assert entry["quality"]["messages_checked"] > 0
    # The fake provider reports no tokens: null, never a fabricated 0.
    assert entry["input_tokens"] is None
    assert entry["output_tokens"] is None


def test_ledger_appends_across_runs_with_per_seed_holdout_flag(tmp_path) -> None:
    runner = make_runner(tmp_path)
    runner.run(game_id="game-001", seed=7)
    runner.run(game_id="game-002", seed=42)

    lines = read_ledger(tmp_path)
    assert [line["game_id"] for line in lines] == ["game-001", "game-002"]
    assert [line["holdout"] for line in lines] == [True, False]  # 42 % 10 == 2


def test_ledger_is_not_a_per_run_artifact(tmp_path) -> None:
    runner = make_runner(tmp_path)
    result = runner.run(game_id="game-001", seed=1)

    assert "ledger.jsonl" not in ARTIFACTS
    assert not (result.run_dir / "ledger.jsonl").exists()
    assert (result.run_dir.parent / "ledger.jsonl").exists()


def test_is_holdout_seed_boundaries() -> None:
    # 30 percent of seeds: the last three of every ten.
    assert not is_holdout_seed(0)
    assert not is_holdout_seed(6)
    assert is_holdout_seed(7)
    assert is_holdout_seed(9)
    assert not is_holdout_seed(10)
    assert not is_holdout_seed(16)
    assert is_holdout_seed(17)
    assert is_holdout_seed(19)
    assert not is_holdout_seed(42)
