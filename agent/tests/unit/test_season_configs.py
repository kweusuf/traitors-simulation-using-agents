"""Season replay configs must agree with the ground truth they replay.

The nine non-UK season configs and their personas were added wholesale, and
nothing loaded them, so the deal a replay opened with could drift from the
season it claimed to reproduce with no test noticing. Two drifts this pins:

- `game.traitors` is the number of traitors the season *opened* with. A
  recruited player belongs to `max_recruits`/the recruitment log, not to the
  starting deal, so pinning one in `traitor_names` opens the replay with a
  traitor too many.
- `season.starting_traitor_count` is documentation, so it silently disagreed
  with the cast's own `traitor_type: original` entries. It has to equal them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulation.experiments.config import load_config

REPO = Path(__file__).resolve().parents[2]

# Config -> ground truth. Keyed explicitly because the UK config's
# `game.name` (`season_uk_s01`) is not its ground-truth id.
REPLAYS = {
    "configs/traitors/season_uk_s01.yaml": "configs/seasons/the-traitors-uk-s01.yaml",
    "configs/traitors/season_us_s01.yaml": "configs/seasons/the-traitors-us-s01.yaml",
    "configs/traitors/season_us_s02.yaml": "configs/seasons/the-traitors-us-s02.yaml",
    "configs/traitors/season_us_s03.yaml": "configs/seasons/the-traitors-us-s03.yaml",
    "configs/traitors/season_us_s04.yaml": "configs/seasons/the-traitors-us-s04.yaml",
    "configs/traitors/season_india_s01.yaml": "configs/seasons/the-traitors-india-s01.yaml",
    "configs/traitors/season_nz_s01.yaml": "configs/seasons/the-traitors-nz-s01.yaml",
    "configs/traitors/season_nz_s02.yaml": "configs/seasons/the-traitors-nz-s02.yaml",
    "configs/traitors/season_australia_s01.yaml": "configs/seasons/the-traitors-australia-s01.yaml",
    "configs/traitors/season_australia_s02.yaml": "configs/seasons/the-traitors-australia-s02.yaml",
}


def _cast_by_type(truth: dict, traitor_type: str) -> set[str]:
    return {
        member["id"]
        for member in truth["cast"]
        if member.get("traitor_type") == traitor_type
    }


@pytest.mark.parametrize(("config_path", "season_path"), sorted(REPLAYS.items()))
def test_season_replay_matches_its_ground_truth(
    config_path: str, season_path: str
) -> None:
    config = load_config(REPO / config_path)
    game = config.game
    truth = yaml.safe_load((REPO / season_path).read_text(encoding="utf-8"))

    originals = _cast_by_type(truth, "original")
    recruited = _cast_by_type(truth, "recruited")

    # The deal the replay opens with is exactly the season's starting traitors:
    # no recruit pinned as a starter, and nobody else pinned as one.
    assert game.traitors == truth["season"]["starting_traitor_count"]
    assert len(game.traitor_names) == game.traitors
    assert set(game.traitor_names) == originals
    assert not (set(game.traitor_names) & recruited)

    # The roster the personas resolve against is the cast the truth records.
    assert game.player_names is not None
    assert len(game.player_names) == game.players
    assert set(game.traitor_names) <= set(game.player_names)
    assert set(game.player_names) == {member["id"] for member in truth["cast"]}

    assert game.personas is not None
    assert len(game.personas) == game.players
    for persona in game.personas:
        assert (REPO / "configs" / "personas" / f"{persona}.yaml").is_file(), persona


def test_every_ground_truth_starts_with_the_originals_it_lists() -> None:
    """`starting_traitor_count` is the count of `traitor_type: original`."""
    seasons = sorted((REPO / "configs" / "seasons").glob("*.yaml"))
    assert seasons, "no ground-truth files found"
    for season_path in seasons:
        truth = yaml.safe_load(season_path.read_text(encoding="utf-8"))
        originals = _cast_by_type(truth, "original")
        assert truth["season"]["starting_traitor_count"] == len(originals), (
            f"{season_path.name}: starting_traitor_count="
            f"{truth['season']['starting_traitor_count']} but the cast lists "
            f"{len(originals)} originals"
        )
