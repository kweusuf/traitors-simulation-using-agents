"""Live health of a run in progress: which round, and is it still moving.

A run writes no summary until it ends, so progress is read from the event
log and the call log. Call health matters more than message count: past what
a host serves in parallel, requests queue into the timeout rather than
failing loudly, so a stalled run keeps growing its files slowly and looks
alive. Prompt size over the rounds is reported for the same reason - a
prompt climbing toward the model's context window degrades the reply before
it ever errors.
"""
import collections
import json
import sys
from pathlib import Path

run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-en5")
events = [json.loads(l) for l in open(run / "events.jsonl")]
calls = [json.loads(l) for l in open(run / "llm_calls.jsonl")]

rounds = [e.get("round") for e in events if e.get("round") is not None]
print(f"{run.name}: round {max(rounds)}, {len(events)} events, "
      f"{len(calls)} llm calls")

print("\n-- prompt_chars by round --")
by_round = collections.defaultdict(list)
for c in calls:
    by_round[c.get("round")].append(c.get("prompt_chars") or 0)
for r in sorted(by_round, key=lambda x: (x is None, x)):
    sizes = sorted(by_round[r])
    print(f"  r{r:<3} n={len(sizes):4d}  min={sizes[0]:6d}  "
          f"median={sizes[len(sizes) // 2]:6d}  max={sizes[-1]:6d}")

print("\n-- call health --")
ok = [c for c in calls if c.get("ok")]
failed = [c for c in calls if not c.get("ok")]
print(f"  ok={len(ok)}  failed={len(failed)}  "
      f"({len(failed) / len(calls):.1%} failed)")
lat = sorted(c.get("latency_ms") or 0 for c in ok)
if lat:
    q = lambda frac: lat[min(len(lat) - 1, int(len(lat) * frac))]  # noqa: E731
    print(f"  latency ms  p50={q(0.5)}  p95={q(0.95)}  max={lat[-1]}")
errs = [c.get("error") for c in calls if c.get("error")]
if errs:
    print(f"  last error: {errs[-1]}")