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

Not a bug so much as a wrong default, kept because changing it mid-experiment
would confound the ledger arm. See [learnings.md](learnings.md).

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