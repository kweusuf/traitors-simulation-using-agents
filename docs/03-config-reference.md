# Config reference: every knob, and what it does on and off

The `game:` block is the interface. A flag that is off has to leave a game
playing exactly as it did before it existed, or every earlier run stops being
a baseline - the reasoning is in
[decisions.md](decisions.md#every-experimental-flag-defaults-off). Two flags
are on by default (`agent_memory`, `anti_echo_instructions`) and are marked
here, because a reader who assumes "all off" would misread them.

This page is the reference for what each key does in both states. For *why* a
default is what it is, each key's comment in `experiments/config.py` carries
the measurement or the failure behind it, and the arms that exist are in
[The arms](#the-arms) below. To run one:
`uv run --no-sync python -m simulation run configs/traitors/<arm>.yaml`.

## The deal and the roster

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `players` | `6` | Roster size; `faithful` is derived as `players - traitors` and validated rather than configured | - |
| `traitors` | `2` | How many traitors the game starts with | - |
| `traitor_names` | none | The traitors are **pinned by name**; must list exactly `traitors` players from `player_names`. Used for a season replay, where the traitors are known | The engine draws them from the seed (`rng.sample`). This is the knob the [random-deal arms](#the-random-deal-arms) vary |
| `player_names` | none | The cast, in draw order - the order matters, because the seeded draw walks it | Generated names (`player1`, ...) |
| `personas` | none | Behaviour files, resolved against `configs/personas/<set>/`, assigned round-robin when there are fewer names than players | Every player gets an empty persona |
| `seed` | `42` | Drives the traitor draw, every player's `solo`/`team` ambition, and item awards; recorded in `GAME_STARTED` so a resumed run continues the same game | - |
| `max_rounds` | `5` | Cap on rounds. When it is reached with no elimination victory, `round_limit_winner` takes the win | - |
| `round_limit_winner` | `"faithful"` | Who wins when `max_rounds` is exhausted: `faithful` or `traitor` | - |
| `allow_self_vote` | `false` | A player's ballot may name themselves | Self-votes are illegal and rejected |

## Recruiting

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `recruit_on_banish` | `false` | A banished traitor converts one living faithful on the way out, so the tower refills itself. Without a cap the faithful can never empty it by voting alone | The traitor count only ever falls |
| `max_recruits` | `0` | Caps `recruit_on_banish` for the whole game | `0` = no cap |
| `recruit_choice` | `false` | Recruitment becomes the traitors' *choice* on the night it opens: they vote recruit-or-murder (a tie murders), offer one living faithful who may decline, and a lone traitor's offer is an ultimatum. Overrides `recruit_on_banish` when both are set | Recruitment happens automatically under `recruit_on_banish`, if that is on |
| `recruit_window` | `true` | With `recruit_choice` on: the choice opens **only** on the night after a traitor was banished, and is spent that night whether they recruit or kill. This is the show's rule | The choice is offered on every traitor night; a vacancy can be held open indefinitely. Either way the living traitor count may never exceed the starting count |

## Discussion, repetition and rejection

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `agent_memory` | **`true`** | A decaying, per-observer memory of the other players, scoped to what each player could have observed (the traitor council never reaches a faithful). Prompts carry only the memories still above `memory_floor` | Memoryless control |
| `memory_decay` | `0.6` | Salience is multiplied by this each round, so trivia fades in a couple of rounds while a named accusation or a private bequest stays | - |
| `memory_floor` | `0.5` | Below this salience a memory is dropped rather than rendered | - |
| `memory_items_in_prompt` | `6` | How many memories one prompt carries | - |
| `reject_invented_players` | `false` | A message naming a player who does not exist is **refused and re-asked**. A phantom introduced in round 1 once occupied the whole room - 82% of messages - and the faithful never found a real traitor | The phantom is counted (`phantoms` in the run summary) and allowed through. Off by default because the detector still false-positives on sentence-initial words |
| `reject_repetition` | `false` | A message that repeats one already said is refused and re-asked with the rejected text quoted back. Self-repeats fell 32 → 0 against `ptr_mem` | Repeats are logged only; the duplicate-rate metrics still count them |
| `repetition_threshold` | `1.0` | The share of content words two messages must share to count as a repeat. At `1.0` only byte-identical messages are refused | Lower it to catch paraphrases, but 48% of a real game's public messages score ≥ 0.6, so anything near that discards half the discussion |
| `repetition_scope` | `"room"` | A player may not echo **anyone** - the conversational-following a small model does constantly. Nested, not disjoint: it includes the speaker's own messages | `"self"` rejects only a player repeating their own message, which zeroed self-repeats and left the cross-player copies that are the actual collapse |
| `repetition_min_words` | `5` | Below this length only an exact match is refused, since `"No."` is not a repetition of `"No."` | - |
| `anti_echo_instructions` | **`true`** | Stylistic constraints on public speech: do not paraphrase the last speaker, take a position rather than validate the room. The rule against replying to yourself is separate, because it is factual rather than stylistic | The `noecho` control. A small model collapsed onto one template and eight different players emitted byte-identical messages |
| `language` | `"english"` | `"hinglish"` makes agents speak Roman-script Hindi mixed with English. Only free prose is translated: any action the engine reads as a machine token (`recruit`, `murder`, `accept`, `decline`, `end`, `banish`) must stay the exact English word | - |

## The suspicion ledger

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `suspicion_ledger` | `false` | Each player keeps a ranked list of who they suspect and who they have cleared, shown every turn. A model asked for a bare `target` reasons in one forward pass over the whole transcript and answers with whatever name looked worst most recently, which is why banishment tracked chance; the ledger gives it somewhere to have written a position down first | No ledger; the target is chosen in one pass |
| `ledger_suspects` | `3` | How many names the suspect list may hold | - |
| `ledger_allies` | `2` | How many the cleared list may hold. Shorter than the suspect list on purpose: a long innocent list is not a defence, it is a preference | - |

## Pointers (the prompt-size experiment)

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `co_generate_gist` | `false` | Ask the model for a one-line pointer to its own message **in the same call** that writes it, so the pointer costs no extra generation. This is an experiment knob, not a recommended default: it asks a small model to do two things at once, and a pointer that comes back as a copy of the opening clause is not a distillation | The transcript carries whole messages |
| `gist_required` | `false` | The pointer is mandatory in the response schema, so a reply without one is refused and the call retried. Changes the grammar, not the request - on its own it does nothing | The pointer is optional and memory falls back to its deterministic clause |
| `pointer_memory` | `false` | Store a decaying pointer for **every** message, not just the ones the show's rules make worth keeping. This is what lets the transcript shrink: old exchanges leave the prompt by fading instead of riding along until they fall out of the window | Only accusations, bequests and deaths leave a trace |

See [2026-10-04-prompt-size-and-template-collapse.md](2026-10-04-prompt-size-and-template-collapse.md)
for what the pointer experiment established and what it withdrew.

## The endgame and the season's cadence

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `finale_traitors` + `finale_faithful` | `0` + `0` | Normal play stops when exactly that split is alive and rapid-fire voting decides the winner; both must be set together | `0` disables the finale and keeps the plain parity win |
| `finale_total` | `0` | Starts the endgame at that many living players whatever the split - the show starts at the final five | `0` disables the total rule |
| `finale_max_votes` | `10` | Consecutive rapid-fire rounds with nobody banished before `round_limit_winner` is declared | - |
| `endgame_vote` | `false` | Every finale round ends with each living player answering `end` or `banish`, so the endgame can also decide to stop | The finale runs until the split or `finale_max_votes` settles it |
| `blind_finale_banishments` | `false` | Roles of players banished during the finale are hidden until the game ends, as the show plays its final table | A finale banishment reveals the role immediately |
| `quiet_murder_rounds` | `[]` | Round numbers on which the traitors do not murder at all | No quiet nights |
| `quiet_banishment_rounds` | `[]` | Round numbers on which the round table votes on nobody | Every round votes |

## Hosted structure

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `discussion_budget` | `0` | Caps the open speaking turns of one debate, which the deterministic host paces and warns on | `0` disables the clock and keeps one turn each |
| `warning_turns` | `1` | How many closing turns run after the host warns that time is almost up | - |
| `nomination_enabled` | `false` | The round table opens with accusations from each player before the vote | Straight to the vote |
| `revote_enabled` | `false` | A tied banishment ballot is re-run as a revote restricted to the tied suspects | A tie is resolved by the plain tally rule |
| `nomination_keep` | `2` | How many of the most-nominated suspects stay standing to answer the room, and face the revote on a tie | - |
| `council_deliberation` | `false` | The night's kill is decided in two rounds: a proposal from each traitor in turn onto the channel, then a simultaneous hold-or-switch where the majority of final picks wins | One simultaneous blind ballot |

## Items (one-shot)

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `shield` | `false` | Awards a one-shot item that blocks the next murder on its holder | No shield |
| `dagger` | `false` | Awards a one-shot item whose holder's vote counts twice | No dagger |
| `seer` | `false` | Awards a one-shot private check of one player's true role | No seer |
| `on_trial` | `false` | Traitors nominate a murder shortlist before the kill, restricting who is legal that night | The whole living roster is legal |

## The round, the transcript and the model

| Key | Default | Set | Unset |
| --- | --- | --- | --- |
| `phases` | `mission, public_discussion, private_chat, round_table, voting, elimination, traitor_night` | The round runs this list in order, and how many times a phase is listed is how many times it runs - the season config extends the round with a third `mission`. `voting` is required; `setup`, `game_end` and `end_vote` are driven by the engine and are rejected here | An unknown phase is a config error, not a silent skip |
| `communication.public_messages_per_agent` | `1` | How many open turns each player gets per discussion phase | `0` = the phase produces no messages |
| `communication.private_messages_per_agent` | `2` | How many messages each player gets per private phase, including the traitors-only channel | `0` = no private chat |
| `communication.transcript_messages_per_prompt` | `40` | How many transcript lines a prompt carries, newest last | `0` = the whole transcript. This is the real cap on prompt growth: the full transcript passes 40k characters in a long game, and every extra token is prompt-evaluation time per call |
| `llm.provider` | `ollama` | `ollama` for a live model; `fake` plays the whole game deterministically with no model at all, which is what the test suite and a config check use | - |
| `llm.model` | `gpt-oss:20b` | The model tag. An identical tag does not imply identical weights across hosts | - |
| `llm.base_url` | `http://localhost:11434` | Tracked arms carry a `HOST_A` placeholder; the real endpoint belongs in a gitignored `*.local.yaml` or a `--base-url` argument, never in a tracked file ([decisions.md](decisions.md#model-identity-is-an-explicit-variable-never-implicit)) | - |
| `llm.max_concurrency` | `2` | Calls the run keeps in flight. Past what a host serves in parallel the extra requests only queue into `timeout_seconds`, so raising it can make a run slower and emptier rather than faster | - |
| `llm.temperature` / `max_tokens` / `reasoning_effort` | `0.7` / `512` / `medium` | Sampling temperature, the reply cap, and how much thinking the provider is asked for. They sit in the config because they belong to the arm, not to the framework | - |
| `llm.timeout_seconds` / `retries` | `120` / `3` | Per-call timeout, and extra attempts for transient transport failures before the run gives up | - |
| `seed` (top level) | `42` | Overridable per run with `--seed`. The ledger marks `seed % 10 >= 7` as holdout, so a holdout run never drives a promotion decision | - |
| `observability` | disabled | `provider: langfuse` with `enabled: true` exports traces | Nothing is exported |

## The arms

Every arm in `configs/traitors/` is one experiment. `season_uk_s01.fix_en.yaml`
is the reference: the rest are it with one or a few settings moved, which is
verified rather than assumed (`tools/diff_arms.py` loads both through the real
loader and diffs the *resolved* settings, since a value an author believed was
inherited may not be).

| Arm | Differs from `fix_en` by | What the arm is for |
| --- | --- | --- |
| `fix_en` | - | The reference arm: phantom gate on, anti-echo on, agent memory on, `max_tokens: 768` |
| `noecho` | `anti_echo_instructions: false` | The control for the template-collapse experiment |
| `ptr_opt` | `co_generate_gist: true` | Pointer requested, not required |
| `ptr_req` | + `gist_required: true` | Pointer required by the schema; measures the retry cost when the model refuses |
| `ptr_mem` | + `pointer_memory: true` | Pointer required plus a decaying pointer for every message - the arm that can actually shrink the prompt |
| `ledger` | `ptr_mem` + `suspicion_ledger: true` | Does a written-down position fix banishment-by-recency? |
| `ledger_room` | `ledger` + `reject_repetition: true` (`repetition_scope: room`) | The ledger and the rejection gate together, to see whether they compose or the extra retries cost more than they buy |
| `run`, `r2`, `r2c4`, `r3` | several settings at once (`name`, `max_tokens`, `max_concurrency`) | Earlier baselines from the concurrency and end-vote work. Left out of the random-deal sweep: they differ in more than one setting, so they cannot be read against the arms above |
| `fix_hl`, `hinglish` | `language` | The language axis, a separate experiment from the mechanics |

## The random-deal arms

The arms above all pin `traitor_names` to the season's three original traitors,
because they are season replays. That makes the deal a constant nobody varies:
every arm draws wilf, amanda and alyssa first, so a behaviour shared by all
seven arms may be a property of those three personas rather than of the flags.
The random-deal variants drop the pin, so the engine draws the traitors from
the seed instead.

`season_uk_s01.<arm>.rand.yaml` is generated from `<arm>` by
`tools/make_rand_arms.py`, and differs from it in exactly three resolved
settings:

| Setting | Arm | Random-deal variant |
| --- | --- | --- |
| `game.traitor_names` | `[wilf, amanda, alyssa]` | unset - drawn from the seed |
| `seed` | `42` | `1` |
| `llm.max_concurrency` | `4` | `1` |

**The seed is a rule, not a taste.** Seed `1` is the lowest seed whose draw
keeps none of the season's traitors *and* is not a holdout seed, which is a
choice anyone can re-derive from that sentence. It draws **imran, kieran and
meryl**. Because the draw is a function of the seed and the cast and nothing
else, every arm in the sweep shares that one deal - so a difference between two
arms is the flag, not a different set of traitors. Changing the cast changes the
draw, so `make_rand_arms.py` re-derives the seed and re-verifies every file it
writes rather than trusting the one recorded here.

**The concurrency is cut because they run together.** Seven arms at the tracked
four apiece would be 28 requests in flight, and the arms themselves record 8 in
flight as clean and 16 as degraded: past what a host serves in parallel, calls
queue into the timeout rather than failing loudly. One per arm keeps the sweep
at 7.

Run the whole set at once:

```bash
uv run --no-sync python tools/make_rand_arms.py        # regenerate and verify
uv run --no-sync python tools/run_rand_sweep.py --base-url http://HOST_A:11434
uv run --no-sync python tools/run_rand_sweep.py --base-url ... --dry-run
```

`run_rand_sweep.py` refuses to start while a blocking bug is open in
[todo.md](todo.md), skips a game id whose run folder already exists (a second
writer would corrupt the log; resume it instead), and writes each arm's output
to `runs/_rand-sweep/<arm>.log` with the pids in `launch.txt`. Reading one while
it runs:

```bash
uv run --no-sync python tools/run_health.py runs/uk-s01-rand-ledger_room
```

Read the game ids as `uk-s01-rand-<arm>`. When they finish, the arm-by-arm
comparison - repeat rate, prompt size, co-generation rate - is
`tools/compare_arms.py`, and `simulation metrics <game_id>` gives one run's LLM
and quality view. `simulation benchmark` is *not* the tool for these runs: it
scores fidelity to the broadcast, and its ground truth names wilf, amanda and
alyssa as the traitors, so the identity-keyed components (roster, banishment
and murder overlap, exit order) are lost by construction.
