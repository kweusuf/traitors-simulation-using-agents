# LLM Social Simulation Framework

## Initial Technical Requirements and Scaffolding Specification

**Document purpose:** This document is intended to be given directly to
Cursor or another coding agent as the basis for creating the first
implementation scaffold.

**Primary objective:** Build a small, general-purpose,
configuration-driven framework for running multi-agent LLM social
simulations. The first environment is a text-only game inspired by *The
Traitors*. The architecture must remain generic enough to support Mafia,
Werewolf, Avalon, Big Boss-style simulations, negotiation games, trust
games, and other social experiments later.

------------------------------------------------------------------------

# 1. Product Definition

Build a framework in which multiple LLM-backed agents interact inside a
deterministic game environment.

Agents have:

-   Persistent identities.
-   Configurable personalities.
-   Roles.
-   Goals.
-   Private knowledge.
-   Agent-specific memory.
-   Beliefs.
-   Relationships.
-   Access to public and private communication channels.
-   A model backend.

The framework has a deterministic game engine that owns the canonical
world state and controls information visibility.

The LLM must never be the source of truth for game state.

------------------------------------------------------------------------

# 2. MVP Scope

Implement a CLI-first MVP.

### Game

-   6 players.
-   2 Traitors.
-   4 Faithful.
-   5 rounds maximum.
-   Text only.
-   Public discussion.
-   Private communication.
-   Round-table discussion.
-   Voting.
-   Elimination.
-   Traitor night phase.
-   Basic mission abstraction.
-   Win conditions.

### Agent

-   ID/name.
-   Role.
-   Personality.
-   Goals.
-   Private knowledge.
-   Memory.
-   Beliefs.
-   Relationship state.
-   LLM backend.
-   Legal-action selection.

### Persistence

Use SQLite initially.

Store:

-   Games.
-   Agents.
-   Events.
-   Messages.
-   Votes.
-   Eliminations.
-   Game snapshots.
-   Configuration.
-   LLM metadata.

Also write an append-only JSONL event log for easy debugging and replay.

### Model backend

First-class support for Ollama.

Use a provider abstraction so cloud providers can be added later.

Do not couple game logic directly to Ollama.

------------------------------------------------------------------------

# 3. Non-Goals for MVP

Do not implement initially:

-   Web UI.
-   Voice.
-   Images.
-   Complex browser interaction.
-   Human multiplayer.
-   Large populations.
-   Distributed execution.
-   Reinforcement learning.
-   Fine-tuning.
-   Complex vector databases.
-   Automatic personality generation.
-   Advanced analytics dashboards.
-   Multi-game persistent identity.

These can come later.

------------------------------------------------------------------------

# 4. Architecture

Use these conceptual layers:

``` text
Application
    |
Experiment Runner
    |
Game Engine
    |
Information / Visibility Layer
    |
Agent Runtime
    |
Memory / Beliefs / Relationships
    |
LLM Gateway
    |
Ollama
```

Suggested package structure:

``` text
src/
  simulation/
    engine/
      game_engine.py
      phase_engine.py
      rules.py
      state.py

    agents/
      agent.py
      persona.py
      goals.py
      beliefs.py
      relationships.py

    memory/
      memory.py
      short_term.py
      long_term.py

    communication/
      router.py
      channels.py
      visibility.py

    actions/
      actions.py
      validator.py

    models/
      base.py
      ollama.py

    persistence/
      database.py
      repositories.py
      event_log.py

    experiments/
      runner.py
      config.py

    environments/
      traitors/
        game.py
        rules.py
        phases.py

    cli/
      main.py

tests/
configs/
  traitors/
    basic.yaml
```

The exact naming can change, but keep the separation of concerns.

------------------------------------------------------------------------

# 5. Game Engine

The Game Engine is the authoritative source of truth.

Responsibilities:

-   Maintain game state.
-   Manage phases.
-   Manage rounds.
-   Assign roles.
-   Enforce rules.
-   Validate actions.
-   Route legal actions to the appropriate state transition.
-   Determine visibility.
-   Execute eliminations.
-   Determine win conditions.
-   Emit events.
-   Create state snapshots.

The game engine should not generate natural language.

