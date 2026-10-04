# Decisions

Every decision made so far, with the reasoning behind it and what was gained
or measured afterwards. Where a decision was later corrected or withdrawn,
that is stated rather than edited away.

The measurements behind these live in [learnings.md](learnings.md). The code
they act on is mapped in [flow.md](flow.md).

---

## The shape of the project

### Config is the primary interface

**Decision.** Game rules, mechanics, model and concurrency are driven from
YAML. A behaviour change is usually a config change, not a code change.

**Reasoning.** This is a framework for social simulation, not a game. The
interesting question is what a *rule* does to an *outcome*, so rules have to
be varyable without touching code. With 22-player games costing hours of
model time, an experiment needing a code edit per arm runs once.

**Benefit.** Thirty-odd arms exist as YAML, some sharing all but one line.
`tools/diff_arms.py` loads two through the real loader and diffs the
resolved settings, so a one-variable change is verifiable before spending
model time - not asserted.

### Every experimental flag defaults off

**Decision.** `reject_invented_players`, `anti_echo_instructions`,
`co_generate_gist`, `pointer_memory`, `reject_repetition` and the rest all
default off, with a run's *state* recorded explicitly in its config.

**Reasoning.** A runtime constructed with new flags must play *exactly* as it
did before, or every earlier run stops being a baseline.

**Benefit.** 577 tests pass with all flags off and the control arms stay
valid. A config that states its own flags is also a record of what the run
actually did, independent of its filename.

### Model identity is an explicit variable, never implicit

**Decision.** Model and endpoint live in gitignored `*.local.yaml`, set
separately from the tracked arm.

**Reasoning.** This repository is public. A LAN address pins one machine; a
model digest pins an exact local build. Neither may enter it. Tracked
configs keep `HOST_A` placeholders.

**Benefit.** `*.local.yaml` is where per-machine detail lives, confirmed by
`git check-ignore`. Tracked arms stay shareable. This was a scrub, not a
precaution - see the history around `4f17852`.

---

## Rejection and repair

Full taxonomy in
[2026-10-05-rejection-paths.md](2026-10-05-rejection-paths.md).

### Refusals split into advisory and authoritative

**Decision.** Output-shape and content failures raise `ActionParseError` and
re-ask. Game-rule violations refuse and log `ACTION_REJECTED`, no retry.

**Reasoning.** Advisory failures mean the model's *intent* was probably
sound - malformed JSON, or a name it could not see was dead. Re-asking costs
one generation and fixes it. A rule violation means the world moved: the
target died, the window closed. Re-asking only invites another illegal
answer, and the loop would never converge.

**Benefit.** One shared retry budget across all advisory layers, so a
response rejected twice has one attempt left. Rule refusals stay visible in
the event log without costing latency.

### Malformed output retries rather than killing the run

**Decision.** `max_retries: 2`, so three attempts per action.

**Reasoning.** Small local models miss required fields often enough that a
single retry still lost whole runs - and a run lost to a JSON error produced
no data at all.

**Benefit.** The suite runs on a fake backend with no model; live runs lose
a small fraction of turns to recoverable output.

### Near-miss names are repaired, not refused

**Decision.** `Meryl` and `Matty` become `matt` when exactly one player is
within one edit. An ambiguous match is left alone and reported.

**Reasoning.** The model clearly named somebody; refusing would be worse than
correcting. Guessing between `cara` and `core` would be worse than asking.

**Benefit.** Name errors stop costing whole turns, and ambiguity stays
visible rather than being silently resolved the wrong way.

### The model's own `actor_id` is always overwritten

**Decision.** Stamp the real agent id onto every parsed action.

### The repetition threshold was set by measurement, not intuition

**Decision.** Default `repetition_threshold: 1.0` - exact match only.

**Reasoning.** The intended threshold was 0.6, chosen to catch paraphrase.
Replaying `ptr_mem` first showed 0.6 would reject **48% of real messages**,
because players legitimately keep returning to the same few claims. The
overlap measure cannot separate copying from discussion at that level.

