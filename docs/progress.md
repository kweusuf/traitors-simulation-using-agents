# Project Progress: LLM Social Simulation Framework

**Source of truth:** `02-framework-requirements-and-scaffolding-spec.md`
**Status:** MILESTONE 2 REACHED (Phases 0-30 complete, 419 tests green)
**Started:** 2026-09-26

This document tracks implementation progress. Each phase ends with a git
commit. A phase is only marked complete when its verification step passes
and its commit lands.

------------------------------------------------------------------------

## Conventions

- One git commit per completed phase, prefixed `phase-N: ...`.
- A phase moves from `pending` to `in progress` to `complete` only after
  its verification step runs and its commit lands.
- Tests are written alongside the phase they verify, not deferred.
- Spec section references (e.g. `spec §29`) point into
  `02-framework-requirements-and-scaffolding-spec.md`.

------------------------------------------------------------------------

## Phase overview

| Phase | Title | Status | Commit |
|-------|-------|--------|--------|
| 0 | Repository initialization | complete | `b5dc646` (docs), `pending` (this doc) |
| 1 | Project skeleton and tooling | complete | `cc0b9dc` |
| 2 | Domain models: state, actions, events | complete | `86e73a0` |
| 3 | Event system and persistence (SQLite + JSONL) | complete | `ef3f4ed` |
| 4 | Game engine and generic phase engine | complete | `fd0ddc4` |
| 5 | Communication routing and information projection | complete | `1c8e337` |
| 6 | Agent runtime: persona, memory, beliefs, relationships, prompts | complete | `1820c57` |
| 7 | LLM gateway and FakeLLMProvider | complete | `5d5a14b` |
| 8 | Traitors environment (phases, rules, win conditions) | complete | `10607b5` |
| 9 | Deterministic test suite (unit, security, integration) | complete | `511a6f5` |
| 10 | OllamaProvider | complete | `2c27c8e` (code), `8fb7d21` (docs) |
| 11 | CLI, config-driven run, run artifacts | complete | `f9da4fe` |
| 12 | Replay, inspect, list-games | complete | `f9da4fe` |
| 13 | Traitor recruitment on banishment | complete | `b9d540c` |
| 14 | Finale at 3v3, rapid-fire voting, solo/team wins | complete | `3c911ca` |
| 15 | Per-call LLM telemetry and quality metrics per run | complete | `1f6fc63` |
| 16 | Concurrent turns and bounded prompts (performance) | complete | `b2bc68f` |
| 17 | Duplication score and cross-player speech similarity | complete | `0e881f4` |
| 18 | Traitor night council and strategy guidance | complete | `4674f58` |
| 19 | Score ledger, paired-seed gates, run diagnosis | complete | `2b89f58` |
| 20 | Shield, seer, dagger and the murder shortlist | complete | `0df713e` |
| 21 | Benchmark a run against a real season | complete | `9d9f658` |
| 22 | Season replay: pinned traitors, cast personas, ground truth | complete | `d9317b5` |
| 23 | Benchmark overlaps and index-aligned eliminations | complete | `f7bb705` |
| 24 | Seasonal cadence for murders and banishments | complete | `cf9c984` |
| 25 | Endgame end-or-banish vote with blind finale | complete | `c28d39c` |
| 26 | Recruitment as a choice with ultimatum | complete | `53b5b89` |
| 27 | Endgame trigger at the final five | complete | `26a2550` |
| 28 | Hosted debate clock (audit fix, phase 1) | complete | `6b8dfe2` |
| 29 | Round-table nomination, rebuttal, revote (audit fix, phase 2) | complete | `8c46d5b` |
| 30 | Sequential traitor council (audit fix, phase 3) | complete | `pending` |
| 31 | Private chats with pair history (audit fix, phase 4) | pending | `pending` |
| 32 | Conflict personas and clash metrics (audit fix, phase 5) | pending | `pending` |

Phase 9 completing is **Milestone 1** (spec §33): a full deterministic
six-player game on the fake backend with proven information boundaries.
Phases 10-11 completing is **Milestone 2** (spec §34): same game on
Ollama with `game.json`, `events.jsonl`, `transcript.txt`,
`metrics.json` outputs.

------------------------------------------------------------------------

## Phase details

### Phase 0: Repository initialization

- [x] `git init`, branch `main`.
- [x] Commit research notes and scaffolding spec.
- [x] Commit this progress document.

**Verification:** `git log` shows the doc commits.

### Phase 1: Project skeleton and tooling

- [x] `pyproject.toml` with package `simulation` under `src/`, Python 3.11+,
  dependencies: `pydantic`, `pyyaml`, `typer` (or `argparse`), `pytest`.
- [x] Directory tree per spec §41: `src/simulation/{engine,agents,memory,
  beliefs,relationships,communication,actions,models,persistence,
  experiments,environments/traitors,cli}`, `tests/{unit,integration,
  security}`, `configs/{traitors,personas}`, `runs/`, `docs/`.
- [x] Package `__init__.py` files, empty placeholder modules.
- [x] `.gitignore` (`runs/`, `__pycache__/`, `.venv/`, `*.db`).
- [x] `README.md` with run instructions stub.
- [x] `configs/traitors/basic.yaml` initial draft (6 players, 2 traitors,
  5 rounds, phase list, llm section per spec §22, §32).
- [x] Persona config files in `configs/personas/` (analytical, politician,
  observer, contrarian, loyalist, opportunist).

**Verification:** `pip install -e ".[dev]"` succeeds, `pytest` collects
zero tests without error, `python -c "import simulation"` works.

### Phase 2: Domain models: state, actions, events

- [x] Pydantic models: `GameState`, `PlayerState`, `Role`, `GamePhase`,
  `MissionState` (spec §6).
- [x] `ActionType` enum with MVP subset: `PUBLIC_MESSAGE`,
  `PRIVATE_MESSAGE`, `VOTE`, `TRAITOR_KILL` (spec §8).
