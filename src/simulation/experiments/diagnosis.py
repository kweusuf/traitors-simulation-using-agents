"""Deterministic post-run diagnosis report (plan wave A, A3).

Turns one run's own numbers into a short markdown report a human can
pick a prompt edit from: the metric summary, the messages that repeat
or contradict the record, the most similar pair, and 1 to 3 hypotheses
drawn from the fixed threshold table below. No model is consulted, so
the report is reproducible from `events.jsonl` alone and never varies
between runs of the same seed.

The human approves exactly one hypothesis before the next iteration;
the table exists so that choice is a lookup, not a fresh opinion.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from simulation.experiments.quality import analyse
from simulation.persistence.event_log import Event, EventType

_EXCERPT = 160  # characters shown per message or sample
_MAX_REPEATED = 3
_MAX_SAMPLES = 5  # per section; the full lists live in metrics.json
_MAX_HYPOTHESES = 3


def _dig(node: Any, *keys: str) -> Any:
    """Nested lookup that returns None instead of raising on gaps."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _num(value: Any) -> float:
    """Numbers compare; anything absent scores 0 and fires no rule."""
    return value if isinstance(value, (int, float)) else 0.0


def _excerpt(text: str) -> str:
    clean = " ".join(text.split())
    return clean[:_EXCERPT]


# Fixed hypothesis mapping (wave A, A3): trigger, suggested edit, and
# the test over the quality block. Deterministic by design: the report
# offers at most the first three triggers that fire, in this order.
HYPOTHESIS_RULES: tuple[tuple[str, str, Callable[[dict[str, Any]], bool]], ...] = (
    (
        "duplication_score > 0.15",
        "tighten originality constraints or shrink the transcript window in prompts",
        lambda q: _num(_dig(q, "diversity", "duplication_score")) > 0.15,
    ),
    (
        "speech_similarity.phrasing.mean > 0.9",
        "shared boilerplate: vary the phrasing instruction or the persona voice",
        lambda q: _num(_dig(q, "speech_similarity", "phrasing", "mean")) > 0.9,
    ),
    (
        "hallucination_score > 0",
        "grounding: restate eliminations, living players and the current round in prompts",
        lambda q: _num(q.get("hallucination_score")) > 0,
    ),
    (
        "secrecy.flags_total > 0",
        "the secrecy rule needs rewording: cover the phrasing the scorer flagged",
        lambda q: _num(_dig(q, "secrecy", "flags_total")) > 0,
    ),
    (
        "parsing.rejected_actions > 0",
        "output format hints: show one valid example action per action type",
        lambda q: _num(_dig(q, "parsing", "rejected_actions")) > 0,
    ),
)


def _summary(game_id: str, metrics: dict[str, Any],
             quality: dict[str, Any]) -> list[str]:
    llm = metrics.get("llm") or {}
    lines = ["## Metric summary", ""]
    if metrics:
        lines.append(
            f"- game: {game_id}, experiment: {metrics.get('experiment_id')}, "
            f"seed: {metrics.get('random_seed')}, model: {metrics.get('model')}"
        )
        lines.append(
            f"- outcome: winner={metrics.get('winner')} "
            f"rounds={metrics.get('rounds')} finale={metrics.get('finale')} "
            f"solo_traitor_win={metrics.get('solo_traitor_win')}"
        )
    else:
        lines.append(f"- game: {game_id} (no metrics.json; run unfinished)")
    lines.append(
        f"- hallucination_score: {quality.get('hallucination_score')} over "
        f"{quality.get('messages_checked')} messages checked"
    )
    lines.append(
        f"- duplication_score: {_dig(quality, 'diversity', 'duplication_score')}, "
        f"duplicate_rate: {_dig(quality, 'diversity', 'duplicate_rate')}"
    )
    lines.append(
        f"- speech similarity: content_words mean "
        f"{_dig(quality, 'speech_similarity', 'content_words', 'mean')}, "
        f"phrasing mean {_dig(quality, 'speech_similarity', 'phrasing', 'mean')}"
    )
    lines.append(f"- secrecy flags_total: {_dig(quality, 'secrecy', 'flags_total')}")
    lines.append(
        f"- rejected actions: {_dig(quality, 'parsing', 'rejected_actions')}"
    )
    if llm:
        lines.append(
            f"- llm: calls={llm.get('calls')} total_tokens={llm.get('total_tokens')} "
            f"latency p95={_dig(llm, 'latency_ms', 'p95')}ms"
        )
    return lines + [""]


