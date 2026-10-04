"""Configuration loading (spec section 22): everything possible is config-driven."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import Field, model_validator

from simulation.engine.state import GamePhase
from simulation.models.base import StrictModel
from simulation.models.llm import ModelConfig

# Phase names that make up the configurable round loop (spec section 7).
# SETUP opens the game and GAME_END closes it, so neither is configurable.
# END_VOTE is driven by the finale loop only, so it is not configurable
# either: listing it in a config's `phases` is an error.
CONFIGURABLE_PHASES = frozenset(
    p.value
    for p in GamePhase
    if p not in (GamePhase.SETUP, GamePhase.GAME_END, GamePhase.END_VOTE)
)


class GameSettings(StrictModel):
    name: str = "traitors"
    players: int = Field(default=6, ge=3)
    traitors: int = Field(default=2, ge=1)
    faithful: Optional[int] = None
    max_rounds: int = Field(default=5, ge=1)
    allow_self_vote: bool = False
    # Who wins when max_rounds is exhausted without an elimination victory.
    round_limit_winner: str = "faithful"
    # Recruitment: when a traitor is banished at the round table, they get
    # to convert one living faithful player before leaving. `max_recruits`
    # caps it for the whole game (0 = no cap); without a cap the faithful
    # can never empty the traitor team by voting alone.
    recruit_on_banish: bool = False
    max_recruits: int = Field(default=0, ge=0)
    # Recruitment as a choice (phase 26): recruitment only opens on the
    # night after a traitor is banished at the round table, and the
    # traitors vote whether to recruit instead of murder. A tie falls to
    # murder. On a recruit night the traitors offer one living faithful
    # player, who may accept or decline; a lone traitor's offer is an
    # ultimatum (a decline is fatal). Off by default so existing configs
    # keep the plain `recruit_on_banish` behaviour, which this flag
    # overrides when both are set.
    recruit_choice: bool = False
    # Whether `recruit_choice` needs the banishment window at all. True
    # (the default) reproduces the show: the traitors get to choose
    # recruit-or-murder only on the night after one of their own was
    # banished by vote, and the window is spent that night whether they
    # recruit or kill. False drops the gate and offers the choice on
    # every traitor night - they can hold a vacancy open as long as
    # they like and convert whenever they choose. Either way the living
    # traitor count may never exceed the number the game started with,
    # so a recruit can only ever fill a slot a banishment opened.
    recruit_window: bool = True
    # Agent memory: a decaying, per-observer memory of the other players.
    # On by default, because an agent that cannot remember the round table
    # plays a different and much weaker game. With it on, an agent is shown
    # only the memories still worth having: salience decays by
    # `memory_decay` per round and is scaled by how close the observer is to
    # the subject, so trivia fades in a couple of rounds while a named
    # accusation or a private bequest stays. What a player may remember is
    # scoped by the show's own rules - see
    # environments/traitors/memory.py, and in particular the traitor
    # council, which never reaches a faithful player. Set it false to run
    # a memoryless control.
    agent_memory: bool = True
    # Reject a message that names a player who does not exist, rather than
    # logging it and letting it through. A model that invents a player
    # early can occupy the whole room: in one run a phantom was in 82% of
    # messages and the faithful never found a real traitor. Off by default
    # because the detector is not calibrated - a false positive discards a
    # legitimate turn. Run once with it off, read `phantoms` in the run
    # summary, then turn it on.
    reject_invented_players: bool = False
    # Reject a message that repeats something already said, and ask again
    # with the rejected text quoted back. A small model settles into one
    # sentence shape and reuses it: byte-identical messages are the obvious
    # case, but near-duplicates are the expensive one, because eight
    # players agreeing in slightly different words reads as agreement
    # while carrying no new information. Off by default because the
    # threshold is a judgement call - calibrate it with
    # tools/repeat_rate.py against a completed run first.
    reject_repetition: bool = False
    # Share of content words two messages must share to count as a repeat.
    #
    # Calibrated by replaying a completed run (tools/repetition_rates.py):
    # 48% of its public messages score >= 0.6 against something said earlier,
    # because players legitimately keep returning to the same handful of
    # players and the same handful of claims. A threshold anywhere near 0.6
    # would throw away half a real game. What separates copying from
    # discussion there is exactness, not overlap: 17.5% of messages are
    # byte-identical to an earlier one, and that is the template collapse
    # this gate exists to stop. So the default requires an exact match.
    #
    # Lower it to catch paraphrases only if a run shows the collapse taking a
    # paraphrased form, and expect real messages to be discarded with it.
    repetition_threshold: float = Field(default=1.0, gt=0.0, le=1.0)
    # "self" rejects a player repeating their own earlier message.
    # "room" also rejects a player echoing the last speaker, which is the
    # conversational-following behaviour a small model does constantly.
    repetition_scope: Literal["self", "room"] = "self"
    # Replies shorter than this are only rejected on an exact match. Below
    # it, content-word similarity is too noisy to act on: "No." is not a
    # repetition of "No."
    repetition_min_words: int = Field(default=5, ge=0, le=40)
    # Stylistic constraints on public speech: do not paraphrase the last
    # speaker, take a position rather than validate the room. Off is the
    # control for an experiment, not a recommendation: with them on, a small
    # model collapsed onto one template and eight different players emitted
    # byte-identical messages. The mechanical rule that stops a player
    # replying to itself is not part of this flag, because it is factual
    # rather than stylistic and does not invite a template.
    anti_echo_instructions: bool = True
    memory_decay: float = Field(default=0.6, gt=0.0, le=1.0)
    memory_floor: float = Field(default=0.5, ge=0.0)
    memory_items_in_prompt: int = Field(default=6, ge=1, le=20)
    # Ask the model for a one-line pointer to its own message, in the same
    # call that writes the message. The pointer costs no extra generation and
    # is what later prompts keep instead of the full 800-character message.
    #
    # This is an experiment knob, not a recommended default, and it is off by
    # default for two reasons. It asks a small model to do two things at once,
    # and this one already drops fields from a reply when a field is not
    # prominent (see `Action.__get_pydantic_json_schema__`). And a pointer
    # that comes back as a copy of the message's opening clause is not a
    # distillation at all. Neither failure is fatal - `usable_pointer` falls
    # back to the deterministic clause - but "falls back silently" is exactly
    # the kind of thing that looks like it worked, so the gist is recorded in
    # the run folder and the co-generation rate is meant to be measured.
    co_generate_gist: bool = False
    # Make the pointer mandatory: a reply that arrives without one is not
    # valid against the schema, so the provider refuses it and the call is
    # retried. Only meaningful with `co_generate_gist` - it changes the
    # grammar, not the request, so on its own it does nothing.
    #
    # This arm is measured because the failure it causes is worth knowing
    # about: a model that will not produce the pointer spends the budget on
    # retries instead of messages, which is expensive precisely when the
    # prompt is already slow.
    gist_required: bool = False
    # Store a decaying pointer for *every* message, not just the ones the
    # show's rules make worth keeping (accusations, bequests, deaths).
    #
    # This is what lets the transcript shrink: a message is remembered as one
    # short line that fades, so old exchanges leave the prompt by themselves
    # instead of riding along until they fall out of the window. Off by
    # default because it is a large behavioural change - every message becomes
    # a memory - and the trade has not yet been measured against the repeat
    # rate it is meant to fix.
    pointer_memory: bool = False
    # The language every agent speaks in. The instructions stay English;
    # only what an agent says changes. `hinglish` is Roman-script Hindi
    # mixed with English, the way the Indian contestants talk. Any action
    # whose `content` the engine reads as a machine token (`recruit`,
    # `murder`, `accept`, `decline`, `end`, `banish`) must stay that
    # exact English word or the validator rejects it - only free prose is
    # translated, which is why this is a prompt directive and not a
    # translation pass over the response.
    language: str = "english"
    # Finale: normal play stops when exactly `finale_traitors` traitors
    # and `finale_faithful` faithful are alive, and rapid-fire voting
    # decides the winner (0/0 disables the finale and keeps the plain
    # parity win).
    finale_traitors: int = Field(default=0, ge=0)
    finale_faithful: int = Field(default=0, ge=0)
    # The show starts its endgame at the final five, whatever the split,
    # which the faction pair above cannot express once recruitment keeps
    # the traitor count topped up. 0 disables the total rule.
    finale_total: int = Field(default=0, ge=0)
    # How many consecutive rapid-fire rounds may pass with nobody
    # banished before `round_limit_winner` is declared instead.
    finale_max_votes: int = Field(default=10, ge=1)
    # Seasonal cadence: round numbers on which the traitors do not
    # murder at all, and round numbers on which the round table votes
    # on nobody. Empty by default, so default games are unchanged.
    quiet_murder_rounds: list[int] = Field(default_factory=list)
    quiet_banishment_rounds: list[int] = Field(default_factory=list)
    # Endgame (phase 25): with `endgame_vote` on, each finale iteration
    # ends with every living player answering `end` (finish the game) or
    # `banish` (force another vote). `blind_finale_banishments` hides the
    # roles of players banished during the finale until the game ends,
    # which is how the show plays out its final round table.
    endgame_vote: bool = False
    blind_finale_banishments: bool = False
    # Hosted debate clock (audit fix, phase 1): how the deterministic
    # host paces timed discussion. `discussion_budget` caps the open
    # speaking turns of one debate; `warning_turns` is how many closing
    # turns run after the host warns that time is almost up. 0 disables
    # the clock and keeps the current one-turn-each phases unchanged.
    discussion_budget: int = Field(default=0, ge=0)
    warning_turns: int = Field(default=1, ge=0)
    # Hosted round table (audit fix, phase 2): open nomination plus a
    # restricted revote when the banishment ballot ties. Off by default
    # so existing games keep the plain tally-then-tie behaviour.
    # `nomination_keep` is how many of the most-nominated suspects stay
    # standing to answer the room (and, on a tie, to face the revote).
    nomination_enabled: bool = False
    revote_enabled: bool = False
    nomination_keep: int = Field(default=2, ge=1)
    # Sequential traitor council (audit fix, phase 3): the night's kill
    # is decided in two rounds instead of one blind ballot. Round 1 is a
    # proposal from each traitor in turn - each one lands on the traitor
    # channel before the next traitor writes - and round 2 is every
    # traitor holding or switching at the same time, where the majority
    # of the final picks wins. Off by default so existing games keep the
    # single concurrent choice.
    council_deliberation: bool = False
    # `shield`: a one-shot item that blocks the next murder on its holder.
    # `dagger`: a one-shot item whose holder's vote counts twice.
    # `seer`: a one-shot item that checks one player's true role in private.
    # `on_trial`: traitors nominate a murder shortlist before the kill.
    # (Wave B mechanics from plan section 4, each off by default so
    # existing configs and games play exactly as before.)
    shield: bool = False
    dagger: bool = False
    seer: bool = False
    on_trial: bool = False
    player_names: Optional[list[str]] = None
    # Pin the traitor roles to named players instead of drawing them from
    # the seed. Used to replay a real season, where the traitors are known.
    traitor_names: Optional[list[str]] = None
    # Persona names resolved against the configs/personas directory;
    # assigned round-robin when there are fewer names than players.
    personas: Optional[list[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _check_counts(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        players = data.get("players", 6)
        traitors = data.get("traitors", 2)
        if traitors >= players:
            raise ValueError("traitors must be fewer than players")
        faithful = players - traitors
        if data.get("faithful") is not None and data["faithful"] != faithful:
            raise ValueError("faithful must equal players - traitors")
        data["faithful"] = faithful
        if data.get("round_limit_winner", "faithful") not in {"faithful", "traitor"}:
            raise ValueError("round_limit_winner must be 'faithful' or 'traitor'")
        finale_total = data.get("finale_total", 0)
        if finale_total and finale_total < 2:
            raise ValueError("finale_total must be at least 2 when set")
        if bool(data.get("finale_traitors", 0)) != bool(data.get("finale_faithful", 0)):
            raise ValueError(
                "finale_traitors and finale_faithful must be set together (or both 0)"
            )
        finale_traitors = data.get("finale_traitors", 0)
        if finale_traitors and finale_traitors >= players:
            raise ValueError("finale_traitors must be fewer than players")
        traitor_names = data.get("traitor_names")
        if traitor_names:
            if len(traitor_names) != traitors:
                raise ValueError(
                    "traitor_names must list exactly `traitors` players"
                )
            names = data.get("player_names")
            if names:
                unknown = [n for n in traitor_names if n not in names]
                if unknown:
                    raise ValueError(
                        f"traitor_names not in player_names: {sorted(unknown)}"
                    )
        return data


class CommunicationSettings(StrictModel):
    public_messages_per_agent: int = Field(default=1, ge=0)
    private_messages_per_agent: int = Field(default=2, ge=0)
    # How many transcript lines a prompt carries (newest last, 0 = all).
    # The full transcript grows past forty thousand characters in a long
    # game, and every extra token is prompt-evaluation time per call.
    transcript_messages_per_prompt: int = Field(default=40, ge=0)


class LLMSettings(StrictModel):
    """The `llm:` YAML block: model selection and model details.

    Everything about which model runs and how (spec section 19, 22)
    lives here so the game config stays the single source of truth.
    """

    provider: str = "ollama"
    model: str = "gpt-oss:20b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 512
    reasoning_effort: str = "medium"
    timeout_seconds: int = 120
    # Extra attempts for transient failures (timeouts, 5xx) before the
    # run gives up; backoff lives in the Ollama provider.
    retries: int = Field(default=3, ge=0)
    max_concurrency: int = Field(default=2, ge=1)
    # Provider-specific knobs the normalized fields do not cover.
    options: dict[str, Any] = Field(default_factory=dict)

    def to_model_config(self) -> ModelConfig:
        """Build the provider-facing normalized model config."""
        return ModelConfig(
            provider=self.provider,
            name=self.model,
            base_url=self.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
            reasoning_effort=self.reasoning_effort,
            options=dict(self.options),
        )


class ObservabilitySettings(StrictModel):
    enabled: bool = False
    provider: str = "langfuse"


class GameConfig(StrictModel):
    game: GameSettings = Field(default_factory=GameSettings)
    phases: list[str] = Field(
        default_factory=lambda: [
            "mission",
            "public_discussion",
            "private_chat",
            "round_table",
            "voting",
            "elimination",
            "traitor_night",
        ]
    )
    communication: CommunicationSettings = Field(default_factory=CommunicationSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    seed: int = 42
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @model_validator(mode="after")
    def _check_phases(self) -> "GameConfig":
        if not self.phases:
            raise ValueError("phases must not be empty")
        unknown = set(self.phases) - CONFIGURABLE_PHASES
        if unknown:
            raise ValueError(f"unknown phases: {sorted(unknown)}")
        if "voting" not in self.phases:
            raise ValueError("phases must include 'voting'")
        return self


def load_config(path: str | Path) -> GameConfig:
    """Load and validate a YAML game config, failing loudly on bad input."""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must contain a YAML mapping")
    return GameConfig.model_validate(data)
