# Learnings

Findings, not decisions. Each is something measured or observed about how
this codebase and these models behave, with the evidence attached so it can
be re-checked rather than trusted.

Decisions and their reasoning live in [decisions.md](decisions.md). Code
flow lives in [flow.md](flow.md).

---

## Measurement

### Word overlap cannot separate copying from discussion in this game

Replaying `uk-s01-ptr-mem` (509 public messages), scoring each against
everything said before it:

| Threshold | Would reject |
| --- | --- |
| 0.6 | 48.1% |
| 0.8 | 34.0% |
| 1.0 (exact) | 17.5% |

Nearly half of a real run's messages overlap something said earlier. That
is not copying - it is players legitimately returning to the same few
claims about the same few players. **Any paraphrase threshold discards
half a real game.**

Reproduce: `python tools/repetition_rates.py runs/<run>`

### The template collapse is cross-player, not self-repetition

Measured on `uk-s01-rep` (gate on, `scope: self`) against `uk-s01-ptr-mem`:

| | `ptr_mem` | `uk-s01-rep` |
| --- | --- | --- |
| Public messages | 509 | 247 |
| Self-repeats (same player twice) | 32 | **0** |
| Cross-player duplicates | 50 | 15 |

Self-repetition went to zero - the gate does exactly what `scope: self`
asks. The surviving duplicates are *different* players emitting identical
text:

```
n=5  meryl, amos, andrea, maddy, rayan  "Theo, your silence since..."
n=4  aaron, amos, andrea, hannah        "Alex, your defense of Ivan's..."
```

So the gate was pointed at the smaller half of the problem. `scope: room`
is the untested setting and is where the remaining 6.5% lives.

### A single swapped word is invisible to word-overlap scoring

"Iris is lying" and "Iris is truthful" score **identically** (0.667) - one
token differs, in either direction, so the union grows the same amount. A
player changing their mind looks exactly like a restatement. Pinned in
`tests/unit/test_repetition.py::test_word_overlap_cannot_see_a_single_word_flip`.

### Pooling public and private messages inflated repetition by ~1.6x

An early write-up reported 38% of messages repeating. Split by channel:
public 23%, private 11%. A private DM echoing text it received is not the
room speaking in one voice. Every rate since is per-channel.

---

## Models and hosts

### Prompt size is the context problem, and it is not the instructions

`tools/prompt_composition.py` differences real builder output:

| Section | Chars | Share |
| --- | --- | --- |
| system prompt (fixed) | 1,180 | 2% |
| public transcript | 33,744 | 49% |
| private threads | 30,543 | 44% |
| instructions + memory | 2,782 | 4% |

The fixed parts are under 3%. Re-prompting for brevity cannot help; the
transcript window is the lever.

### Weights-on-disk is not resident memory

`hauhau-qwen-27b` reports 17.5 GB on disk (Q4_K_M) but the server held
**26.6 GB** resident, `size_vram` 15.3 GB, `quantization_level: unknown`
against `Q4_K_M` on the tag. It timed out at 300s on a **3,721-character**
prompt - the shortest the game produces - through all 6 retries, in round
1. Never emitted a token. Reasoning about "does it fit in 24 GB" from the
file size on disk was wrong; the resident footprint is the number that
matters, and it is only visible from `/api/ps` while loaded.

### The host's context size is server state, not config

A run died at 4,229 tokens against `n_ctx: 4096`, while the same host and
model had served `ptr_mem` prompts of 43,039 characters (~10k tokens)
without complaint. The model reports `context_length: 262144`; Ollama was
serving it at 4096. **Verify `/api/ps` before a long run** - the setting
lives in the server process, not the config.

### A request that announces `Connection: close` was dropped, at any load

The provider used to POST through `urllib.request.urlopen`, which sends
`Connection: close` on every request and cannot be told otherwise. Against
one host, that header alone was the difference between most calls failing and
none: the same requests over a kept-alive connection were not dropped at all,
at the same concurrency, in the same minute, and one kept-alive connection
carried a whole run of requests without a loss. Each drop arrived as
`RemoteDisconnected` - a *retryable* error, so it spent the retry budget and
then ended the run.

