"""Compare the pointer arms against the base on the numbers that matter.

One table, run folders as arguments. Three things are reported per arm, and
they are reported together on purpose:

- the repeat rate, which is the problem being attacked;
- prompt size and latency, which is what the pointers were supposed to buy;
- the co-generation rate, because an arm with no pointers in it did not
  test the model. Without that column, a run where the field was ignored
  every time scores the same as one where it worked, and the difference
  between "the pointer did not help" and "the pointer never arrived" is the
  whole question.

Usage:
  python tools/compare_arms.py runs/uk-s01-en5 runs/uk-s01-ptr-opt ...
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path


def load(run: Path) -> dict:
    events = [json.loads(l) for l in open(run / "events.jsonl")]
    calls_path = run / "llm_calls.jsonl"
    calls = ([json.loads(l) for l in open(calls_path)]
             if calls_path.exists() else [])
    return {"events": events, "calls": calls, "name": run.name}


def repeats(events: list[dict]) -> tuple[int, int, float]:
    """Public repeats only. Pooling in private messages is what produced a
    headline ~1.6x too high on this experiment earlier."""
    public = [e for e in events if e.get("type") == "PUBLIC_MESSAGE"]
    texts = [(e.get("payload") or {}).get("content") for e in public]
    counts = collections.Counter(texts)
    repeat = sum(c - 1 for c in counts.values() if c > 1)
    return len(public), repeat, repeat / len(public) if public else 0.0


def shared_text(events: list[dict]) -> int:
    """Worst case: one line said by how many different players."""
    public = [e for e in events if e.get("type") == "PUBLIC_MESSAGE"]
    by_text: dict[str, set] = collections.defaultdict(set)
    for e in public:
        by_text[(e.get("payload") or {}).get("content")].add(e.get("actor"))
    return max((len(v) for v in by_text.values()), default=0)


def gist_rate(events: list[dict]) -> tuple[int, int]:
    messages = [e for e in events
                if e.get("type") in ("PUBLIC_MESSAGE", "PRIVATE_MESSAGE")]
    have = sum(1 for e in messages if (e.get("payload") or {}).get("gist"))
    return have, len(messages)


def row(data: dict) -> str:
    events, calls = data["events"], data["calls"]
    total, repeat, rate = repeats(events)
    sizes = sorted(c.get("prompt_chars") or 0 for c in calls)
    lat = sorted(c.get("latency_ms") or 0 for c in calls if c.get("ok"))
    failed = sum(1 for c in calls if not c.get("ok"))
    have, msgs = gist_rate(events)
    pointer = f"{have / msgs:.0%}" if msgs else "-"
    return (
        f"{data['name']:<22}"
        f"{total:>7}{repeat:>8}{rate:>9.0%}"
        f"{shared_text(events):>8}"
        f"{(sizes[len(sizes) // 2] if sizes else 0):>10}"
        f"{(sizes[-1] if sizes else 0):>9}"
        f"{(int(statistics.median(lat)) if lat else 0) // 1000:>8}"
        f"{(f'{failed / len(calls):.0%}' if calls else '-'):>9}"
        f"{pointer:>8}"
    )


HEADER = (
    f"{'run':<22}{'msgs':>7}{'repeat':>8}{'rate':>9}{'shared':>8}"
    f"{'p50':>10}{'max':>9}{'p50s':>8}{'fail':>9}{'gist':>8}"
)


def main() -> None:
    runs = [load(Path(a)) for a in sys.argv[1:]]
    if not runs:
        raise SystemExit(__doc__)
    print(HEADER)
    print("-" * len(HEADER))
    for data in runs:
        print(row(data))
    print("\nrepeat = public messages repeating an earlier one, verbatim")
    print("shared = most players who emitted one identical line")
    print("gist   = share of messages where the pointer actually arrived")
    print("an arm with a low gist column is measuring the fallback, not the model")


if __name__ == "__main__":
    main()