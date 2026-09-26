# LLM Social Game Simulation Lab

## Raw Research and Design Notes

**Status:** Exploration / research notes\
**Purpose:** Preserve the full set of ideas, findings, technologies,
design considerations, and open questions discussed while exploring a
multi-agent LLM simulation based initially on *The Traitors*.

------------------------------------------------------------------------

## 1. Original Idea

The proposed experiment is to create multiple autonomous LLM agents,
give each agent a separate personality and role, and run a structured
social game show inspired by *The Traitors* or *Bigg Boss*.

The first target is **The Traitors**, using text only.

The game should have:

-   A definite timeline.
-   Explicit phases.
-   Public conversations visible to all eligible agents.
-   Private conversations visible only to selected agents.
-   Tasks or missions.
-   Hidden roles and asymmetric information.
-   Voting.
-   Eliminations.
-   Persistent agent identities.
-   Persistent memory.
-   Individual personalities.
-   Individual goals and incentives.
-   A game engine that controls the actual state and rules.
-   A transcript/replay of the entire simulation.

The central experimental question is not merely whether an LLM can
role-play a Traitor. It is:

> What kinds of social strategies emerge when autonomous LLM agents
> operate under asymmetric information, persistent identities,
> incentives, private communication, public communication, and
> consequences for decisions?

The broader vision is a **general-purpose laboratory for experiments on
emergent behavior in LLM societies**, with The Traitors as the first
environment.

------------------------------------------------------------------------

# 2. Why the Idea Is Interesting

The experiment combines several properties that are individually common
but unusually interesting when combined:

-   Different information available to different agents.
-   Different objectives.
-   Persistent personalities.
-   Private communication.
-   Public communication.
-   Shared history.
-   Agent-specific memory.
-   Consequences for decisions.
-   Uncertainty about what other agents know.
-   Alliances and betrayal.
-   Repeated interaction.
-   Competition and cooperation.

For example:

-   Alice knows Bob is a Traitor.
-   Bob knows Alice suspects him.
-   Charlie thinks Alice is suspicious.
-   Dave has little information but is highly confident.
-   Eve is a Traitor and has convinced everyone she is trustworthy.

The interesting behavior is not fully scripted. The environment provides
incentives and constraints, and the agents produce behavior within it.

------------------------------------------------------------------------

# 3. Core Conceptual Model

The system should be treated as a **multi-agent social simulation**,
rather than as a collection of chatbots.

A useful conceptual architecture is:

``` text
                         GAME ENGINE
                             |
                    controlled information
                             |
          +------------------+------------------+
          |                  |                  |
       Agent A             Agent B            Agent C
       Memory              Memory              Memory
       Persona             Persona             Persona
       Beliefs             Beliefs             Beliefs
       Goals               Goals               Goals
          |                  |                  |
       Private             Private             Private
       channels             channels            channels
          +------------------+------------------+
                             |
                       Public discussion
                             |
                        GAME ENGINE
```

The game engine is the source of truth.

Agents should not directly access global state.

This information isolation is central to the experiment.

------------------------------------------------------------------------

# 4. Agent Model

Each agent should conceptually have:

``` text
Agent
├── Identity
├── Personality
├── Role
├── Goals
├── Private knowledge
├── Beliefs
├── Memory
├── Relationships
├── Available actions
└── LLM backend/configuration
```

Example:

``` yaml
name: Alice

personality:
  analytical: 0.9
  assertiveness: 0.5
  sociability: 0.4
  trust: 0.3
  risk_tolerance: 0.4

role: faithful

private_knowledge:
  - Bob is a Traitor

goals:
  primary: survive
  secondary: identify_traitors
```

A different agent could be:

``` yaml
name: Bob

personality:
  analytical: 0.6
  assertiveness: 0.9
  sociability: 0.9
  trust: 0.4
  risk_tolerance: 0.8

role: traitor

private_knowledge:
  - Eve is also a Traitor

goals:
  primary: survive
  secondary: ensure_traitors_win
```