**Benefit.** The default catches the 17.5% that are genuinely byte-identical
- the form the collapse actually takes - without discarding half a game.
Paraphrase detection is built and available for a run showing a reworded
collapse. See
[learnings.md](learnings.md#word-overlap-cannot-separate-copying-from-discussion-in-this-game).

### A rejected repeat is accepted on the final attempt

**Decision.** On the last attempt, let the repeated message through.

**Reasoning.** A model stuck in a phrase must not cost the game a whole turn.
The rejection stays counted in the summary.

**Benefit.** The gate cannot end a game: 21 rejections in `uk-s01-rep`, no
lost turns.

### `scope: self` was the wrong default, and the data says so

**Decision.** Run with `repetition_scope: self`; `room` is untested.

**Reasoning.** `self` was chosen to be conservative about false positives.
Measured, it was the smaller half of the problem: cross-player duplicates
fell only 50 → 15, while self-repeats went to zero. The collapse is
*different players* saying the same thing, which `self` cannot see by
construction.

**Benefit.** A clean one-variable result and a specific next arm, rather than
a vague "try it on". See
[learnings.md](learnings.md#the-template-collapse-is-cross-player-not-self-repetition).

---

## Memory and knowledge

### Agent memory decays, per observer, scoped to what each player could know

**Decision.** Default on. Items decay by round, floor at a minimum salience,
stored per-observer, and written only to players who could legitimately see
the source event.

**Reasoning.** A shared memory would leak - a traitor council note must never
reach a faithful player. Non-decaying memory made early rounds loom over later
ones, since round 1 never faded.

**Benefit.** Players reason from recent evidence rather than the whole
transcript, and a resumed run rebuilds memory from the event log.

### Facts do not decay; confidence is carried separately from salience

**Decision.** Durable facts (an elimination, a vote) persist. The model's
`confidence` rides along on the memory item rather than being discarded with
the action.

**Reasoning.** A player forgetting that someone was eliminated is a bug, not
decay. But how sure they *were* matters when the read later turns out wrong,
so confidence cannot be inferred from age alone.

**Benefit.** A belief can be tracked as it moves. See
---

## Correctness of the harness

### Closed-choice content is pinned to an enum, not decided by an example

**Decision.** Remove the worked example from closed-choice prompts; pin
`content` to an enum in the schema.

**Reasoning.** Every season run ended its finale with all five players
answering `end`, because the example said `"content": "end"`. A unanimous end
stops the game - so a surviving traitor took the prize. That was the
mechanism behind a run of traitor wins.

**Benefit.** Measured on the real final five: example `end` gave 6/6 `end`,
example `banish` gave 4/4 `banish`. Bias gone. The model's own preference for
ending remains, recorded rather than papered over.

### Every run gets its own directory and database

**Decision.** `runs/<game_id>/game.db`, with a per-run lock.

**Reasoning.** Two runs sharing one root database interleaved their writes.
The lock records the owning pid so a killed run is recoverable rather than
deadlocking.

**Benefit.** Concurrent runs cannot corrupt each other.

### Resume replays the log; it does not truncate telemetry

**Decision.** Resume reconstructs state from `events.jsonl` and rebuilds
memory from the log. `TelemetryRecorder` is constructed with `resume=` so it
does not truncate.

**Reasoning.** `continue_after` clears the in-memory event list, so a resumed
run reading from it would forget every round before the interruption - and
`public_rivals` would stop finding the nominations that make the animosity
effect work at all.

**Benefit.** A crashed run resumes without losing its rounds or its evidence.
The context-window crash in `uk-s01-rep` cost round 1 only.

### Every refusal that can refuse happens before the run directory is touched

**Decision.** Check resume identity, seed and experiment id before building
anything that writes.

**Reasoning.** `TelemetryRecorder` truncates `llm_calls.jsonl` on
construction, so constructing it before the resume point is settled would
erase a crashed run's telemetry during the very attempt meant to recover it.

**Benefit.** A rejected resume leaves the run untouched.

---

## Process

### Verify host state before a long run, not after it fails

**Decision.** Check `/api/ps` for context length and residency before
launching.

**Reasoning.** Two runs were lost to host state assumed rather than checked:
the 27B (weights-on-disk arithmetic said it fit; resident memory said
otherwise) and the context window (`n_ctx: 4096` serving a model that reports
262144).

**Benefit.** Both are now a ten-second check instead of a lost run. See
[learnings.md](learnings.md#the-hosts-context-size-is-server-state-not-config).

### Concurrent runs are capped at what the host serves in parallel

**Decision.** `max_concurrency: 4`, two runs at once.

**Reasoning.** 16 concurrent finished a season but degraded badly (p95 674s,
23% failed). Past capacity, requests queue into the timeout rather than
erroring.

**Benefit.** Runs complete with a 0-10% failure rate instead of degrading
into timeouts.

### Measure with a tool, not by reading a run folder

**Decision.** `prompt_composition.py`, `repeat_rate.py`,
`repetition_rates.py`, `collapse_probe.py`, `run_health.py`,
`compare_arms.py`, `audit_run.py`, `schema_check.py`, `diff_arms.py`.

**Reasoning.** Every headline number here came from a tool that replays real
data. Three separate wrong claims - the 38% repetition rate, the pointer
arm's speed, the 0.6 threshold - were caught by measuring instead of
reasoning.

**Benefit.** Findings are reproducible by anyone, and each threshold in the
config traces to a measurement.

### Never commit local machine detail

**Decision.** No LAN or public addresses, model digests, throughput
measurements, absolute paths or hostnames in any tracked file.

**Reasoning.** The repository is public, and force-pushing a rewrite does not
retract anything already published.

**Benefit.** Enforced by a scrub over every blob, with the recovery procedure
documented in `AGENTS.md`.

---

## Superseded or under review

- **A larger model.** Abandoned. The 27B timed out on the shortest prompt the
  game produces. Stay on the 9B until host state is understood.
- **`uk-s01-en5`.** Killed during round 4 after repetition and prompt growth
  worsened. Not resumed.
- **`uk-s01-ptr-req`.** Crashed during round 8 on an HTTP 500. Resume is a
  separate decision; its lock is cleared but the run is not resumed.
- **Phantom gate default.** Currently `false`, though `ptr_mem` runs with it
  on. The detector still false-positives on sentence-initial words, so it
  should not become the default until that is fixed.
[2026-10-01-agent-memory-design.md](2026-10-01-agent-memory-design.md).

### Pointers replace full messages in later prompts

**Decision.** Ask for a one-line `gist` in the *same* call that writes the
message, and store a decaying pointer instead of the full text.

**Reasoning.** The transcript is 93% of a late-game prompt. If the pointer
rides along in the existing generation, it costs nothing to produce.

**Benefit.** Repetition fell 23-33% → 16% over 509 messages; worst-case
shared line 13 players → 5. Repetition reduced, not solved - and it did *not*
flatten the prompt curve, since `transcript_messages_per_prompt: 40` is the
real cap. Two claims were withdrawn here; see
[learnings.md](learnings.md#what-we-got-wrong-and-withdrew).

### Private chats route replies to whoever wrote them

**Decision.** A two-wave private chat with reply-target routing.

**Reasoning.** Without it a DM was a stream nobody addressed, and replies
landed on the wrong player.

**Benefit.** Private threads carry the pair's own history, and replies land
where intended.
**Reasoning.** A model claiming to be another player is never correct, and no
retry should be spent discovering that.

**Benefit.** Identity comes from the caller, not the response.

### Repetition is rejected and the model is asked again

**Decision.** Add `reject_repetition`, raising `ActionParseError` so the
existing correction retry carries the rejected message back with a quoted
copy of what it said.

**Reasoning.** Rejected-and-reasked was the requirement, and the machinery
already existed - the retry loop appends the rejected attempt, so quoting it
back cost nothing new. The threshold needed care; see below.

**Benefit.** Self-repeats went **32 → 0** against `uk-s01-ptr-mem`.