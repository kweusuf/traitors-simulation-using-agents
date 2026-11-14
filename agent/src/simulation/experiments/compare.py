"""Paired comparison of two runs against per-dimension gates (plan wave A, A2).

Every gate is one quality or cost dimension with its own threshold, so
a candidate cannot win on one metric while silently regressing on
another. Binary outcome fields (winner, rounds, finale, solo win) are
reported for context but never gated: they need dozens of runs to mean
anything, far more than a paired comparison gives.

Cost gates only apply when both runs reported the value, because a
missing number is absence of evidence, not a regression.
"""

from __future__ import annotations

from typing import Any, Optional

# (label, path inside metrics.json, allowed increase over the baseline)
ABSOLUTE_GATES = (
    ("hallucination_score", ("quality", "hallucination_score"), 0.02),
    ("duplication_score", ("quality", "diversity", "duplication_score"), 0.02),
    (
        "speech_similarity.content_words.mean",
        ("quality", "speech_similarity", "content_words", "mean"),
        0.05,  # higher means less distinct speech, so worse
    ),
    (
        "speech_similarity.phrasing.mean",
        ("quality", "speech_similarity", "phrasing", "mean"),
        0.05,  # higher means more shared phrasing, so worse
    ),
    ("secrecy.flags_total", ("quality", "secrecy", "flags_total"), 0.0),
    ("parsing.rejected_actions", ("quality", "parsing", "rejected_actions"), 0.0),
)

# (label, path inside metrics.json, tolerated factor over the baseline)
RELATIVE_GATES = (
    ("llm.latency_ms.p95", ("llm", "latency_ms", "p95"), 1.25),
    ("llm.total_tokens", ("llm", "total_tokens"), 1.25),
)

# Information only, printed but never gated (see the module docstring).
OUTCOME_FIELDS = ("winner", "rounds", "finale", "solo_traitor_win")

_EPSILON = 1e-9  # keeps 0.05 <= 0.0 + 0.05 from failing on float noise


def dig(metrics: dict[str, Any], *keys: str) -> Any:
    """Nested lookup that returns None instead of raising on gaps."""
    for key in keys:
        if not isinstance(metrics, dict):
            return None
        metrics = metrics.get(key)
    return metrics


def _gate(
    label: str,
    baseline_value: Any,
    candidate_value: Any,
    rule: str,
    *,
    skipped: bool = False,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """One gate record with its delta and verdict."""
    delta = None
    if isinstance(baseline_value, (int, float)) and isinstance(
        candidate_value, (int, float)
    ):
        delta = round(candidate_value - baseline_value, 6)
    return {
        "metric": label,
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta": delta,
        "limit": None,
        "rule": rule,
        "passed": skipped,  # a skipped gate cannot fail the run
        "skipped": skipped,
        "note": note,
    }


def compare_runs(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Gate `candidate` against `baseline`; returns the structured result."""
    gates: list[dict[str, Any]] = []

    for label, path, tolerance in ABSOLUTE_GATES:
        base = dig(baseline, *path)
        cand = dig(candidate, *path)
        rule = f"<= baseline + {tolerance:g}"
        if not isinstance(base, (int, float)) or not isinstance(
            cand, (int, float)
        ):
            missing = [
                side
                for side, value in (("baseline", base), ("candidate", cand))
                if not isinstance(value, (int, float))
            ]
            gates.append(
                _gate(label, base, cand, rule,
                      note=f"value missing from {' and '.join(missing)}")
            )
            continue
        gate = _gate(label, base, cand, rule)
        gate["limit"] = round(base + tolerance, 6)
        gate["passed"] = cand <= base + tolerance + _EPSILON
        gates.append(gate)

    for label, path, factor in RELATIVE_GATES:
        base = dig(baseline, *path)
        cand = dig(candidate, *path)
        rule = f"<= baseline * {factor:g}"
        if not isinstance(base, (int, float)) or not isinstance(
            cand, (int, float)
        ):
            gates.append(
                _gate(label, base, cand, rule, skipped=True,
                      note="not reported by both runs")
            )
            continue
        gate = _gate(label, base, cand, rule)
        gate["limit"] = round(base * factor, 6)
        gate["passed"] = cand <= base * factor + _EPSILON
        gates.append(gate)

    applied = [g for g in gates if not g["skipped"]]
    return {
        "gates": gates,
        "failed": sum(1 for g in applied if not g["passed"]),
        "outcome": {
            field: {
                "baseline": baseline.get(field),
                "candidate": candidate.get(field),
            }
            for field in OUTCOME_FIELDS
        },
        "passed": all(g["passed"] for g in applied),
    }