------------------------------------------------------------------------

# 6. Game State

Use explicit typed models.

Example:

``` python
class GameState:
    game_id: str
    round_number: int
    phase: GamePhase
    players: dict[str, PlayerState]
    alive_players: set[str]
    eliminated_players: set[str]
    roles: dict[str, Role]
    votes: dict[str, str]
    missions: list[MissionState]
    winner: Optional[str]
    winning_team: Optional[str]
```

Never allow an agent object to directly mutate global state.

All state changes should go through engine commands/actions.

------------------------------------------------------------------------

# 7. Phase System

Create a generic phase abstraction.

Example:

``` python
class Phase(Protocol):
    async def run(self, context: PhaseContext) -> PhaseResult:
        ...
```

Initial phases:

``` text
MISSION
PUBLIC_DISCUSSION
PRIVATE_CHAT
ROUND_TABLE
VOTING
ELIMINATION
TRAITOR_NIGHT
GAME_END
```

The exact ordering should be configurable.

------------------------------------------------------------------------

# 8. Action Model

Agents should produce structured actions.

Example:

``` json
{
  "action": "private_message",
  "target": "alice",
  "content": "I think Bob is hiding something.",
  "confidence": 0.73
}
```

Define an enum:

``` python
class ActionType(Enum):
    PUBLIC_MESSAGE = ...
    PRIVATE_MESSAGE = ...
    VOTE = ...
    TRAITOR_KILL = ...
    ACCUSE = ...
    DEFEND = ...
    SHARE_INFORMATION = ...
    WITHHOLD_INFORMATION = ...
```

MVP can start with only:

``` text
PUBLIC_MESSAGE
PRIVATE_MESSAGE
VOTE
TRAITOR_KILL
```

The engine validates:

-   Is the agent alive?
-   Is the target alive?
-   Is the phase correct?
-   Is the target reachable through this channel?
-   Is the action legal for this role?
-   Is the action within the phase limits?

------------------------------------------------------------------------

# 9. Communication System

Implement a message router.

Channels:

``` text
PUBLIC
PRIVATE
ROLE_PRIVATE
SYSTEM
```

Visibility should be explicit.

Example:

``` python
Message(
    sender_id="bob",
    recipients=["eve"],
    channel=Channel.PRIVATE,
    content="Alice suspects me.",
)
```

The recipient receives the message.

Agents who are not recipients must never see it.

Do not rely on prompts alone to enforce privacy.

The application must enforce it structurally.

------------------------------------------------------------------------

# 10. Information Projection

This is one of the most important components.

The global game state should be transformed into an **agent-specific
view**.

Example:

``` python
agent_view = information_projector.project(
    game_state,
    agent_id="alice",
)
```

Alice might see:

``` text
Role: Faithful
Known Traitor: Bob
Alive players: ...
Public transcript: ...
Private conversations: ...
```

Charlie might see:

``` text
Role: Faithful
Known Traitors: none
Alive players: ...
Public transcript: ...
Private conversations: ...
```

The projection layer must guarantee that hidden information is never
accidentally included.

Add tests specifically for information leakage.

------------------------------------------------------------------------

# 11. Agent Runtime

An Agent should be a stateful object.

Conceptually:

``` python
class Agent:
    identity
    persona
    role
    goals
    memory
    beliefs
    relationships
    model
```

The Agent Runtime should:

1.  Receive an agent-specific observation.
2.  Retrieve relevant memory.
3.  Construct an LLM input.
4.  Ask the model to choose an action.
5.  Parse structured output.
6.  Validate output.
7.  Return the action to the engine.

The agent does not directly execute the action.

------------------------------------------------------------------------

# 12. Persona

Persona should be data, not hard-coded prompt text.

Example:

``` yaml
personality:
  analytical: 0.9
  assertiveness: 0.5
  sociability: 0.4
  trust: 0.3
  risk_tolerance: 0.4
```

The prompt builder translates this into behavioral instructions.

Avoid personality descriptions that directly force a strategy.

Bad:

``` text
You always lie.
```

Better:

``` text
You are highly risk tolerant and place strong value on personal survival.
You are comfortable challenging consensus.
```

