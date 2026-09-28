"""Unit tests for the season benchmark (phase 21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulation.cli.main import main
from simulation.experiments.season import (
    COMPONENT_WEIGHTS,
    Season,
    benchmark_run,
    load_season,
)
from simulation.persistence.event_log import Event, EventType

SEASON_ID = "test-season"
SOURCE_URL = "https://example.test/season-one"

SEASON_YAML = f"""\
# Sources:
#   {SOURCE_URL}
season:
  id: {SEASON_ID}
  name: Test Season
  winner_faction: faithful
  final_counts:
    faithful: 2
    traitors: 0
cast:
  - {{id: ana, role: traitor, traitor_type: original, exit_episode: 2}}
  - {{id: ben, role: faithful, exit_episode: 1}}
  - {{id: cara, role: faithful, exit_episode: 3}}
  - {{id: dan, role: faithful, exit_episode: 3}}
eliminations:
  - {{episode: 1, night_victim: ben, banishment: null}}
  - {{episode: 2, night_victim: null, banishment: ana}}
recruitments: []
"""


def ev(
    seq: int,
    type_: EventType,
    *,
    round: int = 0,
    actor: str | None = None,
    targets: list[str] | None = None,
    **payload,
) -> Event:
    return Event(
        event_id=f"e{seq}",
        game_id="game-001",
        sequence=seq,
        round=round,
        phase="",
        type=type_,
        actor=actor,
        targets=targets or [],
        payload=payload,
    )


def write_season(tmp_path: Path) -> Season:
    path = tmp_path / "season.yaml"
    path.write_text(SEASON_YAML, encoding="utf-8")
    return load_season(path)


def opening(rounds: int = 2, winner: str = "faithful") -> list[Event]:
    return [
        ev(1, EventType.GAME_STARTED, players=["ana", "ben", "cara", "dan"]),
        ev(2, EventType.ROLE_ASSIGNED, actor="ana", role="traitor"),
        ev(3, EventType.ROLE_ASSIGNED, actor="ben", role="faithful"),
        ev(4, EventType.ROLE_ASSIGNED, actor="cara", role="faithful"),
        ev(5, EventType.ROLE_ASSIGNED, actor="dan", role="faithful"),
        ev(6, EventType.ROUND_STARTED, round=1),
    ]


def perfect_events() -> list[Event]:
    """A run that reproduces the synthetic season exactly."""
    return opening() + [
        ev(7, EventType.TRAITOR_KILL, round=1, targets=["ben"]),
        ev(8, EventType.PLAYER_ELIMINATED, round=1, actor="ben", method="night"),
        ev(9, EventType.ROUND_STARTED, round=2),
        ev(10, EventType.PLAYER_ELIMINATED, round=2, actor="ana", method="vote"),
        ev(11, EventType.GAME_WON, actor="host", team="faithful"),
        ev(12, EventType.GAME_ENDED, rounds=2, winner="faithful"),
    ]


def mismatched_events() -> list[Event]:
    """The same shape, but the run banishes a faithful and loses."""
    return opening() + [
        ev(7, EventType.TRAITOR_KILL, round=1, targets=["ben"]),
        ev(8, EventType.PLAYER_ELIMINATED, round=1, actor="ben", method="night"),
        ev(9, EventType.ROUND_STARTED, round=2),
        ev(10, EventType.PLAYER_ELIMINATED, round=2, actor="cara", method="vote"),
        ev(11, EventType.GAME_WON, actor="host", team="traitor"),
        ev(12, EventType.GAME_ENDED, rounds=2, winner="traitor"),
    ]


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def test_load_season_reads_the_structure(tmp_path) -> None:
    season = write_season(tmp_path)
    assert season.id == SEASON_ID
    assert season.winner_faction == "faithful"
    assert season.final_counts == {"faithful": 2, "traitors": 0}
    assert [member.id for member in season.cast] == ["ana", "ben", "cara", "dan"]
    assert season.eliminations[0].night_victim == "ben"
    assert season.eliminations[1].banishment == ("ana",)
    assert season.sources == (SOURCE_URL,)


def test_malformed_season_raises_value_error(tmp_path) -> None:
    path = tmp_path / "season.yaml"
    path.write_text("season:\n  id: broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="winner_faction"):
        load_season(path)


def test_missing_season_file_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_season(tmp_path / "nope.yaml")


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


def test_perfect_match_scores_one_on_aligned_components(tmp_path) -> None:
    result = benchmark_run(perfect_events(), write_season(tmp_path))
    assert result["outcome"]["score"] == 1.0
    assert result["outcome"]["matched"] is True
    assert result["traitor_roster"]["score"] == 1.0
    assert result["banishment_alignment"]["match_rate"] == 1.0
    assert result["banishment_alignment"]["pairs"][0]["season"] == "ana"
    assert result["banishment_overlap"]["score"] == 1.0
    assert result["murder_alignment"]["match_rate"] == 1.0
    assert result["murder_alignment"]["pairs"][0]["run"] == "ben"
    assert result["murder_overlap"]["score"] == 1.0
    assert result["traitor_hit_rate"]["score"] == 1.0
    assert result["survival_curve"]["mean_distance"] == 0.0
    assert result["survival_curve"]["score"] == 1.0
    assert result["final_counts"]["score"] == 1.0
    assert result["recruitments"]["score"] == 1.0
    assert result["exit_order"]["match_rate"] == 1.0
    assert result["overall"]["score"] == 1.0


def test_mismatch_lowers_the_overall_score(tmp_path) -> None:
    season = write_season(tmp_path)
    perfect = benchmark_run(perfect_events(), season)
    mismatched = benchmark_run(mismatched_events(), season)

    assert mismatched["overall"]["score"] < perfect["overall"]["score"]
    assert mismatched["outcome"]["score"] == 0.0
    assert mismatched["banishment_alignment"]["match_rate"] == 0.0
    assert mismatched["banishment_overlap"]["score"] == 0.0
    assert mismatched["traitor_hit_rate"]["score"] == 0.0


def test_run_without_finale_reports_null_and_excludes_it(tmp_path) -> None:
    result = benchmark_run(perfect_events(), write_season(tmp_path))
    assert result["finale"]["run"] is None
    assert result["finale"]["score"] is None
    assert "finale" in result["overall"]["components_skipped"]
    assert "finale" not in result["overall"]["components_scored"]
    # The skipped weight is redistributed, so the aligned case still tops out.
    assert result["overall"]["score"] == 1.0


def test_finale_run_is_scored_against_the_final_counts(tmp_path) -> None:
    events = opening() + [
        ev(7, EventType.TRAITOR_KILL, round=1, targets=["ben"]),
        ev(8, EventType.PLAYER_ELIMINATED, round=1, actor="ben", method="night"),
        ev(9, EventType.FINALE_STARTED, traitors=["ana"], faithful=["cara", "dan"]),
        ev(10, EventType.GAME_WON, team="faithful"),
        ev(11, EventType.GAME_ENDED, rounds=2, winner="faithful"),
    ]
    result = benchmark_run(events, write_season(tmp_path))
    assert result["finale"]["run"] == {"traitors": 1, "faithful": 2}
    # The synthetic season's finale split is two faithful and no traitors,
    # so this run matches on faithful alone and scores half.
    assert result["finale"]["matched"] == {"traitors": False, "faithful": True}
    assert result["finale"]["score"] == 0.5


def test_index_alignment_skips_a_season_episode_without_elimination(tmp_path) -> None:
    # Episode 1 has no banishment, so the season's first banishment is
    # episode 2's ana. The run's first banishment pairs with that, not
    # with the empty episode 1 slot, so it is judged as a hit.
    events = opening() + [
        ev(7, EventType.PLAYER_ELIMINATED, round=1, actor="ana", method="vote"),
        ev(8, EventType.GAME_ENDED, rounds=1, winner="faithful"),
    ]
    component = benchmark_run(events, write_season(tmp_path))[
        "banishment_alignment"
    ]
    assert component["pairs"][0]["run"] == "ana"
    assert component["pairs"][0]["season"] == "ana"
    assert component["pairs"][0]["season_episode"] == 2
    assert component["pairs"][0]["matched"] is True
    assert component["comparable_rounds"] == 1
    assert component["matched_rounds"] == 1
    assert component["score"] == 1.0


def test_run_with_more_eliminations_than_the_season_does_not_raise(tmp_path) -> None:
    # The season banished one player (episode 2); the run banished three.
    # The two surplus run banishments have no counterpart to be judged
    # against, so they are excluded rather than failed.
    events = opening() + [
        ev(7, EventType.PLAYER_ELIMINATED, round=1, actor="cara", method="vote"),
        ev(8, EventType.ROUND_STARTED, round=2),
        ev(9, EventType.PLAYER_ELIMINATED, round=2, actor="dan", method="vote"),
        ev(10, EventType.ROUND_STARTED, round=3),
        ev(11, EventType.PLAYER_ELIMINATED, round=3, actor="ben", method="vote"),
        ev(12, EventType.GAME_ENDED, rounds=3, winner="faithful"),
    ]
    component = benchmark_run(events, write_season(tmp_path))[
        "banishment_alignment"
    ]
    assert len(component["pairs"]) == 3
    assert component["comparable_rounds"] == 1
    assert component["pairs"][1]["season"] is None
    assert component["pairs"][1]["matched"] is None
    assert component["match_rate"] == 0.0


def test_run_side_with_no_banishments_is_excluded_not_a_miss(tmp_path) -> None:
    # The season banished one player but the run banished nobody, so there
    # is no counterpart on the run side: the component is skipped and its
    # weight redistributed rather than scored as a miss.
    events = opening() + [ev(7, EventType.GAME_ENDED, rounds=1, winner="faithful")]
    result = benchmark_run(events, write_season(tmp_path))
    component = result["banishment_alignment"]
    assert component["pairs"][0]["run"] is None
    assert component["pairs"][0]["season"] == "ana"
    assert component["pairs"][0]["matched"] is None
    assert component["comparable_rounds"] == 0
    assert component["score"] is None
    assert result["banishment_overlap"]["score"] is None
    assert "banishment_alignment" in result["overall"]["components_skipped"]
    assert "banishment_overlap" in result["overall"]["components_skipped"]


# ----------------------------------------------------------------------
# Set overlap
# ----------------------------------------------------------------------


def test_overlap_scores_one_on_an_exact_set_match(tmp_path) -> None:
    result = benchmark_run(perfect_events(), write_season(tmp_path))
    banishment = result["banishment_overlap"]
    assert banishment["shared"] == ["ana"]
    assert banishment["run_only"] == []
    assert banishment["season_only"] == []
    assert banishment["precision"] == 1.0
    assert banishment["recall"] == 1.0
    assert banishment["score"] == 1.0
    murder = result["murder_overlap"]
    assert murder["shared"] == ["ben"]
    assert murder["run_only"] == []
    assert murder["season_only"] == []
    assert murder["score"] == 1.0


def test_overlap_scores_zero_on_disjoint_sets(tmp_path) -> None:
    events = opening() + [
        ev(7, EventType.PLAYER_ELIMINATED, round=1, actor="ben", method="vote"),
        ev(8, EventType.TRAITOR_KILL, round=1, targets=["cara"]),
        ev(9, EventType.PLAYER_ELIMINATED, round=1, actor="cara", method="night"),
        ev(10, EventType.GAME_ENDED, rounds=1, winner="faithful"),
    ]
    result = benchmark_run(events, write_season(tmp_path))
    banishment = result["banishment_overlap"]
    assert banishment["shared"] == []
    assert banishment["run_only"] == ["ben"]
    assert banishment["season_only"] == ["ana"]
    assert banishment["precision"] == 0.0
    assert banishment["recall"] == 0.0
    assert banishment["score"] == 0.0
    murder = result["murder_overlap"]
    assert murder["shared"] == []
    assert murder["run_only"] == ["cara"]
    assert murder["season_only"] == ["ben"]
    assert murder["score"] == 0.0


OVERLAP_SEASON_YAML = """\
season:
  id: overlap-season
  name: Overlap Season
  winner_faction: faithful
  final_counts:
    faithful: 1
    traitors: 0