- [x] Structured `Action` model (action, target, content, confidence).
- [x] Event models for the full event vocabulary (spec §23).
- [x] `Message` model with `Channel` enum: `PUBLIC`, `PRIVATE`,
  `ROLE_PRIVATE`, `SYSTEM` (spec §9).
- [x] Unit tests: model validation, enum coverage, event serialization.

**Verification:** `pytest tests/unit/test_models*.py` passes.

### Phase 3: Event system and persistence (SQLite + JSONL)

- [x] Append-only JSONL event log writer (spec §23).
- [x] SQLite schema and repositories: `games`, `agents`,
  `agent_memories`, `messages`, `events`, `votes`, `eliminations`,
  `relationships`, `beliefs`, `snapshots`, `experiments` (spec §24).
- [x] Repositories kept separate from domain logic.
- [x] State snapshot creation (for later replay/counterfactuals,
  spec §44).
- [x] Unit tests: event round-trip serialization, repository CRUD,
  snapshot restore.

**Verification:** `pytest tests/unit/test_persistence*.py` passes;
events written to JSONL are append-only and replayable.

### Phase 4: Game engine and generic phase engine

- [x] `game_engine.py`: owns state, assigns roles, validates and applies
  actions, emits events, computes win conditions (spec §5).
- [x] `phase_engine.py` with `Phase` protocol (`run(context) ->
  PhaseResult`) and configurable phase ordering (spec §7).
- [x] `rules.py` rule checks (alive checks, self-vote prohibition,
  role-restricted actions, phase limits).
- [x] Deterministic seeded RNG for role assignment.
- [x] Engine never generates natural language (spec §5, §40).
- [x] Unit tests: role assignment distribution, phase transitions, vote
  counting, tie handling, elimination, win conditions.

**Verification:** `pytest tests/unit/test_engine*.py` passes; engine
steps through phases with scripted actions, no LLM involved.

### Phase 5: Communication routing and information projection

- [x] Message router for `PUBLIC`, `PRIVATE`, `ROLE_PRIVATE`, `SYSTEM`
  channels (spec §9).
- [x] Structural visibility enforcement: non-recipients can never read a
  message (not prompt-based) (spec §9).
- [x] `InformationProjector.project(game_state, agent_id)` producing
  agent-specific views (spec §10).
- [x] Security tests: faithful cannot see traitor private chat, traitor
  cannot see unrelated private chat, hidden roles never appear in public
  observations, dead agents excluded (spec §29).

**Verification:** `pytest tests/security/` passes, zero leakage cases.

### Phase 6: Agent runtime: persona, memory, beliefs, relationships, prompts

- [x] `Agent` stateful object: identity, persona, role, goals, memory,
  beliefs, relationships, model handle (spec §11).
- [x] Runtime loop: observation -> memory retrieval -> prompt build ->
  LLM call -> parse -> validate -> return action (never executes it).
- [x] Persona as YAML data with trait-to-instruction translation
  (spec §12).
- [x] Goals separated from personality, role goals injectable (spec §13).
- [x] `Memory` interface: `remember`, `retrieve`, `summarize`; recent
  buffer + periodic summary, SQLite-backed, no vector DB (spec §14).
- [x] Structured `Beliefs` state (spec §15) and `Relationships` state
  (spec §16), optional at runtime.
- [x] Prompt builder as separate component with composition per spec §20.
- [x] Structured output parsing with malformed-output retry prompt
  (spec §21).
- [x] Unit tests: prompt builder composition, memory persistence,
  belief/relationship updates, output parsing and retry.

**Verification:** `pytest tests/unit/test_agents*.py
tests/unit/test_memory*.py tests/unit/test_prompt*.py` passes.

### Phase 7: LLM gateway and FakeLLMProvider

- [x] `LLMProvider` protocol: `generate(messages, response_schema,
  config) -> LLMResponse` (spec §17).
- [x] `ModelConfig` normalization (temperature, max_tokens, timeout,
  provider-specific options) (spec §19).
- [x] `FakeLLMProvider` returning predetermined structured actions
  (spec §30).
- [x] Concurrency limiting (`max_concurrency`) in gateway (spec §18).
- [x] No provider-specific code in agent or engine (spec §17).
- [x] Unit tests: fake provider script playback, config parsing,
  concurrency cap.

**Verification:** `pytest tests/unit/test_models*.py` passes without any
Ollama running.

### Phase 8: Traitors environment (phases, rules, win conditions)

- [x] `environments/traitors/game.py`, `rules.py`, `phases.py`.
- [x] Phase implementations: `MISSION`, `PUBLIC_DISCUSSION`,
  `PRIVATE_CHAT`, `ROUND_TABLE`, `VOTING`, `ELIMINATION`,
  `TRAITOR_NIGHT`, `GAME_END` (spec §7).
- [x] 6 players, 2 traitors, 4 faithful, 5 rounds max; mission
  abstraction; night kill; majority-vote elimination; win conditions
  (spec §2).
- [x] Communication limits from config (`public_messages_per_agent`,
  `private_messages_per_agent`).
- [x] Environment implementing the generic `Environment` interface
  (spec §38) so future games can reuse the runtime.
- [x] Unit tests: phase ordering, mission resolution, night kill
  targeting, win detection for both teams.

**Verification:** `pytest tests/unit/test_traitors*.py` passes.

### Phase 9: Deterministic test suite (unit, security, integration) — Milestone 1

- [x] Full unit coverage sweep per spec §29 list.
- [x] Security-style tests complete per spec §29.
- [x] Integration test: 4-agent miniature game on `FakeLLMProvider`,
  fully deterministic, no Ollama (spec §29, §30).
- [x] Full six-player game end-to-end on fake backend (spec §33).
- [x] Leakage audit: scan all produced observations for role strings.

**Verification:** `pytest` green across `tests/`; integration test
produces a complete game with a winner and consistent event log.

**Milestone 1 gate:** do not proceed to Ollama until this passes.

### Phase 10: OllamaProvider

- [x] `OllamaProvider` implementing `LLMProvider` over local Ollama HTTP
  API (spec §17, §18), poster-injectable so tests need no server.