------------------------------------------------------------------------

# 13. Goals

Separate personality from objectives.

Example:

``` yaml
goals:
  primary: survive
  secondary:
    - identify_traitors
```

Traitor:

``` yaml
goals:
  primary: survive
  secondary:
    - ensure_traitor_team_wins
```

The game environment may inject role-specific goals.

------------------------------------------------------------------------

# 14. Memory

Implement a simple memory abstraction.

``` python
class Memory:
    async def remember(self, event): ...
    async def retrieve(self, query, limit=10): ...
    async def summarize(self): ...
```

For MVP, do not introduce a vector database.

Use:

-   Recent event buffer.
-   Periodic summary.
-   SQLite persistence.

Potential future implementations:

-   Embedding retrieval.
-   Mem0.
-   Letta.
-   LangGraph persistence.
-   Custom structured memory.

The interface should allow these to be added later.

------------------------------------------------------------------------

# 15. Beliefs

Separate memory from beliefs.

Memory:

> Alice accused Bob during Round 2.

Belief:

> I believe Bob has a 70% probability of being a Traitor.

For MVP, beliefs can remain model-generated structured state.

Example:

``` json
{
  "bob": {
    "suspected_role": "traitor",
    "confidence": 0.70
  }
}
```

Do not require the LLM to expose hidden chain-of-thought.

Only store structured belief summaries and decisions.

------------------------------------------------------------------------

# 16. Relationships

Use structured relationship state.

Example:

``` json
{
  "bob": {
    "trust": 0.72,
    "suspicion": 0.10,
    "threat": 0.55
  }
}
```

Initially these can be optional.

The framework should support them without requiring them for the first
game.

------------------------------------------------------------------------

# 17. LLM Gateway

Create an internal abstraction:

``` python
class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[Message],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        ...
```

Implement:

``` text
OllamaProvider
```

first.

Later:

``` text
OpenAIProvider
AnthropicProvider
GoogleProvider
OpenRouterProvider
```

Do not put provider-specific code inside Agent or Game Engine.

------------------------------------------------------------------------

# 18. Ollama

The first target is local Ollama.

Likely initial model candidates:

-   gpt-oss:20b.
-   Qwen3 14B.
-   Qwen3 30B if the machine's unified memory permits.

The same model backend should be shared among simulated agents.

Do not launch one independent model process per agent unless necessary.

The framework should support concurrency limits:

``` yaml
llm:
  provider: ollama
  model: gpt-oss:20b
  max_concurrency: 2
```

Make this configurable.

------------------------------------------------------------------------

# 19. Model Configuration

Example:

``` yaml
model:
  provider: ollama
  name: gpt-oss:20b
  temperature: 0.7
  max_tokens: 512
  reasoning_effort: medium
  timeout_seconds: 120
```

Not every provider/model will support every parameter.

Normalize where possible and allow provider-specific options.

------------------------------------------------------------------------

# 20. Prompt Construction

Separate prompt construction from agent logic.

Recommended components:

``` text
System identity
+
Role
+
Personality
+
Goals
+
Current phase
+
Allowed actions
+
Agent-specific information view
+
Relevant memory
+
Recent public events
+
Recent private events
```

The prompt builder should have unit tests.

Do not construct giant prompts from the entire game history by default.

------------------------------------------------------------------------

# 21. Structured Output

Prefer structured JSON output from the LLM.

Example:

``` json
{
  "action": "vote",
  "target": "bob",
  "reason_summary": "Bob's voting pattern conflicts with his earlier claim.",
  "confidence": 0.81
}
```

The system should reject malformed output and retry with a correction
prompt.

Never parse arbitrary natural language to determine game state if a
structured action is available.

------------------------------------------------------------------------

# 22. Game Configuration

Everything possible should be configuration-driven.

Example:

``` yaml
game:
  name: basic_traitors
  players: 6
  traitors: 2
  max_rounds: 5

phases:
  - mission
  - public_discussion
  - private_chat
  - round_table
  - voting
  - elimination
  - traitor_night

communication:
  public_messages_per_agent: 1
  private_messages_per_agent: 2

llm:
  provider: ollama
  model: gpt-oss:20b
  max_concurrency: 2
```