------------------------------------------------------------------------

# 5. Information Asymmetry

The global state may contain:

``` text
Alice = Faithful
Bob = Traitor
Charlie = Faithful
David = Faithful
Eve = Traitor
Frank = Faithful
```

But agents receive different views.

Alice:

``` text
You are Faithful.
You know Bob is a Traitor.
```

Bob:

``` text
You are a Traitor.
Your fellow Traitor is Eve.
```

Charlie:

``` text
You are Faithful.
You have no special information.
```

Eve:

``` text
You are a Traitor.
Your fellow Traitor is Bob.
```

This is the basis for an epistemic/social game.

------------------------------------------------------------------------

# 6. Public vs Private Communication

Example private interaction:

``` text
Alice -> Bob:
Something feels off about you.

Bob -> Alice:
Why? I've been trying to help the group.

Bob -> Eve:
Alice is becoming dangerous.

Eve -> Bob:
Agreed. We should push suspicion toward Charlie.
```

Public channel:

``` text
Alice:
I think Bob's behavior yesterday was strange.

Charlie:
I actually thought Bob was one of the more useful people.

David:
Interesting. Alice, you've accused two people now.
```

Agents must reason about:

-   What happened.
-   What was said.
-   What they privately know.
-   What they think other agents know.
-   What they think other agents believe.
-   What information may have leaked.
-   What public claims are consistent with their private knowledge.

------------------------------------------------------------------------

# 7. Proposed Game Loop

A Traitors-style game can be structured approximately as:

``` text
DAY
  |
  +-- Mission / Task
  |
  +-- Public discussion
  |
  +-- Private conversations
  |
  +-- Round-table discussion
  |
  +-- Vote
  |
  +-- Elimination
  |
NIGHT
  |
  +-- Traitor private meeting
  |
  +-- Traitor decision
  |
  +-- Murder / elimination
  |
  +-- Reveal consequences
  |
DAY 2
```

The exact rules should be configuration-driven.

------------------------------------------------------------------------

# 8. Missions

Missions should eventually provide additional strategic context rather
than merely being decorative.

Potential missions:

### Prisoner's dilemma

Each agent chooses Cooperate or Defect.

### Resource allocation

The group receives 100 points and must distribute them.

### Logic puzzle

Agents collaborate to solve a problem.

### Trust game

An agent receives resources and decides how much to transfer.

### Hidden-information puzzle

Each agent receives one piece of a puzzle.

Mission outcomes can become evidence used during later social reasoning.

------------------------------------------------------------------------

# 9. Personalities

Avoid overly cartoonish prompts such as:

> You are extremely manipulative and lie constantly.

Instead, describe behavioral tendencies.

Possible personality dimensions:

-   Analytical
-   Assertive
-   Sociable
-   Trusting
-   Suspicious
-   Risk tolerant
-   Conflict averse
-   Competitive
-   Cooperative
-   Charismatic
-   Confrontational
-   Quiet/observant
-   Consensus-oriented
-   Contrarian
-   Opportunistic
-   Loyal

Potential archetypes:

-   Detective
-   Politician
-   Sheep / consensus follower
-   Contrarian
-   Psychologist
-   Opportunist
-   Loyalist
-   Chaos agent
-   Quiet observer
-   Highly confident player

The personality should influence behavior rather than directly specify
the behavior.

------------------------------------------------------------------------

# 10. Memory

Memory is likely to become one of the most important variables.

Agents should not necessarily receive the entire global transcript
forever.

Possible memory approaches:

1.  Full transcript.
2.  Rolling summary.
3.  Retrieval-based memory.
4.  Structured event memory.
5.  Structured relationship memory.
6.  Hybrid memory.

Example memory:

``` text
Day 1:
Alice accused Bob.
Bob denied it.
Charlie defended Bob.

Day 2:
Alice privately told me she suspects Bob.
```

Another agent might remember:

``` text
Day 1:
Bob avoided answering my question.

Day 2:
I told Eve I suspect Bob.
```