- [x] Model selection and model details are config-driven (spec §18,
  §19, §22): `LLMSettings` gained `base_url` and `options`, and
  `LLMSettings.to_model_config()` builds the provider-facing
  `ModelConfig`. Current model is `hauhau-qwen:latest`, recorded in
  `configs/traitors/basic.yaml`. Machine-specific endpoints (a remote
  Ollama host) go in a `*.local.yaml` copy that `.gitignore` excludes.
- [x] Timeout, transport/HTTP/malformed-response error handling
  (`OllamaError`), JSON-schema structured output (`format`).
- [x] `reasoning_effort` normalized onto Ollama's `think` flag;
  `none` keeps thinking tokens out of the `max_tokens` budget.
- [x] Tests against an injected fake HTTP layer (no live Ollama in CI):
  `tests/unit/test_ollama.py`.

**Verification:** `pytest` green (130 tests; also green with the
committed `basic.yaml`, so the suite does not depend on the
uncommitted config). Manual smoke against local Ollama with
`hauhau-qwen:latest` via `OllamaProvider.generate`: JSON-schema
constrained reply parsed as an `action` in ~14s, 31 eval tokens.

### Phase 11: CLI, config-driven run, run artifacts — Milestone 2

- [x] CLI commands: `run <config>`, `batch <config> --games N`,
  `replay <game_id>`, `inspect <game_id>`, `list-games` (spec §31).
- [x] `python -m simulation run configs/traitors/basic.yaml` produces a
  complete game (spec §43).
- [x] Run artifacts per game under `runs/<game_id>/`: `config.yaml`,
  `events.jsonl`, `transcript.json`, `metrics.json` (spec §34, §43).
  The run also writes `game.json` and `transcript.txt`.
- [x] Experiment identity recorded: `experiment_id`, `game_id`,
  `random_seed`, `model`, `model_parameters`, `prompt_version`,
  `persona_version`, `game_rules_version`, `memory_strategy`
  (spec §27).
- [x] Batch runner with deterministic per-game seeds (spec §26).
- [x] Optional Langfuse flag wired as no-op when disabled (spec §28):
  `experiments/observability.py` ships `NullTracer` and says so when
  `observability.enabled=true`.

**Verification:** fake-backend run from the CLI produces the full output
tree from spec §43 (`config.yaml`, `events.jsonl`, `game.json`,
`transcript.json`, `transcript.txt`, `metrics.json`) with every
identity field populated; one real Ollama run documented as
`runs/game-003` (44 calls, 8,123 tokens, ~11 min against
`hauhau-qwen:latest`).

**Milestone 2 gate:** passed (with Phase 10).

### Phase 12: Replay, inspect, list-games

- [x] `simulation replay <game_id>` reconstructs visible state from
  events (spec §25).
- [x] `simulation inspect <game_id> --agent alice` shows an agent's
  projected view.
- [x] `simulation snapshot <game_id> --round 3` (from snapshots stored
  in Phase 3).
- [x] Tests: replay determinism (same events -> same reconstructed
  state).

**Verification:** `pytest tests/unit/test_replay*.py` passes; CLI
replay of a fresh fake run matches its `game.json` on winner, roles
and alive set; `snapshot --round 1` reads the stored snapshot from
SQLite.

Phases 11 and 12 landed in a single commit (`f9da4fe`) because
`cli/main.py` serves both: `list-games` reads reconstructed state out
of `experiments/replay.py`, so the two phases cannot be split into a
buildable intermediate state.

### Phase 13: Traitor recruitment on banishment

Outside the original phase plan: a game rule the long game needs. When
the round table banishes a traitor and `game.recruit_on_banish` is on,
the banished traitor is asked for one `RECRUIT` action before the tally
resolves and picks a living faithful player, who is converted before
the win check runs. `game.max_recruits` caps conversions for the game
(0 = no cap).

- [x] `RECRUIT` action: target required, traitor-only, elimination
  phase only, one per phase, rejected while recruitment is disabled.
- [x] `ROLE_RECRUITED` event carried through replay, transcript,
  metrics and CLI progress output.
- [x] Engine holds the recruit offer until the banishment resolves, so
  a last traitor taken off the board by vote can still hand over.
- [x] Runner re-syncs agent roles, so a recruited player prompts and
  plays as a traitor from then on.
- [x] Config: `recruit_on_banish` / `max_recruits` on `GameSettings`;
  off in `basic.yaml`, uncapped in `long_game.yaml`.
- [x] Tests: `tests/unit/test_recruitment.py` (config, rules, engine
  conversion and cap, phase wiring, prompt, role sync, replay, ten full
  fake-backend games).

**Verification:** 197 tests green. A fake run of the 21-player
`long_game` config completes in 8 rounds with 477 calls, zero rejected
actions, and a recruitment on every banishment, ending in a traitor
win. Note the balance consequence: with no cap the faithful can never
empty the traitor team by voting, so their only wins come from the
round limit or from `max_recruits` being reached.

### Phase 14: Finale at 3v3, rapid-fire voting, solo and team wins

Also outside the original plan. Normal play stops when exactly
`finale_traitors` traitors and `finale_faithful` faithful are alive:
the parity win is suppressed at that split and a rapid-fire finale
runs instead, one iteration of round table, private chat, vote and
banishment at a time, with no missions, no night murder and no
recruitment. A tie eliminates nobody and the vote repeats; after
`finale_max_votes` consecutive rounds with no banishment,
`round_limit_winner` is declared with reason `finale_vote_limit`.

- [x] `finale_traitors` / `finale_faithful` / `finale_max_votes` on
  `GameSettings`, both counts required together, 0/0 disables the
  finale; enabled in `long_game.yaml`.
- [x] `FINALE_STARTED` event; `GameState.finale` reaches `game.json`,
  snapshots, replay and transcript.
- [x] Seeded hidden ambition per player (`GameState.ambitions`,
  `"solo"` or `"team"`) injected into traitor goals, plus a traitor
  system-prompt paragraph that spells out both alliances. Views never
  expose it.