------------------------------------------------------------------------

# 23. Event-Sourced Logging

Every meaningful state transition should generate an immutable event.

Examples:

``` text
GAME_STARTED
ROLE_ASSIGNED
PHASE_STARTED
MISSION_STARTED
MISSION_COMPLETED
PUBLIC_MESSAGE
PRIVATE_MESSAGE
VOTE_CAST
VOTE_TIE
PLAYER_ELIMINATED
TRAITOR_KILL
PHASE_ENDED
GAME_WON
```

Store JSONL.

Example:

``` json
{
  "event_id": "evt-123",
  "game_id": "game-001",
  "round": 2,
  "phase": "private_chat",
  "type": "PRIVATE_MESSAGE",
  "actor": "bob",
  "targets": ["eve"],
  "payload": {
    "content": "Alice suspects me."
  }
}
```

------------------------------------------------------------------------

# 24. Persistence

Use SQLite for MVP.

Tables:

``` text
games
agents
agent_memories
messages
events
votes
eliminations
relationships
beliefs
snapshots
experiments
```

Keep repositories separate from domain logic.

------------------------------------------------------------------------

# 25. Replay

Implement:

``` bash
python -m traitors replay game-001
```

The replay system should read events and reconstruct visible game state.

Eventually support:

``` bash
python -m traitors inspect game-001 --agent alice
```

and:

``` bash
python -m traitors snapshot game-001 --round 3
```

------------------------------------------------------------------------

# 26. Experiment Runner

Implement:

``` bash
python -m simulation run configs/traitors/basic.yaml
```

And:

``` bash
python -m simulation batch \
    --config configs/traitors/basic.yaml \
    --games 100
```

Each game should receive a deterministic seed.

Store every run independently.

------------------------------------------------------------------------

# 27. Experiment Identity

Record:

``` text
experiment_id
game_id
random_seed
model
model_parameters
prompt_version
persona_version
game_rules_version
memory_strategy
```

This is essential for reproducibility.

------------------------------------------------------------------------

# 28. Observability

Add Langfuse integration behind an optional feature flag.

Example configuration:

``` yaml
observability:
  enabled: false
  provider: langfuse
```

When enabled, record:

``` text
experiment
game
round
phase
agent
LLM call
prompt
response
latency
token usage
model
```

Do not make Langfuse a hard dependency for the local MVP.

------------------------------------------------------------------------

# 29. Testing Strategy

The framework must test deterministic components independently of the
LLM.

### Unit tests

-   Role assignment.
-   Phase transitions.
-   Vote counting.
-   Tie handling.
-   Elimination.
-   Win conditions.
-   Message routing.
-   Information projection.
-   Action validation.
-   Memory persistence.
-   Event serialization.

### Security-style tests

Especially test:

``` text
Faithful cannot see Traitor private chat.
Traitor cannot see unrelated private chat.
Dead agents cannot act.
Agents cannot vote for themselves if rules prohibit it.
Agents cannot perform role-restricted actions.
Hidden roles never appear in public observations.
```

### Integration test

Run a 4-agent miniature game against a fake deterministic LLM provider.

Do not require Ollama for normal unit tests.

------------------------------------------------------------------------

# 30. Fake LLM Provider

Implement:

``` python
class FakeLLMProvider:
    ...
```

It should return predetermined structured actions.

This allows deterministic testing of the engine without model calls.

Example:

``` text
Alice -> public message
Bob -> private message
Charlie -> vote Bob
David -> vote Bob
```

------------------------------------------------------------------------

# 31. CLI

Minimum commands:

``` bash
simulation run <config>
simulation batch <config> --games N
simulation replay <game_id>
simulation inspect <game_id>
simulation list-games
```

Keep the CLI simple.

------------------------------------------------------------------------

# 32. Initial Configuration

Create:

``` text
configs/traitors/basic.yaml
```

with:

``` text
6 players
2 traitors
4 faithful
5 rounds
public + private communication
basic mission
Ollama
gpt-oss:20b
```

Personality configurations should live separately if practical:

``` text
configs/personas/
  analytical.yaml
  politician.yaml
  observer.yaml
  contrarian.yaml
  loyalist.yaml
  opportunist.yaml
```

