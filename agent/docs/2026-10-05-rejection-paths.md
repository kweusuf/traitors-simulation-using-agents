# Rejection paths

Every way a model's output can be refused, in one place, with the reason the
refusals are split into layers and what each layer does about it.

The split is not cosmetic. Two of these layers ask the model again; the
third does not, and the difference decides whether an error costs a retry or
costs a turn.

## The three layers

**Advisory (layers 1-3)** live in `actions/validator.py` and
`agents/runtime.py`. They raise `ActionParseError`, which the decide loop
catches and answers with a correction prompt naming what was wrong. The
rejected attempt is already in the conversation, so the model can see it.

**Authoritative (layer 4)** is `engine/rules.py`. It refuses and logs
`ACTION_REJECTED`. No retry, because the reason is usually not something
re-asking would fix: the target died, the window closed, the phase moved on.

**Not a rejection at all** is the third category, and it is easy to
misread from the logs. Near-miss names are silently repaired. So is a
model-supplied `actor_id`. Both appear in the event log as normal actions.

## Layer 1 - output shape (`parse_action`)

Fails to produce a usable `Action` at all.

| Scenario | Result |
| --- | --- |
| Not valid JSON | Reject |
| Unsupported response type | Reject |
| Not a JSON object | Reject |
| Fails the `Action` schema | Reject |

Two things are overridden rather than refused, deliberately:

- **`actor_id` is always stamped** with the real agent id. A model naming
  itself as somebody else is never allowed to.
- **`target` is cleared** for actions that take no target. The structured
  schema requires the field on every response, so a public message arrives
  carrying either a copied player name or a `'none'` placeholder; neither
  means anything and both would fail the legal-target check.

## Layer 2 - content pre-flight (`check_action_constraints`)

Shape is fine, content is not usable as written.

| Scenario | Result |
| --- | --- |
| Action type not in the allowed set | Reject, retry |
| `end_vote` content not `end` / `banish` | Reject, retry |
| `recruit_decision` not `recruit` / `murder` | Reject, retry |
| `recruit_response` not `accept` / `decline` | Reject, retry |
| Target not among the legal targets | Reject, retry |

The correction text names both accepted values, because a small model
given `"content": "end"` as the example answers `end` regardless of what it
thinks. That bias is a documented finding in `docs/progress.md`, not a
bug; removing the example from the prompt is what fixed it.

## Layer 3 - semantic gates (`agents/runtime.py`)

Two gates, both default-off, both configurable, both retry. Both also
**accept the offending output on the final attempt** rather than failing
the turn - a model that cannot break out of a phrase must not cost the game
a turn, and the rejection stays counted in the run summary.

### Phantom players

A message naming someone who is not in the game. Off by default
(`reject_invented_players`); when on, calibrated by replaying a run in
order - one phantom caught, zero false positives, caught at the sequence
where it was introduced. The detector requires a capitalised token in
address position (immediately before a comma) that matches no player.

### Repetition

A message matching something already said (`reject_repetition`). Newer and
less settled than the other two; the threshold was calibrated by replaying
a completed run rather than assumed, and the calibration changed the
answer:

| Threshold | Would reject |
| --- | --- |
| 0.6 | 48.1% of public messages |
| 0.8 | 34.0% |
| 1.0 (exact only) | 17.5% |

The overlap measure cannot separate copying from discussion in this game.
Players legitimately keep returning to the same few claims, so nearly half
of a real run's messages overlap something said earlier. **The default
therefore requires an exact match**, which is the form the collapse
actually takes. Lower `repetition_threshold` to catch paraphrase, knowing
that it discards real messages with it. `tools/repetition_rates.py`
reproduces the measurement on any run.

Two known blind spots, pinned in tests rather than left to be discovered
in a run summary:

- **Word overlap cannot see a single-word change of mind.** "lying" swapped
  for "truthful" scores identically to a restatement, because one token
  differs in either direction. A player reversing themselves looks like a
  restatement. The exact-match default does not act on this.
- **Fixed-vocabulary actions are exempt.** Two `banish` votes are two
  votes, not a repeated turn. Votes, recruitment decisions and responses
  are never judged.

## Layer 4 - game rules (`engine/rules.py`)

Authoritative. Refuse and log `ACTION_REJECTED`; never retry.

**Phase and actor.** Action not allowed in the current phase; unknown
actor; actor not alive.

**Role legality.** Non-traitor killing; non-traitor recruiting; traitor
channel with no fellow traitor alive; non-traitor nominating; seer check
without the item; seer check already spent this game.

**Voting.** Self-vote where disabled; a tied suspect voting in their own
revote; a revote landing off the tied shortlist.

**Targets.** Unknown target; dead target; nominating yourself; killing
yourself; killing a fellow traitor; a kill outside the murder shortlist;
recruiting yourself; recruiting a traitor; nominating a fellow traitor;
checking yourself.

**Endgame and recruitment.** `end_vote` when the feature is disabled or the
finale is not running; recruitment disabled for the game; no window open
tonight; a non-traitor deciding; anybody but the offered player answering
an offer.

**Limits.** Per-agent, per-phase action limits exceeded.

## Silent repairs

Not refusals, and counted separately in the summary because they change
text that reaches the room:

- **Target typos.** `Meryl` and `Matty` become `matt`, when exactly one
  player is within one edit. Refusing instead would be worse - the model
  clearly named somebody. An ambiguous match (`Care` against `cara` and
  `core`) is left alone and reported, never guessed.
- **Names in prose.** The same repair applied inside `content`, which the
  target field check cannot see. A model can invent a whole player in the
  text and never trip the target check; that is how a phantom reached 82%
  of one run's messages.

## What is not a rejection

**Transport failures.** An HTTP 500 or a timeout is recorded in telemetry
and re-raised. It is not converted into a refusal, because the model never
got to answer and there is nothing to correct. This is the failure mode
that ended the `uk-s01-ptr-req` run in round 8.

**Retries, generally.** A refusal inside the decide loop spends an attempt
from the same budget as a malformed response. With `max_retries: 2` there
are three attempts total, shared between every layer. A response rejected
twice has one attempt left, and a repeat or phantom on that final attempt
is accepted rather than raised.

## Where each one shows up

- Advisory rejections land in `llm_calls.jsonl` as failed attempts, with
  the reason in `error`.
- Rule refusals land in `events.jsonl` as `ACTION_REJECTED`.
- Repairs and phantoms are held in memory on the runtime and reported in
  the run summary as counts, not as log lines - so a resumed run does not
  double-count them.
- `tools/audit_run.py` reports invented names, self-replies, cross-thread
  replies and duplicate text from the event log, independently of whether
  any gate was on.
