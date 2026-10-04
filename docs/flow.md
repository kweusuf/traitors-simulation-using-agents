# Code flow

How a run actually moves through the codebase, and where control branches.
Written from the call chain, not from the directory listing - if this and the
code disagree, the code is right.

Decisions and their reasoning live in [decisions.md](decisions.md).
Measurements live in [learnings.md](learnings.md).

---

## Entry point

```
python -m simulation run configs/traitors/<arm>.yaml --game-id <id>
```

`__main__.py` → `cli/main.py:main()` → argparse dispatch via `args.func`.

Ten subcommands, each a thin `cmd_*` function: `run`, `batch`, `replay`,
`inspect`, `snapshot`, `metrics`, `list-games`, `compare`, `diagnose`,
`benchmark`. Only `run` and `batch` start a game; the rest read what a
finished run already wrote.

```
cli/main.py:cmd_run
  └─ experiments/runner.py:GameRunner.run
       └─ .run_locked                    # the real body
```

---

## The run, top to bottom

```
GameRunner._run_locked                       runner.py:203
│
├─ if resume:                                 # every refusing check happens
│    _resume_identity(run_dir, seed, id)      # BEFORE the directory is touched
│    load_point(...)  -> (round, phase_index)
│    point.recover_ambitions(seed, players, traitors)
│
│    # TelemetryRecorder truncates llm_calls.jsonl on construction, so it is
│    # built only now that the resume point is settled.
│
├─ EventSink(game_id, events.jsonl, db, observer)
├─ TraitorsEnvironment(config, sink, db, seed)
│    ├─ if point: engine.restore_resume(point); sink.continue_after(last_seq)
│    └─ else:    env.initialize()
│
├─ _build_agents(env)          -> dict[player_id, Agent]
├─ build_provider(config.llm)  -> Ollama | Fake | PromptScript
├─ LLMGateway(provider, max_concurrency=config.llm.max_concurrency)
├─ TelemetryRecorder(..., resume=point is not None)
├─ AgentRuntime(agents, gateway, model_config, prompt_builder, telemetry,
│               memory_*, reject_invented_players, reject_repetition, ...)
├─ PhaseContext(engine, config, request_action=self._callback(...))
│
├─ asyncio.run(PhaseEngine(...).run(context, resume_at))
│
├─ events = EventLog(events_path).read_all()      # read back from disk
├─ _build_metrics(...)  -> dict
├─ append_ledger(runs_dir, metrics)                # spans runs
├─ _write_artifacts(state, events, metrics)
└─ _persist_identity(...)
```

Everything after `asyncio.run` reads the **persisted log**, not in-memory
state, so artifacts describe what was actually written.

---

## The phase loop

```
PhaseEngine.run                                phase_engine.py:113
│
├─ engine.start()                    # deals roles; refuses if already started
│
├─ for _ in range(config.game.max_rounds):
│    ├─ BREAK if engine.is_over or state.finale
│    ├─ engine.start_round()
│    ├─ start = resume_phase if opened==0 and round matches resume, else 0
│    └─ for name in self.order[start:]:         # config.game.phases
│         ├─ BREAK if engine.is_over or state.finale
│         ├─ engine.begin_phase(phase)
│         ├─ await phases[name].run(context)     # ← everything happens here
│         └─ engine.end_phase()
│
├─ if state.finale: _run_finale(context)
│     ├─ endgame_vote?  -> _run_endgame_finale   # end-or-banish per player
│     └─ else            -> _run_finale          # rapid-fire rounds
│           stalled >= finale_max_votes -> apply_round_limit("finale_vote_limit")
│
├─ engine.apply_round_limit()        # if still not over
└─ engine.finish()
```

**Branching conditions worth knowing:**

- The round budget is **fresh from the resume point**, so a crash does not
  cost the game the rounds it ate.