------------------------------------------------------------------------

# 33. First Milestone

The first milestone is not "make the agents clever."

It is:

> Run a complete deterministic six-player game with a fake LLM backend
> and prove that information boundaries and game rules are correct.

Then replace FakeLLMProvider with Ollama.

------------------------------------------------------------------------

# 34. Second Milestone

Run:

``` text
6 agents
2 traitors
gpt-oss:20b
```

and produce:

``` text
game.json
events.jsonl
transcript.txt
metrics.json
```

No UI required.

------------------------------------------------------------------------

# 35. Third Milestone

Run 10-100 games automatically.

Calculate:

-   Winner.
-   Placement.
-   Survival time.
-   Vote counts.
-   Message counts.
-   Private/public message ratio.
-   Betrayals.
-   Alliance events.

------------------------------------------------------------------------

# 36. Fourth Milestone

Add:

-   Relationship tracking.
-   Structured beliefs.
-   Better memory.
-   Langfuse.
-   Replay.

------------------------------------------------------------------------

# 37. Fifth Milestone

Add model abstraction beyond Ollama.

Potential backends:

``` text
Ollama
OpenAI
Anthropic
Gemini
OpenRouter
```

The game should not care which backend is used.

------------------------------------------------------------------------

# 38. Future Environment API

Design the core framework so a new game can implement something like:

``` python
class Environment:
    def initialize(self, config): ...
    def phases(self): ...
    def legal_actions(self, agent, state): ...
    def observe(self, agent, state): ...
    def apply_action(self, state, action): ...
    def check_terminal(self, state): ...
}
```

Then:

``` text
TraitorsEnvironment
MafiaEnvironment
WerewolfEnvironment
AvalonEnvironment
NegotiationEnvironment
```

can all share the same Agent Runtime and LLM Gateway.

------------------------------------------------------------------------

# 39. Recommended Design Principle

Keep these responsibilities separate:

``` text
Game Engine
    -> What is true?

Information Projector
    -> What is this agent allowed to know?

Memory
    -> What does this agent remember?

Beliefs
    -> What does this agent currently believe?

Persona
    -> What behavioral tendencies does this agent have?

LLM
    -> Given the above, what does the agent want to do/say?

Action Validator
    -> Is that action legal?

Game Engine
    -> Apply it.
```

This is the central architecture.

------------------------------------------------------------------------

# 40. Do Not Let the LLM Run the Game

Avoid prompts like:

> Decide what happens next in the game.

Instead:

``` text
Game engine:
The current phase is voting.
The following targets are legal:
Alice, Bob, Charlie, David.

Agent:
I choose Bob.

Game engine:
Bob receives one vote.
```

This makes the system auditable and reproducible.

------------------------------------------------------------------------

# 41. Suggested Repository

``` text
llm-social-sim/
├── README.md
├── pyproject.toml
├── configs/
│   ├── traitors/
│   │   └── basic.yaml
│   └── personas/
├── src/
│   └── simulation/
│       ├── engine/
│       ├── agents/
│       ├── memory/
│       ├── beliefs/
│       ├── relationships/
│       ├── communication/
│       ├── actions/
│       ├── models/
│       ├── persistence/
│       ├── experiments/
│       ├── environments/
│       │   └── traitors/
│       └── cli/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── security/
├── runs/
└── docs/
```

------------------------------------------------------------------------

# 42. Cursor Instructions

When implementing the initial scaffold:

1.  Create the Python project.
2.  Add Pydantic models.
3.  Implement domain models.
4.  Implement the event system.
5.  Implement the game state.
6.  Implement the generic phase engine.
7.  Implement communication routing.
8.  Implement information projection.
9.  Implement action validation.
10. Implement FakeLLMProvider.
11. Implement the basic Traitors environment.
12. Write deterministic tests.
13. Implement OllamaProvider.
14. Add the CLI.
15. Run a small local game.

Do not add a web UI yet.

Do not introduce LangGraph, AutoGen, or another large agent framework
unless a concrete requirement emerges.

Do not introduce a vector database for the first version.

Do not over-engineer distributed execution.