- [x] Individual outcomes: `GAME_WON` carries `surviving_traitors`,
  `finale` and `solo`; `metrics.json` gains `outcomes`,
  `solo_traitor_win`, `finale` and `ambitions`.
- [x] Tests: `tests/unit/test_finale.py`.

**Verification:** 212 tests green. A fake run of the 21-player
`long_game` config reaches 3v3 in round 8 (8 recruits along the way),
runs four rapid-fire votes, and ends on a solo traitor win with 531
calls and zero rejected actions.

### Phase 15: Per-call LLM telemetry and quality metrics per run

Every run now carries its own 360 degree view of the model operations,
stored inside the run directory so the artifacts travel together.

- [x] `llm_calls.jsonl`: one line per model call with agent, action,
  phase, round, attempt, tokens in and out, latency, prompt size,
  transport retries and failure reason (`experiments/telemetry.py`).
- [x] `metrics.json` `llm` block: input/output/total tokens, token
  coverage, latency avg/p50/p95/max, per-action breakdown, failed
  turns, parse-retry calls, provider calls and retries.
- [x] `metrics.json` `quality` block (`experiments/quality.py`),
  derived from the run's own events with no model in the loop:
  `hallucination_score` (fabricated eliminations, calling an eliminated
  player alive, invented rounds), secrecy flags judged against the role
  held at that moment (traitor self-declaration, affiliation phrasing,
  naming a fellow traitor next to affiliation wording but not next to
  an accusation, faithful claiming traitor), duplicate messages across
  authors, and parse failures.
- [x] `simulation metrics <game_id>` command with `--json` and
  `--recompute`; an unfinished run reports quality but never gets an
  invented `metrics.json`.
- [x] Ollama input tokens captured from `prompt_eval_count`
  (`LLMResponse.input_tokens`), which was previously discarded.
- [x] Tests: `tests/unit/test_telemetry.py`,
  `tests/unit/test_quality.py`, plus runner and CLI cases.

**Verification:** 250 tests green. The detector was validated against
the spoiled game-004 (one affiliation outing by alice, two teammate
namings, one faithful claiming traitor) while leaving the clean
game-003 at zero. A fake CLI run produces all seven artifacts with the
telemetry line count matching `llm.calls`.

### Phase 16: Concurrent turns and bounded prompts

Speed work driven by measurements against the remote Ollama host, not
guesses. A round had been taking over an hour for two reasons, and
there were no sleeps or waits anywhere in the game path: the only
`asyncio.sleep` in production code is the retry backoff that runs when
a call fails.

- [x] Turns inside a phase are decided concurrently
  (`_ask_all_alive` and the night kill gather them), capped by the
  gateway's `max_concurrency`, then submitted in id order from
  phase-start snapshots so the event log stays deterministic.
  Measured: 4 concurrent calls give 1.7 calls/min against 0.5
  calls/min serial, about 3.4x throughput. Previously
  `in_flight_peak` was 1 in every run, because the phase loop awaited
  one player after another.
- [x] `communication.transcript_messages_per_prompt` (default 40,
  0 = all) bounds the transcript a prompt carries. A reconstructed
  late-game prompt is 168,750 characters and Ollama evaluated 31,410
  tokens of it per call at 109 to 220 seconds per call, so prompt
  evaluation, not generation, dominates. Views, replay and `inspect`
  are unchanged; only the prompt window is trimmed, with an explicit
  "earlier N messages are omitted" note.
- [x] Long game configs run at `max_concurrency: 4`.
- [x] Tests for concurrent asking (every player asked at once,
  submissions still in id order) and for the transcript window.

**Verification:** 253 tests green.

### Phase 17: Duplication score and cross-player speech similarity

Two more deterministic signals in the quality block, both derived from
the run's own messages:

- [x] `diversity.duplication_score`, 1 - (distinct texts / messages),
  so 0.0 means every message is unique and 1.0 means they are all the
  same, alongside the existing cross-author duplicate counters and
  `distinct_texts`.
- [x] `speech_similarity`: for every pair of players who spoke, cosine
  similarity over each player's whole corpus, reported twice, content
  words with stopwords removed (do they talk about the same things) and
  character trigrams (do they phrase things the same way), with the
  mean, the most similar pair, and a per-player average.
- [x] Both printed by `simulation metrics <game_id>`.
- [x] Tests: `tests/unit/test_quality.py`.

**Verification:** 261 tests green. On game-005 the scores read
duplication 0.4908 (139 distinct texts out of 273 messages) and mean
speech similarity 0.82 content / 0.94 phrasing across 210 pairs, with
judy and oscar the most alike pair at 0.979 / 0.9925, which matches
the boilerplate collapse seen by eye.

### Phase 18: Traitor night council and strategy guidance

The night had no discussion at all: each traitor picked a victim
independently and a majority vote settled it, and nothing in the
prompts ever mentioned blame, framing or threat. Now:

- [x] `TRAITOR_MESSAGE` action on the `ROLE_PRIVATE` channel, asked of
  every living traitor before the kill, targetless, content required,
  traitor-only, one per phase, only while other traitors are alive.
  Recipients are the living traitors, enforced by the router, so a
  faithful player structurally cannot read it.
- [x] Prompt guidance: the council argument (biggest threat versus
  most convenient suspect, who takes the blame), the kill (victim for
  a reason that puts an innocent in the frame, recorded in
  `reason_summary`), the round table (build the case against one
  innocent, defend yourself first if suspicion turns), and the vote
  (vote with the faithful against the framed innocent, otherwise keep
  yourself and your team safe).
- [x] Quality metrics ignore traitor-only traffic for secrecy, since
  saying "I am a traitor" to your own team is the point of the
  channel, not a leak.
- [x] Tests: council before kill, structural invisibility to faithful,
  lone traitor gets no council, action rules, prompt guidance scoped to
  traitors and to the round table.

**Verification:** 266 tests green. A fake long game runs 21 council
messages over 12 rounds with zero messages reaching a non-traitor.

