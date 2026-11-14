"""Unit tests for the prompt builder (spec section 20)."""

from __future__ import annotations

from simulation.actions.actions import ActionType
from simulation.agents.goals import (
    MANDATE_CANDID,
    MANDATE_CORE,
    MANDATE_PRACTISED,
    Goals,
)
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder, action_json_hint
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role


def make_view(**overrides) -> AgentView:
    defaults = dict(
        agent_id="alice",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={"alice": Role.FAITHFUL},
        alive_players=["alice", "bob", "charlie"],
        eliminated_players=["david"],
        public_transcript=[
            Message(
                message_id="m1",
                sender_id="eve",
                recipients=[],
                channel=Channel.PUBLIC,
                content="bob voted oddly",
            )
        ],
        private_conversations=[
            Message(
                message_id="m2",
                sender_id="bob",
                recipients=["alice"],
                channel=Channel.PRIVATE,
                content="i trust you",
            )
        ],
        winner=None,
    )
    defaults.update(overrides)
    return AgentView(**defaults)


def build(view: AgentView | None = None, **kwargs) -> list:
    builder = PromptBuilder()
    params: dict = dict(
        action_type=ActionType.PUBLIC_MESSAGE,
        legal_targets=[],
        memory_items=[{"content": "bob was accused"}],
    )
    params.update(kwargs)
    return builder.build(
        agent_id="alice",
        role=Role.FAITHFUL,
        persona=Persona(
            description="Careful player.",
            personality={"analytical": 0.9, "trust": 0.3},
        ),
        goals=Goals(primary="survive", secondary=["identify_traitors"]),
        view=view or make_view(),
        **params,
    )


def test_build_returns_system_and_user_messages() -> None:
    messages = build()
    assert [m.role for m in messages] == ["system", "user"]
    system, user = messages[0].content, messages[1].content

    # System: identity, role, personality, goals.
    assert "You are alice" in system
    assert "Your role: faithful" in system
    assert "weigh evidence" in system
    assert "Primary goal: survive" in system
    assert "identify_traitors" in system

    # User: phase, action type, view, transcript, memories, JSON hint.
    assert "Current phase: public_discussion" in system or "public_discussion" in user
    assert "Required action type: public_message" in user
    assert "Alive players: alice, bob, charlie" in user
    assert "Eliminated: david" in user
    assert "bob voted oddly" in user
    assert "i trust you" in user
    assert "bob was accused" in user
    assert "JSON only" in user


def test_legal_targets_listed() -> None:
    messages = build(action_type=ActionType.VOTE, legal_targets=["bob", "charlie"])
    user = messages[1].content
    assert "Legal targets: bob, charlie" in user


def test_no_legal_targets_states_none() -> None:
    user = build()[1].content
    assert "Legal targets: none" in user


def test_prompt_never_contains_absent_private_content() -> None:
    # View without the private conversation: the prompt must not carry it.
    view = make_view(private_conversations=[])
    user = build(view=view)[1].content
    assert "i trust you" not in user


def test_action_json_hint_covers_required_fields() -> None:
    # The structured-output schema requires action, target and content
    # for every action, so the example always shows all three keys;
    # irrelevant values are null/empty instead of omitted.
    private = action_json_hint(ActionType.PRIVATE_MESSAGE)
    assert '"action": "private_message"' in private
    assert '"target": "bob"' in private
    assert '"content": "your message here"' in private

    vote = action_json_hint(ActionType.VOTE)
    assert '"action": "vote"' in vote
    assert '"target": "bob"' in vote
    assert '"content": ""' in vote

    kill = action_json_hint(ActionType.TRAITOR_KILL)
    assert '"action": "traitor_kill"' in kill
    assert '"target": "bob"' in kill
    assert '"content": ""' in kill

    public = action_json_hint(ActionType.PUBLIC_MESSAGE)
    assert '"content": "your message here"' in public
    assert '"target": null' in public


def test_prompt_uses_action_specific_hint() -> None:
    user = build(action_type=ActionType.PRIVATE_MESSAGE, legal_targets=["bob"])[1].content
    assert action_json_hint(ActionType.PRIVATE_MESSAGE) in user
    assert '"content": "your message here"' in user


def test_extra_instruction_appended() -> None:
    user = build(extra_instruction="Choose carefully.")[1].content
    assert "Choose carefully." in user


# ----------------------------------------------------------------------
# Role secrecy (traitors must not declare themselves in public)
# ----------------------------------------------------------------------