This permits experiments involving imperfect or selective memory.

------------------------------------------------------------------------

# 11. Relationships

The simulation can maintain an agent relationship matrix.

Example:

``` text
          Alice Bob Charlie David Eve
Alice       -    72    41     63   18
Bob        35     -    81     22   91
Charlie    67    42     -     75   33
David      51    77    62      -   29
Eve        84    12    73     68    -
```

The values could represent trust, affinity, perceived threat, or a
combination.

Potential relationship dimensions:

-   Trust.
-   Suspicion.
-   Affinity.
-   Perceived threat.
-   Alliance strength.
-   Debt/obligation.
-   Betrayal history.

The relationship state can be observed and analyzed even if the agent
itself does not directly see numerical values.

------------------------------------------------------------------------

# 12. Behavioral Metrics

The experiment should record quantitative metrics.

### Individual

-   Survival time.
-   Final placement.
-   Win/loss.
-   Number of accusations.
-   Successful accusations.
-   Number of lies.
-   Number of truthful claims.
-   Successful lies.
-   Private conversations initiated.
-   Private conversations received.
-   Alliances formed.
-   Alliances broken.
-   Betrayals.
-   Vote switching.
-   Voting consistency.
-   Risk-taking.
-   Information sharing.
-   Information withholding.

### Social

-   Trust evolution.
-   Alliance graph.
-   Communication graph.
-   Betrayal graph.
-   Suspicion graph.
-   Vote blocs.
-   Centrality of agents.
-   Coalition stability.

------------------------------------------------------------------------

# 13. Emergent Behavior

A key research target is behavior that was not explicitly scripted.

Example:

1.  David is quiet.
2.  Alice dislikes quiet players.
3.  Alice accuses David.
4.  Charlie trusts Alice.
5.  Charlie repeats the accusation.
6.  David becomes defensive.
7.  Bob points out David's defensiveness.
8.  The group interprets defensiveness as guilt.

The interesting outcome is not just the final vote but the chain of
social inference that produced it.

------------------------------------------------------------------------

# 14. Repeated Experiments

A single game is not sufficient evidence.

The intended experimental structure should be:

``` text
Configuration A
100 simulations
        |
        v
Behavior distribution

Configuration B
100 simulations
        |
        v
Behavior distribution

Comparison
```

Important variables:

-   Random seed.
-   Model.
-   Model parameters.
-   Personality.
-   Role.
-   Memory strategy.
-   Communication policy.
-   Number of agents.
-   Private communication availability.
-   Mission structure.
-   Game rules.

------------------------------------------------------------------------

# 15. Proposed Experiments

## Experiment A: Personality

Same model, different personalities.

Question:

> Does personality systematically affect survival, alliances,
> accusations, or betrayal?

## Experiment B: Memory

Compare:

-   Full transcript.
-   Summarized memory.
-   Vector retrieval.
-   Structured memory.
-   Hybrid memory.

Question:

> How does memory architecture affect strategic consistency?

## Experiment C: Communication

Compare:

-   Public only.
-   Public + private.

Question:

> Does private communication substantially change coalition formation
> and deception?

## Experiment D: Asynchrony

Compare:

-   Forced turn-taking.
-   Agents decide when to speak.

Question:

> Does communication timing become a strategic behavior?

## Experiment E: Model heterogeneity

Compare:

-   All agents use the same model.
-   Each agent uses a different model.

Question:

> Does model heterogeneity produce different social dynamics?

## Experiment F: Role conditioning

Keep personality and model fixed but change role:

-   Faithful.
-   Traitor.

Question:

> How much behavior changes because of incentives alone?

------------------------------------------------------------------------

# 16. Existing Research and Prior Art

The idea has substantial prior art. The closest work found during
research is summarized below.

## 16.1 Elimination Game

Repository:

https://github.com/lechmazur/elimination_game

This is extremely close to the original idea.

It describes itself as a multi-player tournament benchmark testing LLM
social reasoning, strategy, and deception.

