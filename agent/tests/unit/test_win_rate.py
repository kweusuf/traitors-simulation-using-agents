"""Tests for the win-rate reporting used by the traitor-bias experiments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simulation.experiments.win_rate import (
    format_report,
    group,
    load_outcomes,
    summarise,
    wilson_interval,
)


def _run(
    root: Path,
    name: str,
    winner: str | None,
    *,
    status: str = "completed",
    game: dict | None = None,
    llm: dict | None = None,
    config: bool = True,
) -> None:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"status": status, "winner": winner}), encoding="utf-8"
    )
    if config:
        (run_dir / "config.yaml").write_text(
            yaml.safe_dump({"game": game or {}, "llm": llm or {}}),
            encoding="utf-8",
        )


def test_wilson_interval_bounds() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(1, 2)
    assert low == pytest.approx(0.0945, abs=0.002)
    assert high == pytest.approx(0.9055, abs=0.002)
    low, high = wilson_interval(29, 48)
    assert low < 29 / 48 < high


def test_load_counts_only_completed_winners(tmp_path: Path) -> None:
    _run(tmp_path, "a", "traitor")
    _run(tmp_path, "b", "faithful")
    _run(tmp_path, "c", "traitor", status="running")
    _run(tmp_path, "d", None)

    outcomes = load_outcomes(tmp_path)
    assert {o.run_id for o in outcomes} == {"a", "b"}
    overall = summarise(outcomes)
    assert overall.total == 2
    assert overall.successes == 1
    assert overall.rate == pytest.approx(0.5)


def test_missing_config_falls_back_to_defaults(tmp_path: Path) -> None:
    _run(tmp_path, "a", "traitor", config=False)
    outcome = load_outcomes(tmp_path)[0]
    assert outcome.pinned is False
    assert outcome.ledger is False
    assert outcome.mandate is True  # default on
    assert outcome.finale is False
    assert outcome.model is None


def test_segments_read_the_config_snapshot(tmp_path: Path) -> None:
    _run(
        tmp_path,
        "pinned-ledger",
        "traitor",
        game={"traitor_names": ["x"], "suspicion_ledger": True, "finale_total": 5},
        llm={"model": "m1"},
    )
    _run(
        tmp_path,
        "random-nomandate",
        "faithful",
        game={"traitor_mandate": False},
        llm={"model": "m2"},
    )

    outcomes = load_outcomes(tmp_path)

    pinned = group(outcomes, "pinned")
    assert set(pinned) == {"pinned", "random"}
    assert [o.run_id for o in pinned["pinned"]] == ["pinned-ledger"]

    ledger = group(outcomes, "ledger")
    assert "ledger-on" in ledger and "ledger-off" in ledger

    mandate = group(outcomes, "mandate")
    assert [o.run_id for o in mandate["mandate-off"]] == ["random-nomandate"]
    assert [o.run_id for o in mandate["mandate-on"]] == ["pinned-ledger"]

    finale = group(outcomes, "finale")
    assert [o.run_id for o in finale["finale"]] == ["pinned-ledger"]

    models = group(outcomes, "model")
    assert set(models) == {"m1", "m2"}


def test_include_filters_arms(tmp_path: Path) -> None:
    _run(tmp_path, "bias-ledger-r1", "traitor")
    _run(tmp_path, "bias-rand-r1", "faithful")
    _run(tmp_path, "season_uk_s01-opencode-go", "traitor")

    assert {o.run_id for o in load_outcomes(tmp_path, include=["bias-"])} == {
        "bias-ledger-r1",
        "bias-rand-r1",
    }


def test_format_report_has_overall_and_segments(tmp_path: Path) -> None:
    _run(tmp_path, "a", "traitor", game={"traitor_names": ["x"]})
    _run(tmp_path, "b", "faithful")
    report = format_report(load_outcomes(tmp_path), by=["pinned"])
    assert "completed runs: 2" in report
    assert "traitor win rate: 50% (9-91%)" in report
    assert "by pinned:" in report
    assert "pinned" in report and "random" in report