### Phase 19: Score ledger, paired-seed gates, run diagnosis

Wave A of the self-improvement plan in
`docs/2026-09-27-traitors-format-adoption-and-self-improvement.md`:
make every run's scores comparable so a prompt change can be promoted or
rejected on evidence.

- [x] `runs/ledger.jsonl`: one appended line per finished run with seed,
  holdout flag, prompt version, outcome, calls, tokens, latency
  percentiles and the quality summary (`experiments/ledger.py`).
- [x] `is_holdout_seed(seed)`, `seed % 10 >= 7`, so 30 percent of seeds
  are excluded from promotion decisions.
- [x] `simulation compare <baseline> <candidate>`: eight gates covering
  hallucination, duplication, both speech similarity means, secrecy
  flags, parse failures, then latency p95 and total tokens as cost
  guards, with per-metric deltas, `--json`, a holdout warning, and exit
  1 on any breach (`experiments/compare.py`).
- [x] `simulation diagnose <game_id>`: deterministic `diagnosis.md` with
  the metric summary, repeated texts, secrecy and hallucination
  samples, the most similar pair, and 1 to 3 hypotheses from a fixed
  threshold table (`experiments/diagnosis.py`).
- [x] Tests: `tests/unit/test_ledger.py` plus five CLI cases.

**Verification:** 275 tests green at `2b89f58`. Two fake long games
compare cleanly (exit 0) with all gates listed, and a breach flips the
exit code to 1.

### Phase 20: Shield, seer, dagger and the murder shortlist

Wave B of the same plan: four mechanics that the research found to be
the portable, single-use, information-bearing kind the show keeps.

- [x] Config flags `shield`, `seer`, `dagger`, `on_trial` on
  `GameSettings`, all default off and enabled only in `long_game.yaml`.
- [x] Seeded item awards after a round's first mission, cycling
  shield, dagger, seer, private to the holder via `GameState.items` and
  an `AgentView.items` line in the prompt.
- [x] Shield blocks one murder and is consumed (`SHIELD_BLOCKED`), the
  murder attempt is not refunded.
- [x] Dagger doubles the holder's vote and is spent on first use
  (`DAGGER_USED`), with a `votes.weight` column and a small
  `Database._migrate()` for pre-existing databases.
- [x] Seer: one-shot `SEER_CHECK` in `private_chat`, answered by a
  role-private message from `host` that only the holder can read.
- [x] On Trial: each living traitor nominates one player at night, the
  union is the murder shortlist (`MURDER_SHORTLIST`), and the kill must
  come from it.
- [x] Quality scoring ignores non-roster senders such as `host`.
- [x] Tests: `tests/unit/test_items.py` and additions to prompts,
  traitors, quality and models tests.

**Verification:** 307 tests green at `0df713e`. Two fake long games with
all four flags on completed end to end: 8 item awards, 6 dagger uses, a
seer check, 7 murder shortlists, one shield block, 8 recruitments, a
finale, and two different winners, with zero rejected actions.

### Phase 21: Benchmark a run against a real season

- [x] `experiments/season.py`: `load_season`, `benchmark_run`,
  `render_benchmark`, with ten weighted components (outcome 0.20,
  traitor roster 0.10, banishment alignment 0.12, murder alignment 0.10,
  traitor hit rate 0.08, survival curve 0.12, final counts 0.08, finale
  0.08, recruitments 0.06, exit order 0.06). A component with no data on
  one side scores null, is listed as skipped, and its weight is
  redistributed rather than counted as a miss.
- [x] `simulation benchmark <game_id> [--season FILE] [--json]`, writing
  `runs/<game_id>/benchmark.md` with the season id, its sources and the
  per-component method notes.
- [x] Tests: `tests/unit/test_season_benchmark.py`, twelve cases.

**Verification:** 319 tests green at `9d9f658`.

### Phase 22: Season replay with pinned traitors and cast personas

- [x] `game.traitor_names` pins the traitor roles to named players,
  validated against the cast and the traitor count, so a replay starts
  from the same information the season did; without a pin the seeded
  draw is unchanged.
- [x] `configs/seasons/the-traitors-uk-s01.yaml`: ground truth for UK
  Series 1, 22 contestants, three original traitors, the elimination
  order by episode, the two recruitment attempts, the final five, the
  faithful win, and the four places where sources disagree.
- [x] `configs/personas/uk_s01/`: 22 personas written from on-screen
  behaviour, traits plus description, one per contestant.
- [x] `configs/traitors/season_uk_s01.yaml`: the run that matches it,
  with recruitment off because the real season never refilled the tower
  by conversion, and the finale at three faithful against two traitors.

**Verification:** 322 tests green at `d9317b5`. A fake dry run of the
season config opens with alyssa, amanda and wilf as traitors and
completes.

### Phase 23: Benchmark overlaps and index-aligned eliminations

The first season benchmark scored game-008 at 0.2302 and hid the real
similarity, because it lined the run's round N up against season episode
N while the real season had two episodes with no elimination, one with
two banishments and a trial episode that replaced the murder.

- [x] Elimination-index alignment: the run's k-th banishment is paired
  with the season's k-th, episodes with nobody removed are skipped, and
  surplus eliminations on either side are excluded rather than scored.
- [x] `banishment_overlap` and `murder_overlap`: order independent set
  precision, recall and F1 over the whole game, with the shared,
  run-only and season-only names listed.
- [x] Shield-blocked attempts are not murders. They killed nobody, and
  counting them inflated the run side; they are reported as
  `blocked_attempts`.
- [x] Twelve weighted components, summing to 1.00.

**Verification:** 329 tests green. Overall alignment for game-008 rose
from 0.2302 to 0.3031, with banishment overlap 0.5714 and murder overlap
0.5556.

### Phases 24 to 27: making the events look like a real season

Four changes so the run's event flow matches how the show actually
plays, each behind a config flag defaulting off.