Features include:

-   Public conversations.
-   Private conversations.
-   Alliances.
-   Anonymous strategic voting.
-   Eliminations.
-   Final jury.
-   Replay.
-   Model-level statistics.
-   Betrayal metrics.
-   Rank distributions.
-   Final-two win rates.
-   Wordiness analysis.

Its published README describes games with 8 LLM players, public
discussion, preference ranking, private pairings, voting, tie-breaking,
eliminations, and a final jury.

This establishes that a direct LLM social elimination game is already an
active benchmark direction.

Source: https://github.com/lechmazur/elimination_game

## 16.2 The Traitors simulation

Repository:

https://github.com/michaelgiba/the-traitors

This project explicitly simulates the TV show The Traitors using
open-source LLMs.

It reports experiments involving local models and free hosted models and
was inspired by Elimination Game.

The repository includes game configurations, analysis, results, and
simulation scripts.

Source: https://github.com/michaelgiba/the-traitors

## 16.3 Time to Talk

Paper:

https://aclanthology.org/2025.findings-emnlp.608/

Repository:

https://github.com/niveck/LLMafia

This work studies asynchronous communication in Mafia games.

Its architecture separates:

-   A scheduler that decides whether the agent should speak.
-   A generator that decides what the agent should say.

The authors evaluated the agent against human Mafia players and reported
comparable game performance and ability to blend into human
conversations.

This is important because communication timing itself can be modeled as
a strategic action.

Sources: https://aclanthology.org/2025.findings-emnlp.608/
https://github.com/niveck/LLMafia

## 16.4 LLMafia dataset

Repository:

https://github.com/cocochief4/llm-mafia

The dataset contains 35 LLM-generated Mafia games.

Each game has 10 players, 2 Mafia, and approximately 3.17 days on
average.

It includes:

-   Public daytime chat.
-   Private Mafia nighttime chat.
-   Game-manager messages.
-   Player names.
-   Mafia identities.
-   Final outcomes.

It was designed to study deception quality, deception detection, natural
language patterns, and strategic communication under partial
observability.

Source: https://github.com/cocochief4/llm-mafia

## 16.5 Werewolf Arena

Repository:

https://github.com/google/werewolf_arena

Werewolf Arena was designed as a framework for evaluating social
reasoning skills of LLMs through the Werewolf game.

It supports single games, bulk evaluation, game state saving, and an
interactive viewer showing private reasoning, bids, votes, and prompts.

The repository was archived on September 24, 2025, but remains useful as
prior art.

Source: https://github.com/google/werewolf_arena

## 16.6 Concordia

Repository:

https://github.com/google-deepmind/concordia

Concordia is a library for generative social simulation.

Its architecture uses:

-   Entities.
-   Components.
-   Game Master.
-   Simulation engine.

Agents describe intended actions in natural language, and the Game
Master resolves actions against the environment.

This architecture is highly relevant to the proposed framework.

Source: https://github.com/google-deepmind/concordia

## 16.7 SOTOPIA

Repository:

https://github.com/sotopia-lab/sotopia

SOTOPIA is an open-ended social learning environment for evaluating
social intelligence in language agents.

It emphasizes extensible environments and scalable interaction among
agents.

Source: https://github.com/sotopia-lab/sotopia

## 16.8 MafiaScope

Repository:

https://github.com/karpovilia/mafiascope

MafiaScope is a particularly interesting newer direction.

It uses Mafia as a testbed for machine Theory of Mind and records
private beliefs such as:

-   Role assessments.
-   Suspicion rankings.
-   Second-order beliefs.
-   Personality attributions.
-   Planned actions.

It can replay games and branch from snapshots for counterfactual
analysis.

This is highly relevant to a future version of this project because it
demonstrates that we can measure internal belief states separately from
game behavior.

Source: https://github.com/karpovilia/mafiascope

## 16.9 Other Mafia implementations

Other open-source implementations found include:

https://github.com/mahbodnr/LLMafia