**This does not make the earlier host numbers wrong; it makes them unreadable
as capacity.** Every in-flight level recorded here - one clean, one degraded,
one that wedged the host - was measured through that client, so a loss that
was really per-request was attributed to how many requests were in flight.
Re-measure before trusting any of it. The provider now holds its connection
(`models/ollama.py`), and `tools/probe_model.py` sends its prompt down that
same transport, because a probe that opens its own connection measures the
probe.

### A retryable transport failure that exhausts the retries still ends the run

From the outside these look like blips: the provider retries a dropped
connection with backoff, the call log records an `attempt: 1` failure, and the
run carries on. Past the retry budget the `OllamaError` propagates through the
decide loop, the phase, the runner and the CLI, and the game ends where it
stands. Only a *parse* failure is absorbed as a skipped action; nothing in the
stack turns a dead socket into one.

That makes supervision part of running a long sweep rather than an optional
extra: every arm sharing one host is one host-wide failure away from ending
together. `tools/run_rand_sweep.py` resumes a crashed arm from its own log, so
a transport failure costs the phase it interrupted instead of the night. Count
the restarts per arm (`grep -c attempt <arm>.log`) and keep that next to the
arm's numbers: two arms interrupted a different number of times were not asked
quite the same question.

Resume assumes the failure was transient, and that assumption can fail
silently. An arm that dies in the same phase on every attempt resumes into
that phase, dies there again, and repeats until the attempt cap. One arm
reached dozens of resumes while still being in round 1 at its first discussion: each
resume replayed the round's leading phases into the log, so the file grew and
looked busy while the phase counter never moved. Read which *phases* the log
contains rather than how many lines it has - an arm resuming at a fixed point
is a bug report, not a run in flight.

---

## Prompting and schema

### A worked example decides closed-choice actions

The end-vote example held `"content": "end"` and every season run ended its
finale with all five players answering `end`. **A unanimous end stops the
game**, so a surviving traitor takes the prize. That was the mechanism
behind a run of traitor wins.

Measured on the real final five: example `end` gave `end` 6/6, example
`banish` gave `banish` 4/4. The fix was pinning `content` to an enum in
the schema and showing `?` in the example.

### Redeclaring a field as required does nothing if the schema hook rewrites `required`

`Action.__get_pydantic_json_schema__` replaces the `required` list
wholesale, so `gist_required` and `co_generate_gist` arms were silently
identical - the provider was handed an optional field. Found by diffing the
arms, not by reading them. Now covered by a test.

### A structured-output schema requires `target` on every response

A public message arrives carrying a copied player name or a `'none'`
placeholder. Neither means anything for an action without a target, and
both fail the legal-target check. Cleared before validation.

### A capitalised token before a comma is not always a player

The phantom detector fires on "However, I think..." - reporting the
adverb `However` as a non-existent player and burning a retry. Observed
live in `uk-s01-rep`. The rule (capitalised, in address position,
matching no player) is right in principle and wrong on sentence-initial
ordinary words.

### Detecting a phantom needs a position rule, not just capitalisation

"Iris, you have been quiet" and "Watching the watchers is exhausting" share
a shape. Position alone is not evidence either - "Round shows us
something" is ordinary. What separates them is the comma: the suspect is a
token *immediately before* one. First-word-of-sentence was tried and
rejected - it flagged every ordinary capital.

---

## What we got wrong, and withdrew

Recorded because a withdrawn claim in the history is still a claim someone
will read.

- **"Pointer memory flattened the prompt curve."** It did not.
  `transcript_messages_per_prompt: 40` is the cap, and pointer memory never
  touched the window.
- **"The pointer arm ran four times faster with no failures."** Drawn from
  51 calls in round 1. Over the full 1,007 calls it matched the baseline.
- **"38% of messages repeat."** Wrong denominator, corrected above.
- **"A 27B model will fit in 24 GB."** Arithmetic on the wrong number, and
  never verified before recommending a run on it.