- [x] **Cadence (24).** `quiet_murder_rounds` and `quiet_banishment_rounds`
  skip a night or a round table, emitting `MURDER_SKIPPED` and
  `BANISHMENT_SKIPPED`. The season replay uses round 1 for no murder and
  round 8 for no banishment, mirroring Series 1, and the long game never
  murders on night one.
- [x] **Endgame vote (25).** `endgame_vote` turns the finale into the
  show's loop: a unanimous `end` finishes the game, any single `banish`
  forces another banishment, and it auto-ends at two players. Parity no
  longer ends the game during such a finale. `blind_finale_banishments`
  hides finale-time banishments from living players until the game ends.
- [x] **Recruitment as a choice (26).** `recruit_choice` makes the
  traitors vote recruit or murder, with a tie falling to murder; the
  chosen target answers `accept` or `decline`; a decline wastes the
  night, unless the offer came from a lone traitor, in which case it is
  an ultimatum and the target is murdered instead.
- [x] **Endgame trigger (27).** `finale_total` starts the finale at that
  many living players whatever the split, because the faction pair
  cannot fire once recruitment keeps the traitor count topped up. The
  season replay uses five, as the show does. A configured endgame
  (`finale_total` or `endgame_vote`) replaces the parity win outright:
  parity closed the season replay (game-009) at three traitors against
  three faithful with six alive, one player short of the final five, so
  the finale never opened. Extinction still ends the game whatever is
  configured; only parity is dropped.

**Verification:** 384 tests green. A fake season game reaches the finale
at five alive, runs end votes, and ends by auto-end at two players, with
the faithful having banished three of their own while holding a four to
one majority. With a three-traitor cast, the same game now plays through
three against three at six alive and on to the final five instead of
being called there by parity.

### Phase 28: Hosted debate clock (audit fix, phase 1)

First of the five phases approved with
`docs/2026-09-28-rounds-vs-season-audit.md`: a deterministic host
(engine code, not an LLM player) paces timed discussion instead of the
cold one-message-each round table.

- [x] Config `discussion_budget` (open speaking turns per debate, 0
  disables the clock and keeps the old phases unchanged) and
  `warning_turns` (closing waves after the host warns), both on
  `GameSettings` with the clock off by default.
- [x] `HOST_WARNING` (actor `host`, payload `turns_left`) fires when the
  open turns run out; `DEBATE_CLOSED` (`forced_vote: true`) when the
  closing turns are spent and the vote is forced. Both render in
  `transcript.txt`.
- [x] `PublicDiscussionPhase` spends the budget in round-robin waves
  (even quota per player, deterministic cut when it does not divide),
  then runs `warning_turns` closing waves for everyone.
- [x] One timer line per turn reaches the prompt through
  `PhaseContext.ask(..., extra_instruction)`; the two production
  callbacks forward it to `AgentRuntime.decide`, and scripted
  three-argument callbacks keep working untouched (the context probes
  the signature once).
- [x] `public_message_limit(config, alive_count)` in `engine/rules.py`
  lets the validator accept exactly the scheduled closing turn while
  still capping the phase; the baseline limit is unchanged with the
  clock off.

**Verification:** 396 tests green. New `tests/unit/test_host_debate.py`
covers the clock off (byte-identical one-turn-each, no host events), the
full open/warn/close sequence and ordering, the budget cut, the
round-robin continuation, the timer lines, three-argument callbacks, the
validator limit with and without the clock, quiet-round coexistence, the
rendered transcript, and a full scripted game. A fake run of the basic
config with the clock on produced 4 warnings, 4 closes, 44 public
messages and zero rejected actions across all seven artifacts.

### Phase 29: Round-table nomination, rebuttal, revote (audit fix, phase 2)

Second of the five phases approved with
`docs/2026-09-28-rounds-vs-season-audit.md`: the round table plays like
the show's. Somebody is named, the accused answers, and a table that
cannot decide votes again on those suspects only.

- [x] Config `nomination_enabled` and `revote_enabled` on
  `GameSettings`, both default off; with them off the ballot is exactly
  what it was.
- [x] New `REBUT` action (16 action types now, `test_models` updated),
  `ACCUSE`/`REBUT` riding the public channel, and the secrecy rule
  rewritten so naming a suspect with a reason is allowed while roles
  stay hidden. The prompt carries one line for the nomination and one
  for the defence.
- [x] `RoundTablePhase`: debate, then one `ACCUSE` from everyone, then
  `tally_accusations(keep)` emitting `NOMINATION_TALLY` with counts and
  the accusations behind them, then one `REBUT` from each nominee before
  the vote. With nomination off the phase is the old plain debate.
- [x] Restricted revote: a tied ballot opens `REVOTE_CALLED` among the
  tied suspects only (nobody tied may vote, one ballot each),
  `REVOTE_RESOLVED` reports the counts, and `resolve_votes` banishes
  the revote's winner. A revote that ties again banishes nobody rather
  than recording a dead round, and when every living player is tied the
  restriction drops so the ballot cannot stall on itself.
- [x] `pending_banishment()` says who is about to leave: the first
  ballot's leader, or the settled revote's winner. `EliminationPhase`
  asks it before resolving, so a traitor whose exit was decided by the
  revote still gets the recruit-or-go choice.
- [x] Rendering on both surfaces: `transcript.txt` narrates
  "Host: nominations tallied", "face a revote" and who is banished, and
  the live progress printer has the matching host lines.

**Verification:** 413 tests green. New
`tests/unit/test_round_table.py` (16 tests) covers nomination off, the
tally and its `keep` rule, the secrecy carve-out for a reasoned
accusation, the defence turn, the restricted ballot and its target
rules, the double tie, recruitment after a revote, and a full scripted
game with both flags on. The fake prompt provider was part of the fix:
it answers `accuse`/`rebut` with a reason and reads the action type and
legal targets from the original prompt, so a correction retry cannot
lose them. A fake six-player run with both flags on produced 10
nominations, 4 defences, 2 nomination tallies and zero rejected actions
across all seven artifacts.

### Phase 30: Sequential traitor council (audit fix, phase 3)