This implementation supports multiple LLM providers, local Ollama
models, agent memory, role-specific behavior, voting, eliminations, and
a web UI.

Another:

https://github.com/nickslevine/mafiabench

This benchmark records game logs, tournament statistics, ELO-style
ratings, event timelines, and per-model performance.

Another:

https://github.com/Alfaxad/mafia

This includes a more elaborate Mafia agent architecture with
objective/evidence/risk review, role-conditioned policy, suspicion
ledgers, public-evidence adjudication, and legal JSON actions.

These projects are valuable prior art for implementation ideas.

------------------------------------------------------------------------

# 17. What Seems Novel Enough to Pursue

The basic proposition:

> Put LLMs into a Traitors-style game.

is no longer novel.

The more interesting opportunity is:

> Build a configurable experimental laboratory where game environments,
> personalities, memory architectures, information visibility,
> communication policies, incentives, and model backends can be
> independently varied and statistically evaluated.

The Traitors game would be Environment #1.

Other future environments could include:

-   Big Boss-style social simulation.
-   Survivor-style elimination.
-   Mafia.
-   Werewolf.
-   Avalon.
-   Prisoner's Dilemma tournaments.
-   Negotiation games.
-   Coalition formation.
-   Resource allocation.
-   Trust games.
-   Custom social experiments.

------------------------------------------------------------------------

# 18. Technology Landscape

## 18.1 Ollama

Ollama is a strong candidate for local model execution.

Relevant models include:

### gpt-oss:20b

Ollama lists:

-   Approximately 14 GB.
-   128K context.
-   Text.
-   Local inference.
-   Configurable reasoning effort.
-   Structured outputs.
-   Tool use.
-   MXFP4 quantization.
-   Designed for lower-latency local use.

Source: https://ollama.com/library/gpt-oss

Ollama states that the 20B model's quantization allows it to run on
systems with as little as 16 GB memory.

### Qwen3

Ollama currently lists Qwen3 variants including:

-   8B.
-   14B.
-   30B.

The Qwen3 30B-A3B Q4 variant is approximately 19 GB and has a 256K
context variant.

Source: https://ollama.com/library/qwen3/tags

------------------------------------------------------------------------

# 19. Local Mac Strategy

The user has an M4 MacBook Air 13-inch.

Exact unified-memory configuration is still an open question.

General guidance:

-   16 GB: small/medium models and carefully managed 20B-class models.
-   24 GB: 20B-class models become much more practical; 30B quantized
    models may be viable.
-   32 GB: 20B and 30B-class experiments become more comfortable.
-   More memory allows larger models and longer contexts.

The experiment does not require one model instance per agent.

Instead:

``` text
                   One Ollama model
                          |
          +---------------+---------------+
          |               |               |
       Agent A          Agent B         Agent C
       context          context         context
       memory           memory          memory
       persona          persona         persona
```

The same model can serve many simulated identities.

Concurrency must still be controlled because the Mac has finite compute
and unified memory.

------------------------------------------------------------------------

# 20. Local Model Experimentation Strategy

Start with one local model.

Recommended initial candidates:

1.  gpt-oss:20b.
2.  Qwen3 30B if memory allows.
3.  Qwen3 14B as a lower-resource baseline.
4.  Smaller models for dialogue-only workloads.

The first experiment should probably be:

``` text
6 agents
4 Faithful
2 Traitors
5 rounds
1 local model
```

Do not immediately attempt 10+ large agents concurrently.

------------------------------------------------------------------------

# 21. Split Cognitive Workloads

A future optimization is to use different models for different cognitive
workloads.

For example:

``` text
Strategic reasoning:
gpt-oss:20b

Conversational generation:
Qwen3:8B
```

Strategic calls could handle:

-   Who should I trust?
-   Who should I accuse?
-   Should I betray an ally?
-   Who should the Traitors eliminate?
-   What information should I reveal?

Dialogue calls could simply turn an already-selected action into natural
language.