- `is_over` is checked before *every* phase, so a game can end mid-round.
- The finale replaces the normal phase order entirely: no missions, no night
  murder, no recruitment.
---

## One agent turn

This is the hot path. `PhaseContext.ask` → the runner's callback →
`AgentRuntime.decide`.

```
runner.py:_callback(env, runtime, agents)          runner.py:392
│
├─ _sync_roles(env, agents)          # roles can change (recruitment)
├─ _sync_memory(env, agents)         # drain NEW events into every observer's
│    │                               #   memory; on first call, replay the
│    │                               #   whole persisted log
│    ├─ writes_for(state, event, history,
│    │            remember_everything=pointer_memory)
│    │      └─ for each player the write is visible_to:
│    │            agent.remember(content, kind, round, sequence, subjects,
│    │                          salience, confidence=writer_confidence,
│    │                          decayable=not durable)
│    └─ _bump_relationships(...)     # animosity from nominations and ballots
│
├─ view = env.observe(agent_id)      # the ONLY thing this player can see
└─ runtime.decide(agent_id, view, action_type, legal_targets, extra)
```

### `AgentRuntime.decide` - the decide loop

```
runtime.py:126
│
├─ agent = agents[agent_id]
├─ messages = [build_system(...), build_user(view, action_type, ...)]
│              └─ memory_items appended when memory_enabled
│
├─ for attempt in range(max_retries + 1):        # 3 by default
│   │
│   ├─ gateway.generate(messages, action_schema(...), model_config)
│   │    └─ semaphore(max_concurrency) → provider
│   │         └─ Ollama /api/chat  |  Fake  |  PromptScript
│   │    └─ EXCEPTION → record, re-raise           # transport failures
│   │
│   ├─ parse_action(response.content, agent_id)   # LAYER 1
│   │    ├─ bad JSON / not an object / bad schema → ActionParseError
│   │    ├─ actor_id ALWAYS overwritten
│   │    └─ target cleared for targetless actions
│   │
│   ├─ resolve_target(action, legal_targets)      # repair, don't refuse
│   │    └─ 'Meryl' → 'matt' if exactly one edit away
│   │
│   ├─ resolve_content_names(content, roster, context)
│   │    ├─ repairs applied to the text
│   │    └─ phantoms recorded
│   │         └─ if reject_invented_players: ActionParseError    # LAYER 3
│   │
│   ├─ _repetition_reason(view, action, action_type)             # LAYER 3
│   │    ├─ off by default
│   │    ├─ exempt: votes, end_vote, recruit_decision/response
│   │    ├─ history: own messages ("self") or everyone's ("room");
│   │    │           private messages use private history only
│   │    └─ find_repeat(content, history, threshold, min_words)
│   │         └─ on the LAST attempt: recorded but ALLOWED through
│   │
│   ├─ check_action_constraints(action, {type}, legal_targets)   # LAYER 2
│   │
│   ├─ return action                                   ← success
│   │
│   └─ ActionParseError:
│        ├─ _record_call(ok=False, error=reason)
│        ├─ if last attempt: raise ActionParseError  # turn is lost
│        └─ messages += [assistant(rejected), user(correction + hint)]
```

The rejected attempt is already in `messages` before the correction is
appended. That is why the repetition gate can quote the model's own text
back to it without any extra plumbing.

### Schema shape

`action_schema(action_type, want_gist, gist_required)` builds the grammar the
---

## Landing the action

```
GameEngine.submit_action(action)                game_engine.py:304
│
├─ _validate(action) -> RuleValidator.validate   engine/rules.py:113
│    1. phase correctness        (not allowed in this phase)
│    2. actor known and alive
│    3. role legality           (only traitors kill/recruit/nominate, etc.)
│    4. endgame vote             (enabled, finale running, end|banish)
│    4c. round table             (nomination/rebuttal targets)
│    4d. restricted revote       (tied suspects only; not your own vote)
│    4b. recruitment choice      (window open, offered player answers)
│    5. target checks            (known, alive, not self, role-appropriate)
│    6. per-agent phase limits
│    └─ NOT ok → _reject() → ACTION_REJECTED event, return. NO retry.
│
├─ _spend(action)                     # charge against the phase limit
├─ dispatch: PUBLIC_MESSAGE → _record_message
│            ACCUSE/REBUT    → recorded on the public record
│            VOTE            → dagger weight 2 if held
└─ ... (kill, recruit, seer, and the rest)
```