Third of the five audit phases: the traitors now decide the night's kill
the way the show does it - one proposal at a time in turn, then
everybody holds or switches from the same moment.

- [x] Config `council_deliberation` on `GameSettings`, default off; with
  it off the night is the old single concurrent ballot. A lone traitor
  skips the council entirely (there is nobody to argue with), and a
  recruit night keeps the old free-for-all message line, because
  arguing recruit versus murder is all that round is for.
- [x] `TraitorNightPhase` runs two rounds when the flag is on and more
  than one traitor is alive. Round 1 asks one traitor at a time and
  freezes each pick as a `COUNCIL_PROPOSAL` on the traitor channel, so
  the second and third traitor write having read the others instead of
  guessing at them. The blind `TRAITOR_MESSAGE` free-for-all is dropped
  in this mode; the proposal is the argument.
- [x] Round 2 collects every final pick from that same moment - a
  ballot, not a bandwagon - with each host line naming every proposal
  and that traitor's own. `resolve_night` counts it, so the majority of
  the final picks wins and a tie still falls back to the earliest
  submission, which is now a stated outcome rather than a silent
  artefact of submission order.
- [x] The outcome record carries the switches, the dissent and whether
  the council was unanimous, and both surfaces render it:
  `transcript.txt` narrates "charlie proposes bob" and the verdict line
  with the picks, and the live progress printer has the matching lines.

**Verification:** 419 tests green. New `tests/unit/test_council.py` (6
tests) covers round 1 running one traitor at a time in id order with the
earlier proposals in each later host line and no blind message, the
proposals landing on the traitor channel and reaching no faithful, each
round-2 line naming every proposal and that traitor's own, a switch
moving the pick, the unanimous case and the lone-traitor skip. A fake
nine-player run with the flag on produced 5 council proposals, 2 council
verdicts and zero rejected actions across all seven artifacts.

### Remaining audit-fix phases (31 to 32): scope and estimate

Two phases left from
`docs/2026-09-28-rounds-vs-season-audit.md`, on the same rhythm as phases
28 to 30 (one session per phase, tests and commit inside the session).
These are effort, not calendar, commitments. They were approved to run
back to back, without a checkpoint between them.

| Phase | Audit item | Scope | Estimate |
|-------|-----------|-------|----------|
| 31 | Audit fix, phase 4 | Private chats carry the pair's own recent messages in the prompt; per-pair counters in the quality metrics | 1 session (small: projector or prompt window plus a metrics block) |
| 32 | Audit fix, phase 5 | Conflict personas (assertiveness and tunnel-vision weights from the Series 1 ground truth) plus clash metrics: accusation rate, revote stubbornness, council switch rate, traitor cohesion | 1 to 2 sessions (largest surface: 22 persona files plus new metrics and tests) |

Total: 2 to 3 sessions after phase 30, running back to back, so the
audit fix lands within roughly a working week. Live Ollama verification
runs (a fake-backend run per phase, plus one season replay for the
benchmark) are included in each session's estimate; a full 4-hour
season replay is not.

------------------------------------------------------------------------

## Later milestones (tracked, not yet scheduled)

- **Milestone 3** (spec §35): batch 10-100 games, aggregate winner,
  placement, survival time, vote counts, message counts, public/private
  ratio, betrayals, alliance events.
- **Milestone 4** (spec §36): relationship tracking live, structured
  beliefs live, better memory, Langfuse, full replay tooling.
- **Milestone 5** (spec §37): providers beyond Ollama (OpenAI,
  Anthropic, Gemini, OpenRouter) behind the same gateway.

------------------------------------------------------------------------

## Change log