cast:
  - {id: ana, role: faithful, exit_episode: 1}
  - {id: ben, role: faithful, exit_episode: 3}
  - {id: cara, role: faithful, exit_episode: 2}
eliminations:
  - {episode: 1, night_victim: null, banishment: ana}
  - {episode: 2, night_victim: null, banishment: cara}
recruitments: []
"""


def test_partial_overlap_reports_the_expected_f1(tmp_path) -> None:
    path = tmp_path / "overlap.yaml"
    path.write_text(OVERLAP_SEASON_YAML, encoding="utf-8")
    season = load_season(path)
    # The season banished ana then cara; the run banished ana then ben, so
    # one of two names matches on each side (precision 0.5, recall 0.5).
    events = [
        ev(1, EventType.GAME_STARTED, players=["ana", "ben", "cara"]),
        ev(2, EventType.ROLE_ASSIGNED, actor="ana", role="faithful"),
        ev(3, EventType.ROLE_ASSIGNED, actor="ben", role="faithful"),
        ev(4, EventType.ROLE_ASSIGNED, actor="cara", role="faithful"),
        ev(5, EventType.ROUND_STARTED, round=1),
        ev(6, EventType.PLAYER_ELIMINATED, round=1, actor="ana", method="vote"),
        ev(7, EventType.ROUND_STARTED, round=2),
        ev(8, EventType.PLAYER_ELIMINATED, round=2, actor="ben", method="vote"),
        ev(9, EventType.GAME_ENDED, rounds=2, winner="faithful"),
    ]
    component = benchmark_run(events, season)["banishment_overlap"]
    assert component["shared"] == ["ana"]
    assert component["run_only"] == ["ben"]
    assert component["season_only"] == ["cara"]
    assert component["precision"] == 0.5
    assert component["recall"] == 0.5
    assert component["f1"] == 0.5
    assert component["score"] == 0.5


def test_component_weights_are_the_agreed_reweighting() -> None:
    assert COMPONENT_WEIGHTS == {
        "outcome": 0.18,
        "traitor_roster": 0.08,
        "banishment_alignment": 0.10,
        "banishment_overlap": 0.10,
        "murder_alignment": 0.08,
        "murder_overlap": 0.08,
        "traitor_hit_rate": 0.08,
        "survival_curve": 0.10,
        "final_counts": 0.06,
        "finale": 0.06,
        "recruitments": 0.04,
        "exit_order": 0.04,
    }


def test_weights_cover_every_component_and_sum_to_one(tmp_path) -> None:
    result = benchmark_run(perfect_events(), write_season(tmp_path))
    assert set(result) == set(COMPONENT_WEIGHTS) | {"overall"}
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _write_run(run_dir: Path, events: list[Event]) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n",
        encoding="utf-8",
    )


def test_benchmark_cli_writes_report_and_prints_season(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "game-001"
    _write_run(run_dir, perfect_events())
    season_path = tmp_path / "season.yaml"
    season_path.write_text(SEASON_YAML, encoding="utf-8")

    code = main(
        [
            "benchmark",
            "game-001",
            "--runs-dir",
            str(runs),
            "--season",
            str(season_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert SEASON_ID in out
    assert "banishment_alignment" in out

    report = (run_dir / "benchmark.md").read_text(encoding="utf-8")
    assert "# Season benchmark: game-001" in report
    assert SEASON_ID in report
    assert SOURCE_URL in report


def test_benchmark_cli_rejects_a_missing_run(tmp_path, capsys) -> None:
    season_path = tmp_path / "season.yaml"
    season_path.write_text(SEASON_YAML, encoding="utf-8")
    code = main(
        [
            "benchmark",
            "game-999",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--season",
            str(season_path),
        ]
    )
    assert code == 1
    assert "game-999" in capsys.readouterr().err


def test_benchmark_cli_rejects_a_missing_season(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    _write_run(runs / "game-001", perfect_events())
    code = main(
        [
            "benchmark",
            "game-001",
            "--runs-dir",
            str(runs),
            "--season",
            str(tmp_path / "nope.yaml"),
        ]
    )
    assert code == 1
    assert "season file not found" in capsys.readouterr().err


def test_shield_blocked_attempt_is_not_counted_as_a_murder(tmp_path) -> None:
    """A blocked kill kills nobody, so it must not inflate the run side."""
    blocked = opening() + [
        ev(7, EventType.TRAITOR_KILL, round=1, targets=["cara"]),
        ev(8, EventType.SHIELD_BLOCKED, round=1, actor="cara", targets=["cara"]),
        ev(9, EventType.GAME_ENDED, rounds=1, winner="faithful"),
    ]
    murder = benchmark_run(blocked, write_season(tmp_path))["murder_overlap"]
    assert murder["run"] == []
    assert murder["blocked_attempts"] == ["cara"]

    # The same kill without a shield block does count.
    killed = opening() + [
        ev(7, EventType.TRAITOR_KILL, round=1, targets=["cara"]),
        ev(8, EventType.PLAYER_ELIMINATED, round=1, actor="cara", method="night"),
        ev(9, EventType.GAME_ENDED, rounds=1, winner="faithful"),
    ]
    murder = benchmark_run(killed, write_season(tmp_path))["murder_overlap"]
    assert murder["run"] == ["cara"]
    assert murder["blocked_attempts"] == []
