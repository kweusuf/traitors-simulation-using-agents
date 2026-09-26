# Project Progress: LLM Social Simulation Framework

**Source of truth:** `02-framework-requirements-and-scaffolding-spec.md`
**Status:** MILESTONE 1 REACHED (110 tests green) - awaiting go-ahead for Phase 10 (Ollama)
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
| 6 | Agent runtime: persona, memory, beliefs, relationships, prompts | pending | |
| 7 | LLM gateway and FakeLLMProvider | complete | `5d5a14b` |
| 8 | Traitors environment (phases, rules, win conditions) | complete | `10607b5` |
| 9 | Deterministic test suite (unit, security, integration) | complete | `pending` |
| 10 | OllamaProvider | complete | `2c27c8e` (code), `pending` (this doc) |
| 11 | CLI, config-driven run, run artifacts | pending | |
| 12 | Replay, inspect, list-games | pending | |

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
- [ ] Persona as YAML data with trait-to-instruction translation
  (spec §12).
- [ ] Goals separated from personality, role goals injectable (spec §13).
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
  `ModelConfig`. Current model is `hauhau-qwen:latest`; its details
  live in `configs/traitors/basic.yaml`, which is deliberately left
  uncommitted for now.
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

- [ ] CLI commands: `run <config>`, `batch <config> --games N`,
  `replay <game_id>`, `inspect <game_id>`, `list-games` (spec §31).
- [ ] `python -m simulation run configs/traitors/basic.yaml` produces a
  complete game (spec §43).
- [ ] Run artifacts per game under `runs/<game_id>/`: `config.yaml`,
  `events.jsonl`, `transcript.json`, `metrics.json` (spec §34, §43).
- [ ] Experiment identity recorded: `experiment_id`, `game_id`,
  `random_seed`, `model`, `model_parameters`, `prompt_version`,
  `persona_version`, `game_rules_version`, `memory_strategy`
  (spec §27).
- [ ] Batch runner with deterministic per-game seeds (spec §26).
- [ ] Optional Langfuse flag wired as no-op when disabled (spec §28).

**Verification:** fake-backend run from the CLI produces the full output
tree from spec §43; one real Ollama run documented.

**Milestone 2 gate.**

### Phase 12: Replay, inspect, list-games

- [ ] `simulation replay <game_id>` reconstructs visible state from
  events (spec §25).
- [ ] `simulation inspect <game_id> --agent alice` shows an agent's
  projected view.
- [ ] `simulation snapshot <game_id> --round 3` (from snapshots stored
  in Phase 3).
- [ ] Tests: replay determinism (same events -> same reconstructed
  state).

**Verification:** `pytest tests/unit/test_replay*.py` passes; CLI replay
of an integration-test game matches its recorded final state.

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