- **"A paraphrase gate will catch the collapse."** It would reject half the
  messages instead.

---

See the "Still open" section of docs/learnings.md for the arm that was
paused and where it stopped.

## Still open

- Whether a paraphrased collapse ever occurs, which is the only condition
  under which lowering `repetition_threshold` is right.
- Latency cost of rejections: p50 rose 24.9s to 70.5s in `uk-s01-rep`, part
  of which is 21 extra generations on already-large prompts.

## `uk-s01-ledger` final: traitor win, round 12

Full-season result of the ledger + standing arm, killed mid-run
by a host reboot at round 7 and finished as a fresh `uk-s01-ledger2`
run, not a resume:

| | `ptr_mem` | `uk-s01-rep` | `uk-s01-ledger2` |
| --- | --- | --- | --- |
| Self-repeats | 32 | 0 | **0** |
| Duplication | 7.2% | 5.2% | **0.4%** (msgs 137, dups 0, self-dups 0) |
| Outcome | traitor, r11 | traitor, r12 | **traitor, r12** |

**Near-zero duplication** - but the ledger run's transcript window means
the 0.4% figure covers a partial season view, not a full replay like the
other two columns. Read it as "collapse solved" only in the sense that
no player is copying anymore; do not compare the percentages directly.

Caught 2 of 3 traitors (amanda r2, alyssa r5) from the equivalent of
~17 eliminations - i.e. **at chance, like both baselines**. The early
signal (2 of 7, against 0.95 expected) regressed to the mean as the game
went on. Private reasoning improved - messages engage named claims with
demands for specifics - without the ballot getting smarter.

**Traitor won again.** All three arms end the same way, so no text-quality
intervention has moved outcomes.

### The confound to keep in mind

This arm moves **two** variables against `ptr_mem`: the ledger *and* the
newly-wired relationship standing. A ledger-only arm is the clean
follow-up if the difference matters - but the outcome column says it
does not: reasoning quality moved, detection did not.

## `uk-s01-ledger-room` final: traitor (solo) win, round 13

The `ledger_room` arm combines the ledger with `reject_repetition` at
`repetition_scope: room` (`season_uk_s01.ledger_room.yaml`, local twin for
the endpoint). `scope: self` zeroes self-repeats but leaves cross-player
copying untouched; the ledger was meant to attack vacuous agreement from
the generation side, the gate from the rejection side. The arm completed a
full season, and two things went the wrong way at once.

| | `ptr_mem` | `uk-s01-rep` | `uk-s01-ledger2` | `ledger_room` |
| --- | --- | --- | --- | --- |
| Self-repeats | 32 | 0 | 0 | **50 (9.1%)** |
| Duplication | 7.2% | 5.2% | 0.4% | 2.5% (cross-author, pooled) |
| Outcome | traitor, r11 | traitor, r12 | traitor, r12 | **traitor, r13 (solo)** |

**Self-repeats came back.** `scope: room` builds its comparison history
from everyone *except* the speaker (`runtime.py:432-443`), so it dropped
the `scope: self` check instead of adding to it: 50 byte-identical
self-repeats in 552 public messages, worse than the 32 the un-gated
`ptr_mem` arm had and the opposite of the 0 the `self`-scope arm managed.
Cross-author duplication did stay low (4 texts, 6 copies), so the gate
worked on the half it was aimed at. The defect is fixed in
`_repetition_reason` (see [todo.md](todo.md)); this run predates the fix,
so its 50 self-repeats are the size of the hole `scope: room` left open. The
default then followed the measurement - `repetition_scope: room`, which nests
`self` - so a new arm gets both checks unless it asks for `self` explicitly.

**An invented object took over the room.** aaron, the first speaker of
round 1 (sequence 41), attributed a "ledger" to Maddy before she had
spoken; **169 of 552 public messages (30.6%)** end up arguing about it,
and the `hallucination_score` is 0.0 because that check compares text only
against the *record* (eliminations, rounds, roles), not against invented
referents. The prompt never names the mechanism, so this was generated once
and then copied by everyone - an object-shaped echo of the invented-player
collapse the phantom gate exists for.

