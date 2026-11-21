"""Win-rate reporting over completed runs, with Wilson intervals.

Reads each run's ``metrics.json`` for the winner and its saved ``config.yaml``
snapshot for the levers worth segmenting on (pinned cast, suspicion ledger,
traitor mandate, finale, model). Used by ``tools/win_rate_report.py`` and the
traitor-bias experiments.

Only completed runs with a traitor/faithful winner are counted; a run that
crashed or was truncated is ignored so it cannot skew the rate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import yaml


@dataclass(frozen=True)
class RunOutcome:
    """One completed game and the levers its config snapshot recorded."""

    run_id: str
    winner: str  # "traitor" or "faithful"
    pinned: bool = False
    ledger: bool = False
    mandate: bool = True
    finale: bool = False
    model: Optional[str] = None


@dataclass(frozen=True)
class Rate:
    """A win count with its point estimate and Wilson 95% interval."""

    successes: int
    total: int
    rate: float
    low: float
    high: float

    def pct(self) -> str:
        return f"{100 * self.rate:.0f}% ({100 * self.low:.0f}-{100 * self.high:.0f}%)"


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate(successes: int, total: int) -> Rate:
    low, high = wilson_interval(successes, total)
    return Rate(successes, total, successes / total if total else 0.0, low, high)


def _load_config(run_dir: Path) -> tuple[dict, dict]:
    path = run_dir / "config.yaml"
    if not path.exists():
        return {}, {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    game = data.get("game") if isinstance(data.get("game"), dict) else {}
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    return game, llm


def _outcome(run_dir: Path) -> Optional[RunOutcome]:
    metrics_path = run_dir / "metrics.json"
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    winner = metrics.get("winner")
    if metrics.get("status") != "completed" or winner not in {"traitor", "faithful"}:
        return None
    game, llm = _load_config(run_dir)
    return RunOutcome(
        run_id=run_dir.name,
        winner=winner,
        pinned=bool(game.get("traitor_names")),
        ledger=bool(game.get("suspicion_ledger")),
        mandate=game.get("traitor_mandate") is not False,
        finale=bool(game.get("finale_total") or game.get("finale_traitors")),
        model=llm.get("model"),
    )


def load_outcomes(
    runs_dir: Path, include: Sequence[str] = ()
) -> list[RunOutcome]:
    """Completed-game outcomes under ``runs_dir``.

    ``include`` keeps only run ids containing one of the given substrings,
    which is how an experiment arm (e.g. ``bias-``) is isolated.
    """
    outcomes: list[RunOutcome] = []
    if not runs_dir.exists():
        return outcomes
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if include and not any(token in run_dir.name for token in include):
            continue
        outcome = _outcome(run_dir)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def summarise(outcomes: Sequence[RunOutcome]) -> Rate:
    """Traitor win rate across ``outcomes`` (success = traitor win)."""
    return rate(sum(1 for o in outcomes if o.winner == "traitor"), len(outcomes))


SEGMENTS: dict[str, Callable[[RunOutcome], str]] = {
    "pinned": lambda o: "pinned" if o.pinned else "random",
    "ledger": lambda o: "ledger-on" if o.ledger else "ledger-off",
    "mandate": lambda o: "mandate-on" if o.mandate else "mandate-off",
    "finale": lambda o: "finale" if o.finale else "no-finale",
    "model": lambda o: o.model or "unknown",
}


def group(
    outcomes: Sequence[RunOutcome], segment: str
) -> dict[str, list[RunOutcome]]:
    key = SEGMENTS[segment]
    grouped: dict[str, list[RunOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(key(outcome), []).append(outcome)
    return dict(sorted(grouped.items()))


def format_report(
    outcomes: Sequence[RunOutcome], by: Sequence[str] = ()
) -> str:
    lines = [
        f"completed runs: {len(outcomes)}",
        f"traitor win rate: {summarise(outcomes).pct()}",
    ]
    for segment in by:
        lines.append(f"\nby {segment}:")
        for label, rows in group(outcomes, segment).items():
            lines.append(f"  {label:12} {summarise(rows).pct()}  (n={len(rows)})")
    return "\n".join(lines)