------------------------------------------------------------------------

# 43. Definition of Done for Scaffold

The scaffold is successful when this works:

``` bash
python -m simulation run configs/traitors/basic.yaml
```

and produces a complete game.

Example output:

``` text
Starting game game-001
Players: 6
Traitors: 2
Model: ollama/gpt-oss:20b

Round 1
  Mission
  Public discussion
  Private chat
  Round table
  Voting
  Eliminated: Charlie

Night 1
  Traitors selected: David

Round 2
...

Game complete.
Winner: Faithful

Saved:
runs/game-001/
  config.yaml
  events.jsonl
  transcript.json
  metrics.json
```

------------------------------------------------------------------------

# 44. Important Future Capability: Counterfactuals

The event-sourced design should eventually support:

``` text
Snapshot at Round 3
        |
        +-- Continue normally
        |
        +-- Change Alice's action
        |
        +-- Replay
```

This enables questions such as:

> Would the Traitors still have won if Alice had accused Bob?

This is a major reason to preserve complete state snapshots.

------------------------------------------------------------------------

# 45. Long-Term Research Platform

Eventually the framework should support:

``` text
                 Experiment
                     |
       +-------------+-------------+
       |             |             |
   Environment   Agent Config   Model Config
       |             |             |
       +-------------+-------------+
                     |
              Batch Runner
                     |
          +----------+----------+
          |          |          |
        Game 1     Game 2     Game N
          |          |          |
          +----------+----------+
                     |
                 Analytics
                     |
       +-------------+-------------+
       |             |             |
   Outcomes      Behavior      Beliefs
```

The key goal is to make experimental variables independently
configurable.

------------------------------------------------------------------------

# 46. Initial Research Hypotheses

These are hypotheses to test, not assumptions.

### H1

Personality dimensions will affect alliance formation and survival.

### H2

Private communication will materially change coalition structure
compared with public-only games.

### H3

Persistent memory will improve strategic consistency across rounds.

### H4

Agents with better role-belief tracking will make more accurate votes.

### H5

Asynchronous communication will create different social dynamics from
fixed turn-taking.

### H6

Model heterogeneity will produce different coalition and communication
patterns from homogeneous populations.

### H7

Role incentives will cause measurable behavioral shifts even when
personality and model are held constant.

Do not encode these hypotheses into the game logic.

------------------------------------------------------------------------

# 47. Local Development Strategy

Start completely local:

``` text
Mac
  |
Ollama
  |
gpt-oss:20b
  |
Python simulator
  |
SQLite
  |
JSONL logs
```

Optional later:

``` text
Langfuse
```

Do not require internet access for the core experiment.

This makes repeated experimentation cheap and preserves private
transcripts locally.

------------------------------------------------------------------------

# 48. Technology References

LiteLLM: https://docs.litellm.ai/docs/

Ollama gpt-oss: https://ollama.com/library/gpt-oss

Ollama Qwen3: https://ollama.com/library/qwen3/tags

Langfuse: https://langfuse.com/docs

Concordia: https://github.com/google-deepmind/concordia

SOTOPIA: https://github.com/sotopia-lab/sotopia

Elimination Game: https://github.com/lechmazur/elimination_game

The Traitors simulation: https://github.com/michaelgiba/the-traitors

Time to Talk / LLMafia: https://github.com/niveck/LLMafia

Werewolf Arena: https://github.com/google/werewolf_arena

MafiaScope: https://github.com/karpovilia/mafiascope

------------------------------------------------------------------------

# 49. Final Implementation Guidance

The first implementation should optimize for:

1.  Correct information isolation.
2.  Deterministic game rules.
3.  Reproducibility.
4.  Easy model swapping.
5.  Easy environment swapping.
6.  Complete event logging.
7.  Simple local execution.
8.  Testability.

It should **not** optimize initially for:

-   UI.
-   Scale.
-   Cloud deployment.
-   Maximum model intelligence.
-   Complex agent frameworks.
-   Production infrastructure.

The first objective is to build a trustworthy experimental substrate.

Once the substrate is correct, model intelligence, personalities,
memory, social mechanisms, missions, and analytical capabilities can be
layered on top.