**This layer never retries.** The world moved; re-asking would only invite
another illegal answer. See
[2026-10-05-rejection-paths.md](2026-10-05-rejection-paths.md).

---

## Visibility

```
InformationProjector.project(state, agent_id) -> AgentView
```

`AgentView` carries `own_role`, `known_roles`, `alive_players`,
`eliminated_players`, `public_transcript`, `private_conversations`. An agent
**cannot read another agent's view** - there is no path that would let it,
which is why memory writes are filtered by `write.visible_to` rather than
trusted to be correct.

---

## Phases

`environments/traitors/phases.py` implements one class per phase:
`MissionPhase`, `PublicDiscussionPhase`, `RoundTablePhase`, `PrivateChatPhase`
(two waves, reply routing), `VotingPhase`, `EndVotePhase`, `EliminationPhase`,
`TraitorNightPhase`, plus the council phases.

Each satisfies `Phase.run(context)` and calls `context.ask(...)`, which routes
to the callback above. A phase decides *who is asked and what is legal*; it
never talks to a model directly.

Order comes from `config.game.phases`, so reordering the game is a config
change.

---

## Modules

| Path | Responsibility |
| --- | --- |
| `cli/main.py` | argparse entry, subcommands |
| `experiments/config.py` | YAML → validated settings (`StrictModel`) |
| `experiments/runner.py` | build everything, run, resume, artifacts |
| `experiments/telemetry.py` | per-call records → `llm_calls.jsonl` |
| `experiments/resume.py` | rebuild a run point from the event log |
| `experiments/lock.py` | per-run exclusive lock, records owning pid |
| `experiments/ledger.py` | cross-run comparison append |
| `experiments/compare.py`, `quality.py`, `diagnosis.py`, `season.py` | analysis |
| `agents/runtime.py` | the decide loop; where all gates live |
| `agents/prompts.py` | `PromptBuilder` - system and user prompts |
| `agents/agent.py`, `beliefs.py`, `relationships.py` | per-player state |
| `memory/short_term.py`, `long_term.py` | decay, salience floor, durable facts |
| `actions/validator.py` | parse, repair, constraint pre-flight |
| `actions/repetition.py` | pure similarity, no I/O |
| `actions/actions.py` | the `Action` model and schema hook |
| `engine/game_engine.py` | submit, spend, dispatch, win checks |
| `engine/rules.py` | authoritative rule validation |
| `engine/phase_engine.py` | the round/finale loop |
| `engine/state.py` | game state, roles, items |
| `communication/visibility.py` | `AgentView`, the projector |
| `communication/router.py` | who receives which channel |
| `environments/traitors/` | phases, memory writes, rules |
| `models/ollama.py`, `fake.py`, `gateway.py` | providers and concurrency cap |
| `persistence/` | event log, SQLite, repositories, sink |

---

## Artefacts per run

```
runs/<game_id>/
  events.jsonl        append-only truth; resume and metrics read this back
  llm_calls.jsonl     per-call: prompt chars, latency, ok, error
  game.db             SQLite for this run only
  config.yaml         captured after the run
  .run.lock           owning pid, so a killed run is recoverable
```

`runs/ledger.jsonl` spans runs; artifacts do not.
provider is handed. `Action.__get_pydantic_json_schema__` rewrites
`required` wholesale, so `gist_required` must be handled inside that hook,
not by redeclaring the field.