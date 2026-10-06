# Known bugs

Every defect found and not yet fixed, in one place, so a bug found during a
run is not rediscovered by the next person reading its logs.

`tools/bug_gate.py` reads this file and **refuses to start a new game while
anything under "Open" is unticked**. A run started with a known bug
silently corrupts its own data, and the corruption is only visible much
later, in the results.

Resuming is exempt: the rounds already played cannot be un-played, and
blocking a resume over a bug found in the run being resumed would strand it.

Format: one `- [ ]` or `- [x]` per bug, under a severity heading. The
checker reads the checkboxes, so do not reformat them away.

Related: [learnings.md](learnings.md) for what is known about behaviour,
[decisions.md](decisions.md) for why things are as they are.

---

## Blocking

These corrupt results. A new game must not start until they are fixed.

### [x] `by` means different things on different recruitment events

`RECRUIT_OFFERED`, `RECRUIT_ACCEPTED` and `RECRUIT_DECLINED` all carry
`payload.by`, but it is the **recruiter** in all three. On the first that
reads naturally; on the others it reads as "declined by X", which is false -
`X` is on the same side as the recruiter.

Observed in `uk-s01-ledger`, where it was read as the original traitor
refusing an offer to himself. He was never offered anything; he recruited
two players. The player who accepted or declined is `actor`.

`src/simulation/engine/game_engine.py:944-955`

**Fixed:** response events carry `by_offerer`, with the offerer also in
`targets`; pinned by
`test_recruit_response_events_name_the_offerer_unambiguously`.

### [x] The phantom detector reads sentence-initial words as players

`resolve_content_names` flags a capitalised token before a comma that
matches no player. "However, I think..." and "Meanwhile, ..." are ordinary
prose, and each costs a full retry on a ~25s call.

Two occurrences in one run:

```
your message names 'However', which is not a player in this game
your message names 'Meanwhile', which is not a player in this game
```

This is the false-positive risk the original calibration claimed was
eliminated; it was not, because only true phantoms were counted in that
check. `src/simulation/actions/validator.py:174-199`

**Fixed:** first-word discourse markers pass through as prose; pinned by
`test_sentence_initial_discourse_marker_is_not_a_phantom`. Note the
detector's remaining shape, recorded in the sibling test: it flags a name
in address position at a sentence start, so mid-sentence addresses
("Listen Iris, ...") were never covered - a separate gap, not blocking.

---

## Not blocking

Real, known, but they degrade a run rather than invalidate it.

### [x] `check_arm_flags.py` refuses any arm it does not recognise

```python
raise SystemExit(f"{path.name} is not a pointer arm; nothing to check")
```

Hit every time an arm is added: the checker exists to verify an arm is what
its name claims, and the first thing it does on a new arm is refuse to look.

`tools/check_arm_flags.py:31`

Fix: fall back to reporting the loaded flags, or delegate to
`tools/diff_arms.py` when a second config is given.

### [ ] Cross-player duplicates are not caught by `scope: self`

`uk-s01-rep` finished with 31 byte-identical duplicates, all from *different*
players, and zero self-repeats. The gate works; it was pointed at the
smaller half of the collapse.

`scope: room` is untested and would fire far more often against a run
already at 12-19% failures.

**Update:** `scope: room` now covers both halves and is pinned by
`test_room_scope_still_catches_the_agent_repeating_itself`. Whether the
*default* should move from `self` to `room` is still open.

Not a bug so much as a wrong default, kept because changing it mid-experiment
would confound the ledger arm. See [learnings.md](learnings.md).

### [x] `scope: room` drops the agent's own messages from the check

`uk-s01-ledger-room` ran `reject_repetition: true` at `repetition_scope:
room` and finished with **50 exact self-repeats in 552 public messages
(9.1%)** — the same author emitting byte-identical text. aaron does it
three times inside round 3 ("Theo, you're asking me to name a specific
gap in Maddy's ledger..."), alex across r5/r6/r6 and tom across r2/r2/r3.
By author: amos 7, alex 5, rayan 5, aisha 4, matt 4, fay 4, then imran,
john, aaron and hannah 3 each, the remaining five 2 or 1. That is *worse*
than the `scope: self` arm it replaced, which finished with zero.

Cause (read from `_repetition_reason`,
`src/simulation/agents/runtime.py:432-443`): `scope: room` builds the
history from `m.sender_id != view.agent_id` — everyone *else*. `scope:
self` uses `==`. The two scopes are therefore disjoint rather than nested,
and switching from `self` to `room` *removes* the self-check instead of
adding to it. The gate works; `room` is "others-only" where it should be
"self plus others". Cross-author duplication did stay low (4 texts, 6
copies), so the gate did catch the half it was aimed at.

`src/simulation/agents/runtime.py`, `src/simulation/actions/repetition.py`

**Fixed:** `scope: room` now takes the whole `public_transcript`, so it is
self-plus-others and nested under `self` rather than disjoint from it.
Pinned by `test_room_scope_still_catches_the_agent_repeating_itself`.

### [ ] The discourse-marker allowlist misses ordinary sentence-initial adverbs

`resolve_content_names` (`src/simulation/actions/validator.py:179-208`)
excludes a fixed set of sentence-initial words — "however", "meanwhile",
"moreover", ... — from being read as an address. The set is not complete,
and the gap costs a retry. `uk-s01-ledger-room` r1 spent one on

```
alex: your message names 'Otherwise', which is not a player in this game
```

`"Otherwise, ..."` is the same shape of ordinary prose as the markers
already listed, and it is not rare enough to leave out. Same defect class
as the closed However/Meanwhile entry above: one false positive discards a
real turn, here on a call that p50s at ~77s.

The run's other two phantom rejections were real — `Ilya`, invented by
aaron in r1 and again by andrea in r5 — so the gate catches true phantoms
and misfired only on this one.

`src/simulation/actions/validator.py:179-208` (`resolve_content_names`)

### [ ] A hallucinated object can occupy the room, and the detector is blind to it

`uk-s01-ledger-room`'s central fiction is a **ledger** that does not exist
in the game. aaron — the first speaker of round 1 (sequence 41) — opens
with "Maddy, you mentioned the ledger entries were perfectly sequential",
attributing to Maddy a claim she had not made: maddy does not speak until
sequence 58, and her first message never mentions a ledger. The room takes
it up regardless, layering "a break in the sequence", "the absence of
gaps" and "a jagged scar" on top, and **169 of 552 public messages (30.6%)**
name the ledger by round 13. This is not a prompt leak — the prompt never
names the mechanism, deliberately
(`src/simulation/agents/ledger.py:241-243`) — it is generated once and then
copied by everyone.

The `hallucination_score` for this run is **0.0**. `experiments/quality.py`
checks generated text only against the *record* — fabricated eliminations,
alive-after-elimination, invented rounds, role contradictions — so an
invented non-player *object* driving 30% of the discussion is invisible to
it. Same failure mode as the invented *player* the phantom gate exists for,
but object-shaped and undetected.

`src/simulation/experiments/quality.py`

---

## Closed

### [x] Ledger calls bypassed telemetry

The ledger's generation was never recorded, so a run where every ledger
failed looked exactly like a run where the players had no opinions. Fixed
as `ledger_update`; `tools/run_health.py` now separates the two populations.

### [x] `run_health.py` divided by zero on a run with no calls

Crashed on a freshly started run before it had made any. Guarded.

### [x] Internal prompt vocabulary leaked into the fiction

"18 of the first 24 public messages" argued about a ledger that did not
exist in the game. Caused by the prompt saying "the top of your list".
The prompt now never names the mechanism; two tests pin it. See
[learnings.md](learnings.md).