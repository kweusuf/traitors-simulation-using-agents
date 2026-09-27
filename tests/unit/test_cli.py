"""Unit tests for the CLI (spec sections 31 and 43)."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.cli.main import make_progress_printer, main
from simulation.persistence.event_log import Event, EventType

CONFIG = "configs/traitors/basic.yaml"


def run_cli(args: list[str], capsys) -> tuple[int, str]:
    code = main(args)
    return code, capsys.readouterr().out


def base_args(tmp_path: Path) -> list[str]:
    return [
        "--runs-dir", str(tmp_path / "runs"),
        "--db", str(tmp_path / "sim.db"),
    ]


def test_run_produces_complete_game(tmp_path, capsys) -> None:
    code, out = run_cli(
        ["run", CONFIG, "--provider", "fake", "--seed", "42", "--quiet"]
        + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    assert "Winner: " in out
    assert "Saved:" in out
    run_dir = tmp_path / "runs" / "game-001"
    for name in ("config.yaml", "events.jsonl", "game.json",
                 "transcript.json", "transcript.txt", "metrics.json"):
        assert (run_dir / name).exists(), f"missing {name}"


def test_run_prints_progress_by_default(tmp_path, capsys) -> None:
    code, out = run_cli(
        ["run", CONFIG, "--provider", "fake"] + base_args(tmp_path), capsys
    )
    assert code == 0
    assert "Starting game game-001" in out
    assert "Players: 6" in out
    assert "Round 1" in out
    assert "Game complete." in out


def test_run_rejects_unknown_config(tmp_path, capsys) -> None:
    code, _ = run_cli(
        ["run", "configs/traitors/nope.yaml", "--quiet"] + base_args(tmp_path),
        capsys,
    )
    assert code == 1


def test_batch_writes_multiple_games(tmp_path, capsys) -> None:
    code, out = run_cli(
        ["batch", CONFIG, "--games", "2", "--seed", "50", "--provider", "fake",
         "--quiet"] + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    assert "game-001" in out and "game-002" in out
    runs = tmp_path / "runs"
    assert sorted(p.name for p in runs.iterdir()) == ["game-001", "game-002"]
    seeds = [
        json.loads((runs / g / "metrics.json").read_text())["random_seed"]
        for g in ("game-001", "game-002")
    ]
    assert seeds == [50, 51]


def test_list_games_reads_metrics(tmp_path, capsys) -> None:
    run_cli(["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path), capsys)
    capsys.readouterr()
    code, out = run_cli(["list-games", "--runs-dir", str(tmp_path / "runs")], capsys)
    assert code == 0
    assert "game-001" in out
    assert "winner" in out.lower() or "faithful" in out or "traitor" in out


def test_list_games_empty_runs_dir(tmp_path, capsys) -> None:
    code, out = run_cli(["list-games", "--runs-dir", str(tmp_path)], capsys)
    assert code == 0
    assert "No games" in out


def test_replay_and_inspect(tmp_path, capsys) -> None:
    run_cli(["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path), capsys)
    capsys.readouterr()
    runs = str(tmp_path / "runs")

    code, out = run_cli(["replay", "game-001", "--runs-dir", runs], capsys)
    assert code == 0
    assert "Winner: " in out and "Rounds played:" in out

    code, out = run_cli(["replay", "game-001", "--runs-dir", runs, "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["game_id"] == "game-001"
    assert payload["winner"] in ("faithful", "traitor")
    assert len(payload["players"]) == 6

    code, out = run_cli(["inspect", "game-001", "--agent", "alice",
                         "--runs-dir", runs], capsys)
    assert code == 0
    assert "Your role: " in out
    assert "Alive players:" in out


def test_inspect_rejects_unknown_agent(tmp_path, capsys) -> None:
    run_cli(["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path), capsys)
    capsys.readouterr()
    code, out = run_cli(
        ["inspect", "game-001", "--agent", "mallory",
         "--runs-dir", str(tmp_path / "runs")],
        capsys,
    )
    assert code == 1


def test_replay_rejects_unknown_game(tmp_path, capsys) -> None:
    code, out = run_cli(["replay", "game-999", "--runs-dir", str(tmp_path)], capsys)
    assert code == 1


def test_snapshot_from_database(tmp_path, capsys) -> None:
    run_cli(["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path), capsys)
    capsys.readouterr()
    code, out = run_cli(
        ["snapshot", "game-001", "--round", "1",
         "--runs-dir", str(tmp_path / "runs"), "--db", str(tmp_path / "sim.db")],
        capsys,
    )
    assert code == 0
    assert "Snapshot game-001 round 1" in out
    assert "Alive:" in out


def test_snapshot_rejects_missing_round(tmp_path, capsys) -> None:
    run_cli(["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path), capsys)
    capsys.readouterr()
    code, out = run_cli(
        ["snapshot", "game-001", "--round", "99",
         "--runs-dir", str(tmp_path / "runs"), "--db", str(tmp_path / "sim.db")],
        capsys,
    )
    assert code == 1


def test_provider_flag_overrides_config(tmp_path, capsys) -> None:
    # Config says ollama; --provider fake must keep the run offline.
    code, out = run_cli(
        ["run", CONFIG, "--provider", "fake", "--quiet"] + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    metrics = json.loads(
        (tmp_path / "runs" / "game-001" / "metrics.json").read_text()
    )
    assert metrics["model"].startswith("fake/")
    assert metrics["model_parameters"]["max_concurrency"] >= 1


def test_base_url_and_model_flags_override_config(tmp_path, capsys) -> None:
    # Remote-Ollama workflow: point a run at another host without
    # editing the YAML (still offline because provider=fake).
    remote = "http://10.0.0.5:11434"
    code, _ = run_cli(
        ["run", CONFIG, "--provider", "fake", "--model", "remote-model",
         "--base-url", remote, "--quiet"] + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    metrics = json.loads(
        (tmp_path / "runs" / "game-001" / "metrics.json").read_text()
    )
    assert metrics["model"] == "fake/remote-model"
    assert metrics["model_parameters"]["base_url"] == remote


def test_batch_accepts_base_url_flag(tmp_path, capsys) -> None:
    code, _ = run_cli(
        ["batch", CONFIG, "--games", "1", "--seed", "7", "--provider", "fake",
         "--base-url", "http://10.0.0.5:11434", "--quiet"] + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    metrics = json.loads(
        (tmp_path / "runs" / "game-001" / "metrics.json").read_text()
    )
    assert metrics["model_parameters"]["base_url"] == "http://10.0.0.5:11434"


def test_progress_printer_reports_rejected_actions(capsys) -> None:
    observe = make_progress_printer()
    observe(
        Event(
            event_id="e1",
            game_id="game-001",
            sequence=1,
            type=EventType.ACTION_REJECTED,
            actor="alice",
            payload={"reason": "self-vote\nnot allowed"},
        )
    )
    out = capsys.readouterr().out
    assert "alice: action rejected (self-vote not allowed)" in out


# ----------------------------------------------------------------------
# metrics command (LLM ops + quality, 360 view of a run)
# ----------------------------------------------------------------------


def play_fake(tmp_path, capsys) -> Path:
    code, _ = run_cli(
        ["run", CONFIG, "--provider", "fake", "--seed", "42", "--quiet"]
        + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    return tmp_path / "runs"


def test_metrics_command_shows_llm_and_quality(tmp_path, capsys) -> None:
    runs = play_fake(tmp_path, capsys)
    code, out = run_cli(["metrics", "game-001", "--runs-dir", str(runs)], capsys)

    assert code == 0
    assert "LLM operations:" in out
    assert "tokens: in=" in out
    assert "latency ms:" in out
    assert "public_message: calls=" in out
    assert "Quality: hallucination_score=" in out
    assert "secrecy: traitor declarations=" in out


def test_metrics_recompute_restores_a_missing_quality_block(tmp_path, capsys) -> None:
    runs = play_fake(tmp_path, capsys)
    metrics_path = runs / "game-001" / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    del metrics["quality"]
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    code, out = run_cli(
        ["metrics", "game-001", "--recompute", "--runs-dir", str(runs)], capsys
    )
    assert code == 0
    assert "recomputed quality" in out
    restored = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert "quality" in restored
    assert restored["quality"]["messages_checked"] > 0


def test_metrics_on_an_unfinished_run_never_invents_metrics(tmp_path, capsys) -> None:
    runs = play_fake(tmp_path, capsys)
    metrics_path = runs / "game-001" / "metrics.json"
    metrics_path.unlink()

    code, _ = run_cli(["metrics", "game-001", "--runs-dir", str(runs)], capsys)
    assert code == 1

    code, out = run_cli(
        ["metrics", "game-001", "--recompute", "--runs-dir", str(runs)], capsys
    )
    assert code == 0
    assert "never finished" in out
    assert "hallucination_score" in out
    assert not metrics_path.exists()  # no fake artifact for a dead run