def _repeated(events: list[Event]) -> list[str]:
    """Top repeated message texts across all authors, with senders."""
    texts: dict[str, list[str]] = {}
    for event in events:
        if event.type not in (EventType.PUBLIC_MESSAGE, EventType.PRIVATE_MESSAGE):
            continue
        content = " ".join(str(event.payload.get("content", "")).split())
        if content:
            texts.setdefault(content, []).append(event.actor or "?")
    ranked = sorted(
        ((text, senders) for text, senders in texts.items() if len(senders) >= 2),
        key=lambda item: (-len(item[1]), item[0]),
    )
    lines = ["## Most repeated messages", ""]
    if not ranked:
        lines.append("- none: every message text is distinct")
    for text, senders in ranked[:_MAX_REPEATED]:
        senders_line = ", ".join(dict.fromkeys(senders))
        lines.append(f"- {len(senders)} messages ({senders_line}): \"{_excerpt(text)}\"")
    return lines + [""]


def _samples(title: str, samples: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not samples:
        lines.append("- none")
    for sample in samples[:_MAX_SAMPLES]:
        lines.append(
            f"- {sample.get('kind')}, round {sample.get('round')}, "
            f"{sample.get('sender')}: {sample.get('detail')}"
        )
        lines.append(f"  > {sample.get('excerpt')}")
    if len(samples) > _MAX_SAMPLES:
        lines.append(f"- ... {len(samples) - _MAX_SAMPLES} more in metrics.json")
    return lines + [""]


def _similar_pair(similarity: dict[str, Any]) -> list[str]:
    lines = ["## Most similar player pair", ""]
    content = similarity.get("content_words") or {}
    phrasing = similarity.get("phrasing") or {}
    pair = content.get("max_pair")
    if not pair:
        lines.append("- not enough speaking players to compare")
    else:
        phrasing_pair = phrasing.get("max_pair")
        phrasing_text = (
            f"{phrasing_pair[0]} ~ {phrasing_pair[1]}"
            if phrasing_pair
            else "n/a"
        )
        lines.append(
            f"- {pair[0]} ~ {pair[1]}: content {content.get('max')}; "
            f"phrasing {phrasing_text} at {phrasing.get('max')}"
        )
    return lines + [""]


def _hypotheses(quality: dict[str, Any]) -> list[str]:
    lines = ["## Hypotheses", ""]
    fired = [
        f"{trigger}: {suggestion}"
        for trigger, suggestion, test in HYPOTHESIS_RULES
        if test(quality)
    ][:_MAX_HYPOTHESES]
    if not fired:
        lines.append("- none: no threshold fired, keep the current prompts")
    for index, text in enumerate(fired, start=1):
        lines.append(f"{index}. {text}")
    return lines + [""]


def build_diagnosis(
    game_id: str, events: list[Event], metrics: dict[str, Any]
) -> str:
    """The full `diagnosis.md` text for one run; deterministic."""
    quality = analyse(events)
    sections = [
        f"# Diagnosis: {game_id}",
        "",
        *_summary(game_id, metrics, quality),
        *_repeated(events),
        *_samples("Secrecy samples", quality["secrecy"]["samples"]),
        *_samples("Hallucination samples", quality["hallucination"]["samples"]),
        *_similar_pair(quality["speech_similarity"]),
        *_hypotheses(quality),
    ]
    return "\n".join(sections).rstrip() + "\n"
