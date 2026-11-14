"""What would the repetition gate have caught in a run that already happened?

The threshold is a judgement call, so measure it instead of guessing. This
replays the public messages of a completed run through the same comparison
the gate uses and reports the score distribution, plus what each candidate
threshold would have rejected.

Read it as: where the bulk of the mass sits is the real signal, and a
threshold that rejects the top few percent of real messages is a gate
that will fire on ordinary conversation.

Usage:
  python tools/repetition_rates.py [runs/uk-s01-ptr-mem]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.actions.repetition import similarity  # noqa: E402


def messages(run: Path) -> list[tuple[str, str]]:
    """(sender, content) for every public message, in order."""
    out = []
    for line in open(run / "events.jsonl"):
        event = json.loads(line)
        if event.get("type") != "PUBLIC_MESSAGE":
            continue
        payload = event.get("payload") or {}
        content = (payload.get("content") or "").strip()
        if content:
            out.append((event.get("actor"), content))
    return out


def main() -> None:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-ptr-mem")
    msgs = messages(run)
    if not msgs:
        raise SystemExit(f"no public messages in {run}")

    # For each message, its best match against what came before it from
    # anyone - the strictest reading, and the one that shows the ceiling of
    # what a gate could ever reject.
    scores: list[float] = []
    for i, (_, content) in enumerate(msgs):
        best = 0.0
        for _, prior in msgs[:i]:
            best = max(best, similarity(content, prior))
        scores.append(best)

    print(f"{run.name}: {len(msgs)} public messages\n")
    print("best prior-match score distribution:")
    for low, high in [(1.0, 1.01), (0.8, 1.0), (0.6, 0.8), (0.4, 0.6),
                      (0.2, 0.4), (0.0, 0.2)]:
        n = sum(1 for s in scores if low <= s < high)
        bar = "#" * round(n * 40 / max(len(scores), 1))
        label = "1.00" if high > 1 else f"{low:.2f}"
        print(f"  {label}  {n:4d}  {bar}")

    print("\nwhat each threshold would reject:")
    for t in (0.5, 0.6, 0.7, 0.8, 1.0):
        hits = [s for s in scores if s >= t]
        print(f"  >= {t:.1f}  {len(hits):4d}  ({len(hits) / len(scores):5.1%})")

    print("\nthe five closest repeats in this run:")
    for (sender, content), score in sorted(
        zip(msgs, scores), key=lambda pair: -pair[1]
    )[:5]:
        first = content.strip().replace("\n", " ")[:88]
        print(f"  {score:.2f}  {sender:8} {first}")

    exact = sum(c - 1 for c in Counter(c for _, c in msgs).values() if c > 1)
    print(f"\nbyte-identical repeats already in this run: {exact}")


if __name__ == "__main__":
    main()