The detector is no longer blind to the *entry*. What put the ledger in the
room is checkable against the record - a claim attributed to a player who had
not spoken - and that is now `fabricated_attribution`. Every arm in the
archive that shows the pattern shows it in round 1, 0-7 times per run; this
run registers three, the seed among them. What the room does with the object
*after* that is still unmeasured, because 169 messages about a thing that does
not exist are not contradictions of the record. See [todo.md](todo.md).

Detection did not move. The room banished two of the three original
traitors (alyssa r6, amanda r7) and the r6 recruit fay (r11), but original
traitor wilf survived to the endgame and won solo - the fourth arm in a row
ending in a traitor win, and the first where no text-quality change moved
the outcome.

For comparison with the earlier arms, the cost: 1,305 calls, 13.3% failed
(174), p50 76.8s, p95 126.4s against a 300s timeout. Failures were 104
illegal-target, 62 repetition, 5 parse and 3 phantom; the repetition gate
alone rejected 62 turns and 12 of those re-generated successfully. The
rejections, not the generation, are where the ledger arm's failures live.

## Paused: uk-s01-ledger

Stopped at the owner's request, mid-game, to free the host for other work.
Resumable with `--resume`; nothing was discarded.

State at the pause, for comparison when it is picked back up:

| | |
| --- | --- |
| Game id | `uk-s01-ledger` |
| Config | `season_uk_s01.ledger.local.yaml` |
| Seed | 42 |
| Reached | round 6, phase `traitor_night` (870 events, sequence 870) |
| Eliminations | 7 - tom, amanda, maddy, kieran, alyssa, rayan, alex |
| Traitors | alyssa, amanda, wilf |
| **Caught** | **2 of 3** (amanda r2, alyssa r5) |
| Calls | 842, 12.6% failed, p50 78.6s |

**This is the first run in which the table caught a traitor at all before
the endgame.** The two prior runs finished 2 of 3 from 17 eliminations,
which is at or below the 2.32 expected by chance. Here 2 of 7 caught
against 0.95 expected is a better ratio than either baseline managed -
but on seven eliminations that is still a small sample, and it is the
reason to resume rather than to call the question answered.

Resume with:

```bash
python -m simulation run configs/traitors/season_uk_s01.ledger.local.yaml \
    --game-id uk-s01-ledger --resume
```

Note that this arm moves **two** variables against `ptr_mem`: the
suspicion ledger and the newly-wired relationship standing. If the result
is ambiguous, a ledger-only arm is the obvious follow-up - see
docs/decisions.md.

## A resume that reopened the round it landed in

`restore_resume` rebuilds a crashed run's board from its event log and
steps the round number back one on purpose, because `start_round`
increments on the way in. The resume path then called `start_round`, so
the round the crash happened in was opened a second time: the log gained a
duplicate `ROUND_STARTED` for it, and the phase loop ran from the top of
the round rather than the phase that was cut off - re-spending every call
the completed phases had already made. Folded from that point, a round has
to be read by its *last* `ROUND_STARTED` to land on an honest board, and
code that has to be told that is a symptom, not the fix.

The fix is `GameEngine.reopen_round`: it sets the round and clears the
per-round scratch *without* emitting `ROUND_STARTED`, so the resumed
phases run against a round the log already vouches for. It keeps the
recruitment window recovered from the log, which a plain `start_round`
would have cleared - the window belongs to a traitor's banishment later in
the same round, so a crash between that banishment and the night they use
it is exactly the case that must keep it.

What did not change is the transport. An unreachable host that exhausts
the provider's retries still ends the run, deliberately: the framework's
answer to a sustained outage is to resume from the log, not to skip the
turn, because skipping turns through a real outage fills a run with missed
actions and passes it off as a vote. The transport cannot tell a genuine
outage from a one-off, so it does the safe thing. Only the *wait* grew - an
unreachable host (a network errno, no route) is backed off for longer than
a busy server - so a short outage is ridden out in process before the run
gives up and the supervisor resumes it.