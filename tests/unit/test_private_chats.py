"""Private chats with pair history (audit fix, phase 4).

186 private messages across 69 pairs looks healthy, but the samples were
one-to-ones quoting a third player's public post: the prompt told the
model to continue something the pair had said while showing it only the
public transcript, so the model reached for what was in front of it.
These tests cover the pair's own thread reaching the prompt (and only
the pair's, never a third party's chat), and the per-pair counters that
make reciprocity measurable instead of assumed.
"""

from __future__ import annotations

from simulation.actions.actions import ActionType
from simulation.agents.prompts import PromptBuilder
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role
from simulation.experiments.quality import analyse
from simulation.persistence.event_log import Event, EventType

ROSTER = ["alice", "bob", "charlie", "david"]


def ev(
    seq: int,
    type_: EventType,
    *,
    round: int = 0,
    actor: str | None = None,
    targets: list[str] | None = None,
    **payload,
) -> Event:
    return Event(
        event_id=f"e{seq}",
        game_id="game-001",
        sequence=seq,
        round=round,
        phase="",
        type=type_,
        actor=actor,
        targets=targets or [],
        payload=payload,
    )


def base_events() -> list[Event]:
    """A minimal clean run: the roster plus one public message."""
    return [
        ev(1, EventType.GAME_STARTED, players=ROSTER),
        ev(2, EventType.ROLE_ASSIGNED, actor="alice", role="traitor"),
        ev(3, EventType.ROLE_ASSIGNED, actor="charlie", role="faithful"),
        ev(
            4,
            EventType.PUBLIC_MESSAGE,
            round=1,
            actor="charlie",
            content="who wants to hear the task result?",
        ),
        ev(5, EventType.GAME_ENDED, rounds=1, winner="faithful"),
    ]


def private(
    message_id: str,
    sender: str,
    recipients: list[str],
    content: str = "what do you think?",
    round_number: int = 1,
    channel: Channel = Channel.PRIVATE,
) -> Message:
    return Message(
        message_id=message_id,
        sender_id=sender,
        recipients=recipients,
        channel=channel,
        content=content,
        round_number=round_number,
    )


def make_view(**overrides) -> AgentView:
    defaults = dict(
        agent_id="alice",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.PRIVATE_CHAT,
        own_role=Role.FAITHFUL,
        known_roles={"alice": Role.FAITHFUL},
        alive_players=["alice", "bob", "charlie"],
        eliminated_players=[],
        public_transcript=[
            private(
                "m-pub", "david", [], "alice never answers", channel=Channel.PUBLIC
            )
        ],
        private_conversations=[
            private("m1", "bob", ["alice"], "you kept quiet last night"),
            private("m2", "alice", ["bob"], "I was waiting to see who spoke"),
            private("m3", "charlie", ["alice"], "david thinks you are a traitor"),
        ],
        winner=None,
    )
    defaults.update(overrides)
    return AgentView(**defaults)


def build(view: AgentView, action_type: ActionType, **kwargs) -> str:
    builder = PromptBuilder(**kwargs)
    return builder.build_user(view, action_type, legal_targets=["bob", "charlie"])


# ----------------------------------------------------------------------
# The pair's own history reaches the prompt
# ----------------------------------------------------------------------


def test_private_prompt_carries_the_pairs_own_thread() -> None:
    prompt = build(make_view(), ActionType.PRIVATE_MESSAGE)

    assert "Your conversation with bob" in prompt
    assert "you kept quiet last night" in prompt
    assert "I was waiting to see who spoke" in prompt
    # The pair reads as a two-way thread, labelled from both sides.
    assert "you: I was waiting to see who spoke" in prompt


def test_each_thread_is_labelled_with_its_counterpart() -> None:
    prompt = build(make_view(), ActionType.PRIVATE_MESSAGE)

    assert "Your conversation with charlie" in prompt
    # A thread is one player: bob's line never appears under charlie and
    # charlie's never under bob.
    bob_block = prompt.split("Your conversation with bob:")[1].split(
        "Your conversation with charlie:"
    )[0]
    assert "david thinks you are a traitor" not in bob_block
    charlie_block = prompt.split("Your conversation with charlie:")[1]
    assert "you kept quiet last night" not in charlie_block


def test_the_instruction_says_reply_inside_the_thread() -> None:
    prompt = build(make_view(), ActionType.PRIVATE_MESSAGE)

    assert "Pick one thread" in prompt
    assert "answer what that player last said" in prompt
    assert "Do not start a new topic from the room" in prompt


def test_a_first_timer_sees_no_thread_block() -> None:
    # No private history yet: there is no thread to reply inside, so the
    # block is absent rather than an empty heading.
    view = make_view(private_conversations=[])
    prompt = build(view, ActionType.PRIVATE_MESSAGE)

    assert "Your conversation with" not in prompt
    assert "Pick one thread" not in prompt
    assert "This message goes to one player only" in prompt