def test_both_roles_get_the_secrecy_rule() -> None:
    builder = PromptBuilder()
    persona = Persona(description="Careful player.")
    for role in (Role.FAITHFUL, Role.TRAITOR):
        system = builder.build_system("alice", role, persona, Goals())
        assert "Role secrecy (hard rule)" in system
        assert "never say or hint that you are a traitor" in system
        assert "Never claim a role you were not given" in system


def test_traitor_prompt_keeps_the_alliance_paths_secret() -> None:
    system = PromptBuilder().build_system(
        "alice", Role.TRAITOR, Persona(description="Careful player."), Goals()
    )
    assert "switch sides" in system
    assert "keep your own identity secret" in system
    # The two win paths must not read as an invitation to announce
    # yourself to the faithful.
    assert "never by announcing that you or anyone else is a traitor" in system


def test_faithful_prompt_has_no_traitor_alliance_paragraph() -> None:
    system = PromptBuilder().build_system(
        "bob", Role.FAITHFUL, Persona(description="Careful player."), Goals()
    )
    assert "switch sides" not in system


# ----------------------------------------------------------------------
# Traitor mandate (an induced role is briefed on what the role requires)
# ----------------------------------------------------------------------


def test_traitor_mandate_reaches_the_traitor_and_scales_to_the_persona() -> None:
    builder = PromptBuilder()
    persona = Persona(
        description="Careful player.", personality={"analytical": 0.9, "trust": 0.3}
    )
    goals = Goals(primary="survive", secondary=["ensure_traitor_team_wins"])

    candid = builder.build_system(
        "meryl", Role.TRAITOR, persona, goals, deception=0.1
    )
    practised = builder.build_system(
        "wilf", Role.TRAITOR, persona, goals, deception=0.9
    )
    faithful = builder.build_system("bob", Role.FAITHFUL, persona, goals)

    # The same non-negotiable core for both, because refusing to lie is not a
    # personality trait - it is not playing.
    assert MANDATE_CORE in candid and MANDATE_CORE in practised
    # The method is the part that scales.
    assert MANDATE_CANDID in candid and MANDATE_CANDID not in practised
    assert MANDATE_PRACTISED in practised and MANDATE_PRACTISED not in candid
    # And it must not leak into a faithful player's prompt.
    assert MANDATE_CORE not in faithful


def test_traitor_mandate_can_be_switched_off_for_the_control_arm() -> None:
    off = PromptBuilder(traitor_mandate=False)
    system = off.build_system(
        "alice", Role.TRAITOR, Persona(description="Careful player."), Goals(),
        deception=0.9,
    )
    assert MANDATE_CORE not in system
    # The rest of the traitor briefing survives; only the mandate is off.
    assert "switch sides" in system


def test_build_supplies_a_deception_score_when_the_caller_does_not() -> None:
    # The probe tools hold only a persona, so `build` has to degrade to a
    # number rather than raise on a traitor turn.
    messages = PromptBuilder().build(
        agent_id="alice",
        role=Role.TRAITOR,
        persona=Persona(
            description="Careful player.",
            personality={"analytical": 0.8, "risk_tolerance": 0.8},
        ),
        goals=Goals(secondary=["build_alliances", "ensure_traitor_team_wins"]),
        view=make_view(own_role=Role.TRAITOR),
        action_type=ActionType.PUBLIC_MESSAGE,
        legal_targets=[],
    )
    assert MANDATE_CORE in messages[0].content
    assert MANDATE_PRACTISED in messages[0].content


# ----------------------------------------------------------------------
# Language (game.language moves an agent's speech, not its instructions)
# ----------------------------------------------------------------------


def test_english_adds_no_language_rule() -> None:
    """The default must leave every existing game byte-for-byte identical."""
    for value in ("english", "en", "", "none"):
        system = PromptBuilder(language=value).build_system(
            "alice", Role.FAITHFUL, Persona(description="Careful."), Goals()
        )
        assert "Language (hard rule)" not in system
        assert "Exception (harder rule)" not in system


def test_hinglish_adds_the_language_rule() -> None:
    system = PromptBuilder(language="hinglish").build_system(
        "alice", Role.FAITHFUL, Persona(description="Careful."), Goals()
    )
    assert "speak only in hinglish" in system
    assert "Roman script" in system
    # The rules that keep the game honest stay in force alongside it.
    assert "Role secrecy (hard rule)" in system
    assert "Originality (hard rule)" in system