| Date | Change |
|------|--------|
| 2026-09-26 | Repo initialized (`main`), specs committed, progress doc created, phase plan defined (Phases 0-12). |
| 2026-09-26 | Phase 1 complete: skeleton, pyproject, configs, personas, smoke test (1 passed). |
| 2026-09-26 | Phase 2 complete: domain models (state, actions, channels, events), 12 unit tests passed. |
| 2026-09-26 | Phase 3 complete: JSONL event log, SQLite schema + 11 repositories, snapshots; 19 tests passed. |
| 2026-09-26 | Phase 4 complete: game engine, rule validator, generic phase engine, config loader; 47 tests passed. |
| 2026-09-26 | Phase 5 complete: message router, structural visibility, InformationProjector, security leakage tests; 60 tests passed. |
| 2026-09-26 | History rewritten to purge accidentally staged `ollama-model/*.gguf` (2.6 GB -> 192 KB); phase 2-5 hashes changed, dir gitignored. |
| 2026-09-26 | Phase 6 code complete: persona/goals/beliefs/relationships/memory/prompt builder/action parser; 88 tests passed. Agent decision loop deferred to Phase 7 (needs LLM gateway). |
| 2026-09-27 | Phase 7 complete: LLMProvider protocol, ModelConfig, LLMGateway concurrency cap, FakeLLMProvider, AgentRuntime decide loop with correction retries; 99 tests passed. |
| 2026-09-27 | Phase 8 complete: TraitorsEnvironment, all 7 phases, legal-target rules, votes/eliminations persisted; 105 tests passed. |
| 2026-09-27 | Phase 9 complete = MILESTONE 1: 4-agent and 6-player full games on fake backend, deterministic replay, leakage audit over 42 observed views; 110 tests passed. |
| 2026-09-27 | Phase 10 complete: OllamaProvider (JSON schema output, error handling, `think` mapping), config-driven model selection with `base_url`/`options`; 130 tests passed. `configs/traitors/basic.yaml` model details (`hauhau-qwen:latest`) intentionally left uncommitted. |
| 2026-09-26 | Phases 11 and 12 complete = MILESTONE 2: CLI `run`/`batch`/`list-games`, per-run artifact tree with experiment identity, `replay`/`inspect`/`snapshot`, no-op observability tracer; 179 tests passed; `basic.yaml` now tracked with its model block. |
| 2026-09-26 | Phase 13 complete (extends the 0-12 plan): traitor recruitment on banishment (`RECRUIT` action, `ROLE_RECRUITED` event, `recruit_on_banish`/`max_recruits` config); 197 tests passed. Machine-local configs excluded via `*.local.yaml` in `.gitignore`. |
| 2026-09-26 | Phase 14 complete: finale at exactly 3 traitors and 3 faithful with rapid-fire voting, seeded solo/team ambitions, solo and team win reporting; 212 tests passed. |
| 2026-09-27 | Fix after the first live run died: Ollama calls now retry transient failures (timeout, unreachable host, 5xx/429, garbage body) with exponential backoff, `llm.retries` is config-driven and reported in `metrics.json`, and the long game allows 300s per call for cold model loads; 217 tests passed. |
| 2026-09-27 | Fix: traitors were announcing themselves in the first public message (both live runs). The prompt had no secrecy rule, `AgentView.render()` printed `Known roles:` as if it were public, and the alliance paragraph invited allying with the faithful. Now both prompts carry a hard secrecy rule, the public-message turn repeats it, and role knowledge is labelled private; verified against the live model; 222 tests passed. |
| 2026-09-27 | Phase 15 complete: per-call telemetry (`llm_calls.jsonl`), `llm` metrics with input/output tokens and latency percentiles, deterministic `quality` block (hallucination, secrecy, diversity, parsing), `simulation metrics` command with `--recompute`; 250 tests passed. |
| 2026-09-27 | Phase 16 complete: concurrent turns within a phase (3.4x measured throughput at concurrency 4) and a bounded transcript window per prompt (31k evaluated tokens per call was the main cost); 253 tests passed. |
| 2026-09-27 | Fix: dropped connections are now retryable; game-005 and game-006 both died when the remote host stopped answering because `RemoteDisconnected` is an `OSError` and never reached the retry loop; 256 tests passed. |
| 2026-09-27 | Fix: message duplication. game-005 had 153 of 273 messages as exact duplicates (one line shared by 89), public prompts were near-identical across players and agents copied what they could see; added an Originality hard rule plus per-action reminders; 256 tests passed. |
| 2026-09-27 | Phase 17 complete: duplication score and cross-player speech similarity (content words and trigrams, per pair and per player) in the quality block and the metrics command; 261 tests passed. |
| 2026-09-27 | Phase 18 complete: traitor night council on a role-private channel plus strategy guidance for the kill, the round table and the vote; secrecy scoring skips traitor-only traffic; 266 tests passed. |
| 2026-09-27 | Research: two docs-researcher briefs on the worldwide Traitors format and on metric-driven prompt loops, condensed into docs/2026-09-27-traitors-format-adoption-and-self-improvement.md. |
| 2026-09-27 | Phase 19 complete: score ledger, holdout seeds, `simulation compare` gates and `simulation diagnose`; 275 tests passed. |
| 2026-09-27 | Phase 20 complete: shield, seer, dagger and the murder shortlist behind default-off config flags, enabled in long_game.yaml; 307 tests passed. |
| 2026-09-27 | Phase 21 complete: season benchmark with ten weighted components and a `benchmark` command writing `benchmark.md`; 319 tests passed. |
| 2026-09-27 | Phase 22 complete: pinned traitors, 22 UK Series 1 personas, season ground truth and the season_uk_s01 config; 322 tests passed. Live season run started as game-008. |
| 2026-09-28 | Season replay game-008 finished: traitor win, wilf alone against one faithful, 841 calls, 4h 33m, hallucination 0, duplication 0.433. Benchmark against UK Series 1: overall 0.2302 positional, 0.3031 after the overlap work. |
| 2026-09-28 | Phase 23 complete: benchmark set overlaps, elimination-index alignment and shield-blocked attempts excluded from murders; 329 tests passed. |
| 2026-09-28 | Phases 24 to 27 complete: seasonal cadence (no murder on night one, quiet rounds), the end-or-banish endgame vote with blind finale banishments, recruitment as a choice with a lone-traitor ultimatum, and a finale trigger based on the living count; 378 tests passed. |
| 2026-09-28 | Fix: a configured endgame (`finale_total` or `endgame_vote`) replaces the parity win. The season replay (game-009) reached three traitors against three faithful with six alive and parity closed it there, one player short of the final five, so the finale never opened; the count trigger also fires below its number now, for a cast that cannot land on it. The season config on the fake backend now reaches the finale at its final five (wilf against four faithful) and ends by end vote. The season config's `max_recruits: 2` is documented as intended. 384 tests passed. |
| 2026-09-28 | Audit written (`docs/2026-09-28-rounds-vs-season-audit.md`): season replays versus UK Series 1, clash-free discussion, private chats echoing the public transcript, and every decision point that lacks a tie-breaker; five-phase fix plan approved. |
| 2026-09-28 | Phase 28 complete (audit fix, phase 1): deterministic host with a debate clock, `HOST_WARNING`/`DEBATE_CLOSED` events, `discussion_budget`/`warning_turns` config, a per-turn timer line in prompts via `extra_instruction`, and a validator limit that admits the closing turn; clock off by default, 396 tests passed. |
| 2026-09-29 | Phase 29 complete (audit fix, phase 2): hosted round table with `ACCUSE`/`REBUT`, a `NOMINATION_TALLY` from everyone's accusation (`keep` nominations on top), a restricted revote on a tied ballot whose second tie bansishes nobody, the secrecy carve-out for a reasoned accusation, and `pending_banishment()` so the recruit choice still reaches a traitor whose exit the revote decided; `nomination_enabled`/`revote_enabled` default off, fake provider fixed to play the phase; 413 tests passed. |
| 2026-09-29 | Phase 30 complete (audit fix, phase 3): sequential traitor council behind `council_deliberation` (default off) - round 1 proposes one traitor at a time as a `COUNCIL_PROPOSAL` on the traitor channel so each writes having read the others, round 2 takes a concurrent hold-or-switch ballot where the majority of final picks wins, and the outcome records switches, dissent and unanimity on both render surfaces; a lone traitor and a recruit night skip it; 419 tests passed. |
