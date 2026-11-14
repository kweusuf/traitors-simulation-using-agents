# Code review — findings

A read of `src/` at 12,628 lines across 59 modules, with 43 test modules and
16 tools. No changes made; this is the report.

Findings are ordered by what they cost you. The first is a live product
problem, not a style one.

---

## 1. `Relationships` is written every turn and never read

`runner._bump_relationships` (`runner.py:495-526`) runs on every event and
maintains per-player trust, suspicion and threat:

```python
rel = agent.relationships.get(nominee)
agent.relationships.update(nominee, suspicion=min(1.0, rel.suspicion + 0.4))
```

Across nominations, private messages and eliminations. Then:

```
grep -rn 'relationships\|trust\|animosity' src/simulation/agents/prompts.py
(no matches)
```

**Nothing reads it.** `Relationships.prompt_lines()` exists and has zero
callers outside a unit test. The numbers are computed, stored, incremented
across every game, and never shown to the model that would use them.

This is the same defect class as `Beliefs` was before the ledger work — state
maintained at runtime that the prompt never receives — and it is the more
expensive of the two, since it runs on *every event* rather than once per
round.

**It is also the answer to a question we left open.** `docs/learnings.md`
lists "richer signals" as unexplored, but the signal was already built. The
animosity accumulation (`suspicion + 0.4` per nomination, `+ 0.2` trust per
private exchange) is exactly the voting-history signal I proposed building
last turn. It exists. It is just not connected to anything.

**Recommendation.** Either render it into the prompt, or delete it. Rendering
is worth a line or two and would give the ballot a second basis for the
decision. Deleting removes a per-event cost and ~40 lines of code that imply
a capability the game does not have.

---

## 2. `game_engine.py` is 1,432 lines with 70 methods

| Method | Lines |
| --- | --- |
| `submit_action` | 104 |
| `__init__` | 81 |
| `_record_message` | 55 |
| `start` | 47 |
| `_resolve_recruit_response` | 46 |

No single method is out of hand, but `submit_action` is a 104-line dispatch
branching over every action type — public/private/traitor messages, accuse,
rebut, vote with dagger weighting, kill, recruit, seer, end-vote, recruit
decision and response.

The dispatch is a natural `match` or a table of `(action_type, handler)`.
That would make the exhaustiveness checkable: today, adding an `ActionType`
produces no error anywhere if `submit_action` forgets it, which is how a
mechanic can be implemented in the validator, the schema and the phases yet
silently do nothing on submission.

**Recommendation.** Split the dispatch. It is the one structural change that
would pay for itself, because it converts a silent failure mode into a
testable one. Not urgent — nothing is broken today.

---

## 3. `engine/` imports `experiments/`, inverting the layering

```
game_engine.py:18   from simulation.experiments.config import GameConfig
phase_engine.py:17  from simulation.experiments.config import GameConfig
rules.py:15         from simulation.experiments.config import GameConfig
```

The intended direction is the reverse: `experiments` orchestrates `engine`.
`config.py` is effectively a leaf (it imports only `engine.state`,
`models.base`, `models.llm`), so there is no cycle today and no import error.
But `agents/runtime.py` needs a `TYPE_CHECKING` guard to import
`experiments.telemetry` — the comment reads *"avoids an agents → experiments
import at runtime"*. The inversion is already costing a workaround.

**Recommendation.** Move `GameConfig` to a neutral module
(`simulation/config.py`), or invert the dependency. Small and mechanical, and
it removes a class of future cycle. Low priority since nothing breaks.

---

## 4. `runner.py` is doing four jobs

`GameRunner` handles resume, orchestration, memory synchronisation and
metrics:

| Method | Lines |
| --- | --- |
| `_run_locked` | 144 |
| `_build_metrics` | 97 |
| `_callback` | 90 |
| `_bump_relationships` | 42 |

`_callback` is 90 lines because it embeds `_sync_memory` — the loop that
replays the event log into every observer's memory, filters writes by
`write.visible_to`, and carries writer confidence. That is a genuine
responsibility with its own invariants (the resume-replay subtlety
documented at length in its docstring) currently living inside a callback
factory.

36 imports in one module.

**Recommendation.** Extract `_sync_memory` to `memory_sync.py`. Self-contained,
---

## 5. Three parallel "render this state as prompt lines" implementations

- `Beliefs.prompt_lines()` — `"bob: suspected traitor (confidence 0.70, round 2)"`
- `Relationships.prompt_lines()` — `"bob: trust 1.00, suspicion 0.20, threat 0.00"`
- `ledger._ranked()` / `ledger_lines()` — `"- matt (0.90, held since round 1)"`

Three formats for one idea, two of them unused. `Beliefs.prompt_lines()` is
used once (`runtime.py:361`, feeding the ledger call); `Relationships`' never.

Minor in isolation, but it is why the ledger's rendering had to be written
from scratch rather than reusing either. `Beliefs.prompt_lines()` and
`ledger_lines()` read the *same* `Belief` objects and format them
differently — a divergence waiting to confuse.

---

## 6. Edit distance and word-overlap both implemented

`validator._edit_distance` (Damerau-Levenshtein, capped) and
`repetition.similarity` (Jaccard over content words) solve different problems
and I'd leave them separate. Noted only because the repetition threshold is
now the single most-argued number in the project, and it lives in a pure,
dependency-free module — which is correct and worth preserving.

---

## What is genuinely in good shape

- **Layering below `experiments` is clean.** `actions/`, `agents/`,
  `memory/`, `communication/`, `models/`, `persistence/` import only downward.
  No cycles.
- **`actions/repetition.py` and `agents/ledger.py` are well-factored.** Pure,
  no I/O, no framework imports, fully unit-testable. The two newest modules
  took no shortcuts.
- **Tests do not touch the network.** `test_ollama.py` and `test_cli.py`
  contain `http://localhost:11434` but patch `urlopen` — checked, not assumed.
- **591 tests**, with no sleeps-as-synchronisation except one deliberate
  lock-contention test.
- **Telemetry/resume ordering is correct and documented.** The invariant that
  `TelemetryRecorder` must be constructed *after* the resume point settles is
  subtle and well-handled.

---

## Suggested order

1. **Decide on `Relationships`** — render or delete. Per-event work for zero
   benefit today, and a ready-made voting signal.
2. **Extract `_sync_memory`** — lowest-risk structural win.
3. **Split `submit_action`'s dispatch** — turns a silent failure mode into a
   testable one.
4. **Relocate `GameConfig`** — mechanical, removes an existing workaround.

None are urgent. The codebase is in better shape than its size suggests; the
real finding is #1, and it is a product decision rather than a refactoring one.

---

## A correction to my own review

I first flagged `Relationships` as dead code on the strength of its import
list. That was wrong — it *is* written, in `runner._bump_relationships`. The
finding is narrower and more interesting than "dead code": it is live,
maintained, and unwired. Checked before reporting rather than trusting the
first grep.
heavily commented, independently testable — the easiest extraction here.