def test_language_rule_names_the_machine_tokens() -> None:
    """The decision words the engine parses must be excluded by name.

    `recruit_decision`, `recruit_response` and `end_vote` are validated
    against exact English words, so a directive that translated
    everything would silently break those three actions.
    """
    system = PromptBuilder(language="hinglish").build_system(
        "alice", Role.TRAITOR, Persona(description="Careful."), Goals()
    )
    exception = system.split("Exception (harder rule)")[1]
    for token in ("recruit", "murder", "accept", "decline", "end", "banish"):
        assert f"`{token}`" in exception


def test_language_rule_reaches_both_roles() -> None:
    builder = PromptBuilder(language="hinglish")
    persona = Persona(description="Careful player.")
    for role in (Role.FAITHFUL, Role.TRAITOR):
        assert "speak only in hinglish" in builder.build_system(
            "alice", role, persona, Goals()
        )


def test_public_message_prompt_repeats_the_secrecy_reminder() -> None:
    builder = PromptBuilder()
    public = builder.build_user(make_view(), ActionType.PUBLIC_MESSAGE, [])
    assert "no claims about your own role" in public
    assert "no naming anyone as a traitor" in public

    # Other actions keep their own instructions; the reminder is
    # scoped to the message everybody can read.
    vote = builder.build_user(make_view(), ActionType.VOTE, ["bob", "charlie"])
    assert "no claims about your own role" not in vote


def test_view_labels_role_information_as_private() -> None:
    view = make_view(
        own_role=Role.TRAITOR,
        known_roles={
            "alice": Role.TRAITOR,
            "bob": Role.TRAITOR,
            "charlie": Role.FAITHFUL,
        },
    )
    rendered = view.render()
    assert "Your role: traitor (secret, never reveal it publicly)" in rendered
    assert (
        "Private knowledge of roles (never public): bob=traitor, charlie=faithful"
        in rendered
    )
    assert "Known roles" not in rendered


def test_transcript_window_keeps_only_the_newest_messages() -> None:
    messages = [
        Message(
            message_id=f"m{n}",
            sender_id="eve",
            recipients=[],
            channel=Channel.PUBLIC,
            content=f"public line {n}",
        )
        for n in range(10)
    ]
    view = make_view(public_transcript=messages, private_conversations=[])

    unlimited = PromptBuilder().build_user(view, ActionType.PUBLIC_MESSAGE, [])
    assert "public line 0" in unlimited
    assert "omitted" not in unlimited

    capped = PromptBuilder(transcript_limit=3).build_user(
        view, ActionType.PUBLIC_MESSAGE, []
    )
    assert "public line 9" in capped
    assert "public line 7" in capped
    assert "public line 6" not in capped
    assert "earlier 7 public messages are omitted" in capped


def test_originality_rules_are_in_both_prompts() -> None:
    builder = PromptBuilder()
    persona = Persona(description="Careful player.")
    for role in (Role.FAITHFUL, Role.TRAITOR):
        system = builder.build_system("alice", role, persona, Goals())
        assert "Originality (hard rule)" in system
        assert "never repeat or rephrase" in system

    public = builder.build_user(make_view(), ActionType.PUBLIC_MESSAGE, [])
    assert "Name the player you are responding to" in public
    assert "do not echo phrasing" in public

    private = builder.build_user(
        make_view(), ActionType.PRIVATE_MESSAGE, ["bob"]
    )
    assert "Address them by name" in private
    assert "do not reuse phrasing" in private


def test_night_and_table_guidance_only_for_traitors() -> None:
    builder = PromptBuilder()
    persona = Persona(description="Careful player.")

    traitor_view = make_view(own_role=Role.TRAITOR, phase=GamePhase.ROUND_TABLE)
    traitor_public = builder.build_user(traitor_view, ActionType.PUBLIC_MESSAGE, [])
    assert "choose one innocent to take the fall" in traitor_public
    assert "defend yourself before pushing anyone else" in traitor_public

    faithful_view = make_view(own_role=Role.FAITHFUL, phase=GamePhase.ROUND_TABLE)
    faithful_public = builder.build_user(faithful_view, ActionType.PUBLIC_MESSAGE, [])
    assert "take the fall" not in faithful_public

    traitor_vote = builder.build_user(traitor_view, ActionType.VOTE, ["bob"])
    assert "Vote with the faithful against the innocent" in traitor_vote
    faithful_vote = builder.build_user(faithful_view, ActionType.VOTE, ["bob"])
    assert "Vote with the faithful against the innocent" not in faithful_vote

    council = builder.build_user(traitor_view, ActionType.TRAITOR_MESSAGE, [])
    assert "no faithful player can read this" in council
    assert "biggest threat to your team" in council

    kill = builder.build_user(traitor_view, ActionType.TRAITOR_KILL, ["bob"])
    assert "puts an innocent in the frame" in kill
    assert "reason_summary" in kill

    # Discussion outside the round table keeps the framing advice off.
    discussion = builder.build_user(
        make_view(own_role=Role.TRAITOR, phase=GamePhase.PUBLIC_DISCUSSION),
        ActionType.PUBLIC_MESSAGE,
        [],
    )
    assert "take the fall" not in discussion


