"""Append-only score ledger shared across runs (plan wave A, A1).

One JSON line per finished run in `<runs_dir>/ledger.jsonl`, so prompt
versions, configs and seeds can be compared across history without
re-reading every run directory. The file spans runs, which is why it
is appended here rather than listed in the per-run `ARTIFACTS` tuple.

Values a provider never reported (the fake provider reports no tokens)
are written as null: the ledger records what happened and never
fabricates a zero to look complete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEDGER_FILENAME = "ledger.jsonl"


def is_holdout_seed(seed: int) -> bool:
    """True for the 30% of seeds reserved for final validation.

    Holdout seeds are excluded from candidate evaluation so a change
    tuned against them is already overfit; every ledger line carries
    the flag and `simulation compare` warns on it.
    """
    return seed % 10 >= 7


def _dig(node: Any, *keys: str) -> Any:
    """Nested lookup that returns None instead of raising on gaps."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def ledger_entry(metrics: dict[str, Any]) -> dict[str, Any]:
    """One ledger line built from a run's `metrics.json` payload."""
    llm = metrics.get("llm") or {}
    quality = metrics.get("quality") or {}
    seed = metrics.get("random_seed")
    reported = bool(_dig(llm, "token_reported_calls"))
    return {
        "game_id": metrics.get("game_id"),
        "experiment_id": metrics.get("experiment_id"),
        "seed": seed if isinstance(seed, int) else None,
        "holdout": is_holdout_seed(seed) if isinstance(seed, int) else None,
        "prompt_version": metrics.get("prompt_version"),
        "model": metrics.get("model"),
        "winner": metrics.get("winner"),
        "winning_team": metrics.get("winning_team"),
        "finale": metrics.get("finale"),
        "solo_traitor_win": metrics.get("solo_traitor_win"),
        "rounds": metrics.get("rounds"),
        "calls": llm.get("calls"),
        # Sums over calls that reported tokens only; with zero coverage
        # they would be invented zeros, so they stay null.
        "input_tokens": llm.get("input_tokens") if reported else None,
        "output_tokens": llm.get("output_tokens") if reported else None,
        "latency_ms_p50": _dig(llm, "latency_ms", "p50"),
        "latency_ms_p95": _dig(llm, "latency_ms", "p95"),
        "rejected_actions": metrics.get("rejected_actions"),
        "quality": {
            "hallucination_score": quality.get("hallucination_score"),
            "duplication_score": _dig(quality, "diversity", "duplication_score"),
            "duplicate_rate": _dig(quality, "diversity", "duplicate_rate"),
            "speech_similarity": {
                "content_words_mean": _dig(quality, "speech_similarity",
                                          "content_words", "mean"),
                "phrasing_mean": _dig(quality, "speech_similarity", "phrasing",
                                      "mean"),
            },
            "secrecy_flags_total": _dig(quality, "secrecy", "flags_total"),
            "messages_checked": quality.get("messages_checked"),
        },
    }


def append_run(runs_dir: str | Path, metrics: dict[str, Any]) -> Path:
    """Append one line for a finished run; returns the ledger path."""
    path = Path(runs_dir) / LEDGER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger_entry(metrics), sort_keys=True) + "\n")
    return path