This is optional and should not complicate v1.

------------------------------------------------------------------------

# 22. Structured Agent Actions

The LLM should not directly control game state.

A good pattern is:

``` json
{
  "action": "private_message",
  "target": "alice",
  "content": "I think Bob is hiding something.",
  "confidence": 0.73
}
```

Other actions could be:

``` text
PUBLIC_MESSAGE
PRIVATE_MESSAGE
VOTE
TRAITOR_KILL
FORM_ALLIANCE
SHARE_INFORMATION
WITHHOLD_INFORMATION
ACCUSE
DEFEND
```

The game engine validates whether the action is legal and applies it.

------------------------------------------------------------------------

# 23. Recommended Initial Technology Stack

A minimal custom stack:

``` text
Python
Pydantic
asyncio
SQLite or PostgreSQL
Ollama
LiteLLM
Langfuse
```

## Python

Core simulation language.

## Pydantic

Schemas for:

-   Game state.
-   Agent state.
-   Messages.
-   Actions.
-   Events.
-   Configuration.

## asyncio

Concurrent agent calls and asynchronous communication.

## SQLite

Excellent for the first local prototype.

Move to PostgreSQL if the experiment becomes a larger research platform.

## Ollama

Local LLM backend.

## LiteLLM

Unified model interface.

LiteLLM provides an OpenAI-compatible interface to many providers and
supports Ollama, retries, fallbacks, routing, and cost tracking.

Source: https://docs.litellm.ai/docs/

## Langfuse

Observability and experiment tracing.

Langfuse records traces, observations, prompts, outputs, latency, token
usage, and metadata. It is open source and self-hostable.

Sources: https://langfuse.com/docs/observability/overview
https://langfuse.com/docs/observability/data-model

------------------------------------------------------------------------

# 24. Why Not Start With a Large Agent Framework?

Possible frameworks include:

-   AutoGen.
-   LangGraph.
-   AgentScope.
-   Concordia.

However, the game itself is already a state machine.

The first version should avoid unnecessary abstraction:

``` text
Game Engine
    |
Agent
    |
LLM
```

rather than:

``` text
Game Engine
    |
Framework
    |
Graph
    |
Agent abstraction
    |
LLM framework
    |
Provider
```

The most important thing to own is **information visibility**.

------------------------------------------------------------------------

# 25. Suggested General Architecture

``` text
                        EXPERIMENT
                            |
            +---------------+---------------+
            |                               |
      Game Configuration              Agent Configuration
            |                               |
            +---------------+---------------+
                            |
                       GAME ENGINE
                            |
        +-------------------+-------------------+
        |                   |                   |
      Rules               State               Phase
        |                   |                   |
        +-------------------+-------------------+
                            |
                     INFORMATION LAYER
                            |
             +--------------+--------------+
             |              |              |
           Agent A        Agent B        Agent C
             |              |              |
          Persona        Persona        Persona
          Memory         Memory         Memory
          Beliefs        Beliefs        Beliefs
          Goals          Goals          Goals
             |              |              |
             +--------------+--------------+
                            |
                      MESSAGE ROUTER
                       /          \
                  PUBLIC          PRIVATE
                            |
                       LLM GATEWAY
                            |
                         LiteLLM
                            |
             +--------------+--------------+
             |              |              |
           Ollama          API          Other
                            |
                       OBSERVABILITY
                         Langfuse
                            |
                        DATA STORE
                            |
             +--------------+--------------+
             |              |              |
          Transcript      Metrics       Events
```

------------------------------------------------------------------------

# 26. Configuration-Driven Design

The experiment should be defined by configuration rather than code.

Example:

``` yaml
game:
  players: 6
  traitors: 2
  rounds: 5

  phases:
    - mission
    - public_discussion
    - private_chat
    - round_table
    - vote
    - elimination
    - traitor_night

agents:
  - id: alice
    personality:
      analytical: 0.9
      assertiveness: 0.5
      trust: 0.3
      sociability: 0.4

  - id: bob
    personality:
      analytical: 0.6
      assertiveness: 0.9
      trust: 0.4
      sociability: 0.9

model:
  provider: ollama
  name: gpt-oss:20b
  temperature: 0.7
```