def test_the_traitor_channel_is_not_a_pair_thread() -> None:
    view = make_view(
        private_conversations=[
            private(
                "m-t",
                "alice",
                ["bob", "charlie"],
                "we agree on tonight",
                channel=Channel.ROLE_PRIVATE,
            )
        ]
    )
    prompt = build(view, ActionType.PRIVATE_MESSAGE)

    # A group channel has no single counterpart, so it stays out of the
    # one-to-one block and falls back to the flat private list.
    assert "Your conversation with" not in prompt
    assert "Your private conversations:" in prompt
    assert "we agree on tonight" in prompt


def test_other_actions_keep_the_flat_private_list() -> None:
    # Only the private-message prompt is restructured; a public turn must
    # not be handed a per-pair breakdown it has no use for.
    prompt = build(make_view(), ActionType.PUBLIC_MESSAGE)

    assert "Your conversation with bob" not in prompt
    assert "Your private conversations:" in prompt


def test_a_busy_thread_cannot_crowd_out_the_others() -> None:
    view = make_view(
        private_conversations=[
            private(f"m{i}", "bob", ["alice"], f"bob line {i}", round_number=i)
            for i in range(6)
        ]
        + [private("mc", "charlie", ["alice"], "charlie line")]
    )
    prompt = build(view, ActionType.PRIVATE_MESSAGE, transcript_limit=4)

    # The budget is split across threads, so charlie's thread survives.
    assert "charlie line" in prompt
    assert "Your conversation with bob" in prompt
    assert "bob line 0" not in prompt  # the oldest bob lines are dropped
    assert "omitted" in prompt


# ----------------------------------------------------------------------
# Per-pair metrics
# ----------------------------------------------------------------------


def private_event(seq: int, sender: str, target: str, round: int = 1):
    return ev(
        seq,
        EventType.PRIVATE_MESSAGE,
        round=round,
        phase="private_chat",
        actor=sender,
        targets=[target],
        content=f"{sender} to {target}",
    )


def test_reciprocity_rate_separates_conversations_from_broadcasts() -> None:
    events = base_events() + [
        # A real thread: three back, three forth.
        private_event(20, "alice", "bob"),
        private_event(21, "bob", "alice"),
        private_event(22, "alice", "bob"),
        private_event(23, "bob", "alice"),
        private_event(24, "alice", "bob"),
        private_event(25, "bob", "alice"),
        # A broadcast: alice talks at charlie, charlie never answers.
        private_event(26, "alice", "charlie"),
    ]
    chats = analyse(events)["private_chats"]

    assert chats["pairs"] == 2
    assert chats["messages"] == 7
    assert chats["two_way_pairs"] == 1
    assert chats["reciprocity_rate"] == 0.5
    assert chats["longest_thread"] == 6
    assert chats["mean_messages_per_pair"] == 3.5
    assert chats["per_player"] == {"alice": 7, "bob": 6, "charlie": 1}


def test_a_pair_direction_is_counted_once_whatever_the_id_order() -> None:
    # The pair key must not depend on who is alphabetically first, or
    # alice-to-bob and bob-to-alice would look like two pairs.
    events = base_events() + [
        private_event(20, "bob", "alice"),
        private_event(21, "alice", "bob"),
    ]
    chats = analyse(events)["private_chats"]

    assert chats["pairs"] == 1
    assert chats["two_way_pairs"] == 1
    assert chats["reciprocity_rate"] == 1.0


def test_pairs_are_bucketed_by_how_long_they_ran() -> None:
    events = base_events() + [
        private_event(20, "alice", "bob"),  # pair of 1
        private_event(21, "alice", "charlie"),
        private_event(22, "charlie", "alice"),  # pair of 2
        private_event(23, "david", "alice"),
        private_event(24, "alice", "david"),
        private_event(25, "david", "alice"),
        private_event(26, "alice", "david"),  # pair of 4
    ]
    chats = analyse(events)["private_chats"]

    assert chats["pairs_by_length"] == {"1": 1, "2": 1, "4": 1}


def test_long_threads_are_capped_in_the_length_buckets() -> None:
    events = base_events() + [
        private_event(20 + i, "alice", "bob") for i in range(8)
    ]
    chats = analyse(events)["private_chats"]

    assert chats["longest_thread"] == 8
    assert chats["pairs_by_length"] == {"5": 1}  # 5 or more reads as "5+"


def test_a_run_without_private_traffic_reports_zeroes_not_an_error() -> None:
    chats = analyse(base_events())["private_chats"]

    assert chats["pairs"] == 0
    assert chats["reciprocity_rate"] == 0.0
    assert chats["per_player"] == {}


def test_group_private_messages_are_not_counted_as_a_pair() -> None:
    # The seer's answer and any group private line have no single
    # counterpart, so they must not invent a pair.
    events = base_events() + [
        ev(
            20,
            EventType.PRIVATE_MESSAGE,
            round=1,
            actor="alice",
            targets=["bob", "charlie"],
            content="to everyone",
        )
    ]
    chats = analyse(events)["private_chats"]

    assert chats["pairs"] == 0
    # It still counts as a private message for the run's totals.
    assert analyse(events)["private_messages"] == 1