# ----------------------------------------------------------------------
# Wave B: item instructions and the new turns
# ----------------------------------------------------------------------


def test_view_renders_only_the_holders_own_items() -> None:
    rendered = make_view(items=["shield"]).render()
    assert "Items: shield" in rendered
    assert "Items:" not in make_view().render()


def test_prompts_tell_the_holder_what_the_item_does() -> None:
    builder = PromptBuilder()

    shield = builder.build_user(make_view(items=["shield"]), ActionType.VOTE, ["bob"])
    assert "blocks the next murder attempt" in shield
    assert "not refunded" in shield
    assert "choose whether to disclose" in shield

    dagger = builder.build_user(make_view(items=["dagger"]), ActionType.VOTE, ["bob"])
    assert "your vote counts twice" in dagger
    assert "spent the first time you vote" in dagger

    seer = builder.build_user(make_view(items=["seer"]), ActionType.VOTE, ["bob"])
    assert "check one player's true role once" in seer
    assert "seer_check action during the private_chat phase" in seer

    # Without an item none of those lines appear.
    plain = builder.build_user(make_view(), ActionType.VOTE, ["bob"])
    for fragment in ("murder attempt", "counts twice", "true role once"):
        assert fragment not in plain


def test_prompts_explain_the_new_turns() -> None:
    builder = PromptBuilder()
    view = make_view(items=["seer"], phase=GamePhase.PRIVATE_CHAT)

    seer_turn = builder.build_user(view, ActionType.SEER_CHECK, ["bob", "charlie"])
    assert "Required action type: seer_check." in seer_turn
    assert "seer_check tells you one player's true role" in seer_turn
    assert "Legal targets: bob, charlie" in seer_turn

    traitor_view = make_view(
        own_role=Role.TRAITOR, phase=GamePhase.TRAITOR_NIGHT, items=[]
    )
    nominate = builder.build_user(traitor_view, ActionType.NOMINATE, ["bob"])
    assert "Required action type: nominate." in nominate
    assert "Nominate the player you would most want gone" in nominate
    assert "nominated group can be murdered tonight" in nominate


def test_action_json_hint_covers_the_new_actions() -> None:
    seer = action_json_hint(ActionType.SEER_CHECK)
    assert '"action": "seer_check"' in seer
    assert '"target": "bob"' in seer
    assert '"content": ""' in seer

    nominate = action_json_hint(ActionType.NOMINATE)
    assert '"action": "nominate"' in nominate
    assert '"target": "bob"' in nominate
    assert '"content": ""' in nominate


# ----------------------------------------------------------------------
# Endgame: the end-or-banish vote
# ----------------------------------------------------------------------


def test_end_vote_prompt_explains_the_choice() -> None:
    builder = PromptBuilder()
    view = make_view(phase=GamePhase.END_VOTE)
    user = builder.build_user(view, ActionType.END_VOTE, [])

    assert "Required action type: end_vote." in user
    assert "you may end the game now, or force one more banishment" in user
    assert "Ending is only right when you are confident" in user
    assert "'end' or 'banish'" in user
    assert "Legal targets: none" in user


def test_end_vote_json_hint_offers_no_answer_of_its_own() -> None:
    hint = action_json_hint(ActionType.END_VOTE)
    assert '"action": "end_vote"' in hint
    assert '"target": null' in hint
    # The example must not hold either answer: the model copies whatever
    # value sits here, and `end` in this slot made the end vote a unanimous
    # `end` in every season run, which stops the game on any surviving
    # traitor's terms. The accepted values live in the instruction and in
    # the enum on the response schema, not in the example.
    assert '"content": "?"' in hint
    assert '"content": "end"' not in hint
    assert '"content": "banish"' not in hint
    # And the instruction is what names them.
    user = PromptBuilder().build_user(
        make_view(phase=GamePhase.END_VOTE), ActionType.END_VOTE, []
    )
    assert "'end' or 'banish'" in user