------------------------------------------------------------------------

# 27. Data Model

At minimum:

``` text
Experiment
Game
Phase
Agent
Role
Personality
AgentMemory
Belief
Relationship
Message
Action
Vote
Event
Mission
Elimination
Outcome
ModelConfiguration
```

Every state-changing operation should generate an event.

Example:

``` json
{
  "event_type": "private_message",
  "game_id": "game-001",
  "round": 2,
  "sender": "bob",
  "recipient": "eve",
  "content": "Alice suspects me.",
  "visibility": "private",
  "timestamp": "..."
}
```

------------------------------------------------------------------------

# 28. Determinism and Replay

Every game should record:

-   Experiment configuration.
-   Random seed.
-   Model name.
-   Model parameters.
-   Prompt version.
-   Agent configuration.
-   Role assignment.
-   Event sequence.
-   LLM outputs.
-   Game state snapshots.

The system should support:

``` text
Replay game
Pause game
Inspect state
Inspect agent memory
Inspect private messages
Branch from a snapshot
Run counterfactual
```

Counterfactual replay is especially interesting.

Example:

> What happens if Alice had not accused Bob on round 2?

------------------------------------------------------------------------

# 29. Observability

Langfuse can be used to capture:

-   LLM calls.
-   Prompt.
-   Output.
-   Token usage.
-   Latency.
-   Agent identity.
-   Round.
-   Phase.
-   Model.
-   Game.
-   Experiment.

A useful hierarchy is:

``` text
Experiment
  |
  +-- Game
       |
       +-- Round
            |
            +-- Phase
                 |
                 +-- Agent action
                      |
                      +-- LLM generation
```

This makes debugging questions such as:

> Why did Alice suddenly decide Bob was a Traitor?

answerable.

------------------------------------------------------------------------

# 30. Experimental Reproducibility

Every run should be identified by something like:

``` text
experiment_id
game_id
seed
model
model_parameters
prompt_version
personality_version
game_rules_version
memory_strategy
```

Do not overwrite runs.

Store immutable results.

------------------------------------------------------------------------

# 31. Potential Future Research Dimensions

Once the basic platform works, potential studies include:

### Personality vs outcome

Does a specific trait correlate with survival?

### Role vs personality

Does a personality behave differently when incentives change?

### Memory architecture

Does richer memory improve strategic consistency?

### Private communication

Does private communication increase alliance stability?

### Communication timing

Does deciding when to speak create emergent strategy?

### Model heterogeneity

Do different models form different coalition structures?

### Reasoning effort

Does stronger reasoning improve deception or merely verbosity?

### Agent population size

How do dynamics change from 6 to 10 to 20 agents?

### Information leakage

How robustly can the system maintain private information?

### Social centrality

Do highly connected agents become more influential?

### Deception

When do agents choose to lie versus remain silent?

### Trust

How quickly does trust form and how quickly does betrayal destroy it?

------------------------------------------------------------------------

# 32. Potential Experimental Metrics

A future analytics layer could calculate:

``` text
Win rate
Survival rate
Average placement
Round of elimination
Traitor success rate
Faithful success rate
Alliance count
Alliance duration
Betrayal count
Betrayal success
Accusation precision
Accusation recall
Vote accuracy
Vote switching rate
Message count
Words/message
Private/public ratio
Trust trajectory
Suspicion trajectory
Network centrality
Coalition stability
Information-sharing rate
```

For belief-oriented experiments:

``` text
Role belief accuracy
Suspicion calibration
Second-order belief accuracy
Prediction of other agents' votes
Prediction of other agents' beliefs
```

------------------------------------------------------------------------

# 33. Important Methodological Principle

Do not interpret one game as evidence of a general behavior.

Use repeated runs.

For example:

``` text
10 configurations
×
100 games each
=
1,000 games
```

Then analyze distributions rather than anecdotes.

The research literature itself increasingly emphasizes repeated
simulations and empirical validation rather than relying on single
simulation runs.

------------------------------------------------------------------------

# 34. Initial MVP

The recommended MVP is deliberately small.

### Game

-   6 agents.
-   2 Traitors.
-   4 Faithful.
-   5 rounds.
-   Text only.
-   Public chat.
-   Private chat.
-   Voting.
-   Elimination.
-   Traitor night.
-   Basic mission.

### Agent

-   Persona.
-   Role.
-   Goal.
-   Short-term memory.
-   Long-term summary.
-   Model backend.
-   Public/private visibility.

### Infrastructure

-   Python.
-   Pydantic.
-   asyncio.
-   SQLite.
-   Ollama.
-   JSON/JSONL event log.

### Interface

Initially CLI.

Example:

``` text
$ python -m traitors run configs/basic.yaml
```

Later:

``` text
$ python -m traitors replay game-001
```

------------------------------------------------------------------------

# 35. First Local Model Test

Before building the full simulator, test whether the local model can
handle the core reasoning.

Example:

``` text
You are Bob.

Alice is secretly a Traitor.
Charlie suspects you.
David trusts you.
Eve is neutral.

Who should you privately approach?

A) Alice
B) Charlie
C) David

Return JSON with:
- choice
- reasoning_summary
- confidence
```

Then run 50-100 variants.

Also test:

-   Memory consistency.
-   Role secrecy.
-   Name tracking.
-   Voting.
-   Belief updates.
-   Private-information isolation.

------------------------------------------------------------------------

# 36. Local Hardware Strategy

The user has an M4 MacBook Air.

Exact RAM configuration is not yet known.

The practical strategy is:

1.  Start with gpt-oss:20b.
2.  Benchmark response latency and quality.
3.  Try Qwen3 14B as a lightweight baseline.
4.  Try Qwen3 30B if unified memory allows.
5.  Avoid launching one model process per agent.
6.  Share the model backend between simulated agents.
7.  Limit concurrency.
8.  Keep context windows bounded through memory and summaries.

The goal is not maximum model size.

The goal is enough intelligence to produce coherent strategic behavior
while enabling many repeated games.

------------------------------------------------------------------------

# 37. Key Architectural Principle

The LLM should be treated as a participant in the simulation, not as the
simulation itself.

The deterministic system owns:

-   Truth.
-   Roles.
-   Game state.
-   Visibility.
-   Rules.
-   Legal actions.
-   Timing.
-   Votes.
-   Eliminations.
-   Win conditions.

The LLM owns:

-   Interpretation.
-   Belief formation.
-   Strategy.
-   Natural-language communication.
-   Choice among legal actions.

This separation is critical.

------------------------------------------------------------------------

# 38. Long-Term Vision

The final system could become:

``` text
             SOCIAL SIMULATION LAB
                       |
       +---------------+---------------+
       |               |               |
   The Traitors      Mafia          Avalon
       |               |               |
       +---------------+---------------+
                       |
                Generic Engine
                       |
        +--------------+--------------+
        |              |              |
    Personalities    Memory        Incentives
        |              |              |
        +--------------+--------------+
                       |
                 Model Backends
                       |
       +---------------+---------------+
       |               |               |
     Ollama          API Models      Other
```

The game itself becomes one configurable environment.

------------------------------------------------------------------------

# 39. Final Position

The core concept is worth pursuing.

It is not novel simply because it uses LLMs to play The Traitors.
Several projects already demonstrate that.

The interesting contribution is a **general, reproducible experimental
framework** in which social dynamics can be studied by independently
changing:

-   Agent personality.
-   Agent role.
-   Model.
-   Memory.
-   Information visibility.
-   Communication mechanism.
-   Game rules.
-   Incentives.
-   Population size.
-   Timing.

That makes the project substantially more useful than a one-off game
simulator.
