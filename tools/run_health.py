"""Live health of a run in progress: which round, and is it still moving.

A run writes no summary until it ends, so progress is read from the event
log and the call log. Call health matters more than message count: a stalled
run keeps growing the files it already has, so size says nothing, and a phase
that is not advancing shows up in the call log as failed calls, retries spent,
or latency that has stopped fitting the timeout. Read which phases the event
log contains, not how many lines it has. Prompt size over the rounds is
reported for the same reason - a prompt climbing toward the model's context
window degrades the reply before it ever errors.
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
rate = f"({len(failed) / len(calls):.1%} failed)" if calls else "(no calls yet)"
print(f"  ok={len(ok)}  failed={len(failed)}  {rate}")
lat = sorted(c.get("latency_ms") or 0 for c in ok)
if lat:
    q = lambda frac: lat[min(len(lat) - 1, int(len(lat) * frac))]  # noqa: E731
    print(f"  latency ms  p50={q(0.5)}  p95={q(0.95)}  max={lat[-1]}")
errs = [c.get("error") for c in calls if c.get("error")]
if errs:
    print(f"  last error: {errs[-1]}")

# The ledger's generation is a model call the engine never sees, so pooling it
# with the game's own calls makes both unreadable: the prompt sizes stop
# belonging to one phase, and a run where every ledger failed looks like a
# healthy run that simply made more calls.
LEDGER = "ledger_update"
ledger = [c for c in calls if c.get("action_type") == LEDGER]
if ledger:
    lf = [c for c in ledger if not c.get("ok")]
    shared = [c for c in calls if c.get("action_type") != LEDGER]
    print(f"\n-- ledger ({len(ledger)} calls, "
          f"{len(ledger) / len(calls):.0%} of all) --")
    print(f"  ok={len(ledger) - len(lf)}  failed={len(lf)}")
    llat = sorted(c.get("latency_ms") or 0 for c in ledger if c.get("ok"))
    if llat:
        q = lambda frac: llat[min(len(llat) - 1, int(len(llat) * frac))]  # noqa: E731
        print(f"  latency ms  p50={q(0.5)}  max={llat[-1]}")
    print(f"  players with a ledger call: "
          f"{len({c.get('agent_id') for c in ledger})}")
    print(f"  without the ledger: {len(shared)} game calls")

# Prompt sizes are reported per round above, pooled. Once the ledger is on,
# the game's own prompts are the ones that matter for context pressure, since
# the ledger's prompt is short and flat by construction.
if ledger:
    game_only = [c for c in calls if c.get("action_type") != LEDGER]
    sizes = sorted(c.get("prompt_chars") or 0 for c in game_only)
    if sizes:
        print(f"\n  game prompts only: median={sizes[len(sizes) // 2]:6d}  "
              f"max={sizes[-1]:6d}")