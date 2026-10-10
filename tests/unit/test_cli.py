"""Unit tests for the CLI (spec sections 31 and 43)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from simulation.cli.main import make_progress_printer, main
from simulation.persistence.event_log import Event, EventType

CONFIG = "configs/traitors/basic.yaml"


def run_cli(args: list[str], capsys) -> tuple[int, str]:
    # Tests start games for their own sake, on the fake backend, with results
    # thrown away. The bug gate exists to stop an *experiment* being corrupted
    # by a known defect, so it does not apply here - but it must stay active
    # everywhere else, which is why this bypass is set here and nowhere else.
    os.environ["SIMULATION_IGNORE_BUG_GATE"] = "1"
    try:
        code = main(args)
    finally:
        os.environ.pop("SIMULATION_IGNORE_BUG_GATE", None)
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


def test_progress_printer_reports_the_hosted_round_table(capsys) -> None:
    """The live host lines: who faces the room, and what the revote did."""
    observe = make_progress_printer()
    observe(
        Event(
            event_id="evt-1",
            game_id="game-001",
            sequence=1,
            type=EventType.NOMINATION_TALLY,
            targets=["alice", "bob"],
            payload={"nominees": ["alice", "bob"], "counts": {"alice": 3, "bob": 2}},
        )
    )
    observe(
        Event(
            event_id="evt-2",
            game_id="game-001",
            sequence=2,
            type=EventType.REVOTE_CALLED,
            targets=["alice", "bob"],
            payload={"targets": ["alice", "bob"]},
        )
    )
    observe(
        Event(
            event_id="evt-3",
            game_id="game-001",
            sequence=3,
            type=EventType.REVOTE_RESOLVED,
            targets=["alice"],
            payload={"top": ["alice"], "tie": False, "counts": {"alice": 1}},
        )
    )
    observe(
        Event(
            event_id="evt-4",
            game_id="game-001",
            sequence=4,
            type=EventType.REVOTE_RESOLVED,
            payload={"top": [], "tie": True, "counts": {}},
        )
    )
    out = capsys.readouterr().out
    assert "Nominations tallied, facing the room: alice, bob" in out
    assert "Revote: alice and bob must be chosen between" in out
    assert "Revote banished: alice" in out
    assert "Revote tied again: nobody eliminated" in out


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
    assert sorted(p.name for p in runs.iterdir() if p.is_dir()) == [
        "game-001",
        "game-002",
    ]
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


def test_concurrency_flag_overrides_config(tmp_path, capsys) -> None:
    # The sweep arms pin max_concurrency to 1 so every arm can run at once.
    # Running one arm alone wants the level the host actually serves, and
    # that is a property of the moment, not of the arm's rules, so it
    # belongs on the command line rather than in a generated config.
    code, _ = run_cli(
        ["run", CONFIG, "--provider", "fake", "--concurrency", "4", "--quiet"]
        + base_args(tmp_path),
        capsys,
    )
    assert code == 0
    metrics = json.loads(
        (tmp_path / "runs" / "game-001" / "metrics.json").read_text()
    )
    assert metrics["model_parameters"]["max_concurrency"] == 4


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
    assert "fabricated attributions=" in out
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


# ----------------------------------------------------------------------
# compare: paired-seed gates (plan wave A, A2)
# ----------------------------------------------------------------------


def write_metrics(run_dir: Path, **overrides) -> None:
    """A small complete metrics.json for gate tests."""
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "random_seed": 42,
        "winner": "faithful",
        "rounds": 3,
        "finale": "vote",
        "solo_traitor_win": False,
        "quality": {
            "hallucination_score": 0.0,
            "diversity": {"duplication_score": 0.05, "duplicate_rate": 0.0},
            "speech_similarity": {
                "content_words": {"mean": 0.3},
                "phrasing": {"mean": 0.4},
            },
            "secrecy": {"flags_total": 0},
            "parsing": {"rejected_actions": 1},
        },
        "llm": {"total_tokens": 1000, "latency_ms": {"p95": 120.0}},
    }
    metrics.update(overrides)
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")


def test_compare_passes_on_identical_runs(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    write_metrics(runs / "game-001")
    write_metrics(runs / "game-002")

    code, out = run_cli(
        ["compare", "game-001", "game-002", "--runs-dir", str(runs)], capsys
    )
    assert code == 0
    assert "hallucination_score" in out
    assert "PASS" in out
    assert "FAIL" not in out
    assert "never gated" in out  # outcome fields are information only
    assert "All gates pass." in out


def test_compare_exits_one_when_a_gate_is_breached(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    write_metrics(runs / "game-001")
    # 0.05 > baseline 0.0 + 0.02: hallucination gate must fail.
    worse_quality = {
        "hallucination_score": 0.05,
        "diversity": {"duplication_score": 0.05, "duplicate_rate": 0.0},
        "speech_similarity": {
            "content_words": {"mean": 0.3},
            "phrasing": {"mean": 0.4},
        },
        "secrecy": {"flags_total": 0},
        "parsing": {"rejected_actions": 1},
    }
    write_metrics(runs / "game-002", quality=worse_quality)

    code, out = run_cli(
        ["compare", "game-001", "game-002", "--runs-dir", str(runs)], capsys
    )
    assert code == 1
    assert "hallucination_score" in out
    assert "FAIL" in out
    assert "do not promote" in out


def test_compare_json_dumps_every_gate(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    write_metrics(runs / "game-001")
    write_metrics(runs / "game-002")

    code, out = run_cli(
        ["compare", "game-001", "game-002", "--runs-dir", str(runs), "--json"],
        capsys,
    )
    payload = json.loads(out)
    assert code == 0
    assert payload["passed"] is True
    assert payload["holdout_candidate"] is False
    assert {gate["metric"] for gate in payload["gates"]} == {
        "hallucination_score",
        "duplication_score",
        "speech_similarity.content_words.mean",
        "speech_similarity.phrasing.mean",
        "secrecy.flags_total",
        "parsing.rejected_actions",
        "llm.latency_ms.p95",
        "llm.total_tokens",
    }
    assert payload["outcome"]["winner"]["baseline"] == "faithful"


def test_compare_warns_on_a_holdout_candidate_seed(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    write_metrics(runs / "game-001", random_seed=42)
    write_metrics(runs / "game-002", random_seed=7)  # 7 % 10 >= 7: holdout

    code, out = run_cli(
        ["compare", "game-001", "game-002", "--runs-dir", str(runs)], capsys
    )
    assert code == 0  # a warning, not a gate: the gates decide the exit
    assert "WARNING" in out
    assert "holdout seed" in out
    assert "seed 7" in out


# ----------------------------------------------------------------------
# diagnose: deterministic post-run report (plan wave A, A3)
# ----------------------------------------------------------------------


def test_diagnose_writes_report_with_top_repeated_text(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "game-001"
    run_dir.mkdir(parents=True)
    repeated = "We should all vote together today."
    events = [
        {"event_id": "e1", "game_id": "game-001", "sequence": 1,
         "type": "GAME_STARTED",
         "payload": {"players": ["alice", "bob", "carol"]}},
        {"event_id": "e2", "game_id": "game-001", "sequence": 2, "round": 1,
         "type": "PUBLIC_MESSAGE", "actor": "alice",
         "payload": {"content": repeated}},
        {"event_id": "e3", "game_id": "game-001", "sequence": 3, "round": 1,
         "type": "PUBLIC_MESSAGE", "actor": "bob",
         "payload": {"content": repeated}},
        {"event_id": "e4", "game_id": "game-001", "sequence": 4, "round": 1,
         "type": "PUBLIC_MESSAGE", "actor": "carol",
         "payload": {"content": repeated}},
        {"event_id": "e5", "game_id": "game-001", "sequence": 5, "round": 1,
         "type": "PUBLIC_MESSAGE", "actor": "alice",
         "payload": {"content": "One lone take that nobody echoes."}},
        {"event_id": "e6", "game_id": "game-001", "sequence": 6,
         "type": "GAME_ENDED",
         "payload": {"rounds": 1, "winner": "faithful"}},
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    code, out = run_cli(["diagnose", "game-001", "--runs-dir", str(runs)], capsys)
    assert code == 0
    report = (run_dir / "diagnosis.md").read_text(encoding="utf-8")
    assert report in out  # the report is printed as well as written
    assert "## Metric summary" in out
    assert "## Most repeated messages" in out
    assert "3 messages (alice, bob, carol)" in out
    assert repeated in out
    assert "## Secrecy samples" in out
    assert "## Hallucination samples" in out
    assert "## Most similar player pair" in out
    assert "## Hypotheses" in out
    # Two of four texts repeat across authors: duplication_score 0.5
    # crosses the documented 0.15 threshold and must be proposed.
    assert "duplication_score > 0.15" in out
    assert "no metrics.json" in out  # metrics were never written here
