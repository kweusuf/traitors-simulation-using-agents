"""Unit tests for the deterministic quality analysis of a run."""

from __future__ import annotations

from simulation.experiments.quality import analyse
from simulation.persistence.event_log import Event, EventType

ROSTER = ["alice", "bob", "charlie", "david"]


def ev(
    seq: int,
    type_: EventType,
    *,
    round: int = 0,
    phase: str = "",
    actor: str | None = None,
    targets: list[str] | None = None,
    **payload,
) -> Event:
    return Event(
        event_id=f"e{seq}",
        game_id="game-001",
        sequence=seq,
        round=round,
        phase=phase,
        type=type_,
        actor=actor,
        targets=targets or [],
        payload=payload,
    )


def base_events() -> list[Event]:
    """A tiny clean run: two traitors, two faithful, one night murder."""
    return [
        ev(1, EventType.GAME_STARTED, players=ROSTER),
        ev(2, EventType.ROLE_ASSIGNED, actor="alice", role="traitor"),
        ev(3, EventType.ROLE_ASSIGNED, actor="bob", role="traitor"),
        ev(4, EventType.ROLE_ASSIGNED, actor="charlie", role="faithful"),
        ev(5, EventType.ROLE_ASSIGNED, actor="david", role="faithful"),
        ev(6, EventType.ROUND_STARTED, round=1),
        ev(
            7,
            EventType.PUBLIC_MESSAGE,
            round=1,
            phase="public_discussion",
            actor="charlie",
            content="I will listen to everyone before I decide anything.",
        ),
        ev(
            8,
            EventType.PUBLIC_MESSAGE,
            round=1,
            phase="public_discussion",
            actor="alice",
            content="Nothing suspicious yet, let us keep talking.",
        ),
        ev(9, EventType.PLAYER_ELIMINATED, round=1, actor="david", method="night"),
        ev(10, EventType.GAME_ENDED, rounds=1, winner="traitor"),
    ]


def message(seq: int, actor: str, content: str, round: int = 1) -> Event:
    return ev(
        seq,
        EventType.PUBLIC_MESSAGE,
        round=round,
        phase="public_discussion",
        actor=actor,
        content=content,
    )


def secrecy_counts(events: list[Event]) -> dict[str, int]:
    return {
        k: v
        for k, v in analyse(events)["secrecy"].items()
        if k != "samples" and isinstance(v, int)
    }


# ----------------------------------------------------------------------
# Clean runs
# ----------------------------------------------------------------------


def test_clean_run_scores_zero_everywhere() -> None:
    result = analyse(base_events())
    assert result["messages_checked"] == 2
    assert result["public_messages"] == 2
    assert result["hallucination_score"] == 0.0
    assert result["hallucination"]["messages_with_contradictions"] == 0
    assert result["secrecy"]["flags_total"] == 0
    assert result["diversity"]["duplicate_messages"] == 0
    assert result["parsing"] == {"rejected_actions": 0, "unparseable_actions": 0}


def test_empty_run_scores_zero() -> None:
    result = analyse([])
    assert result["messages_checked"] == 0
    assert result["hallucination_score"] == 0.0


# ----------------------------------------------------------------------
# Hallucination: text that contradicts the record
# ----------------------------------------------------------------------


def test_fabricated_elimination_is_flagged() -> None:
    events = base_events() + [
        message(20, "bob", "We all know charlie was banished yesterday for lying.")
    ]
    result = analyse(events)
    assert result["hallucination"]["fabricated_eliminations"] == 1
    assert result["hallucination_score"] > 0
    assert result["hallucination"]["samples"][0]["kind"] == "fabricated_elimination"


def test_true_elimination_claim_is_not_flagged() -> None:
    events = base_events() + [
        message(20, "bob", "David was banished last night, that much is clear.")
    ]
    result = analyse(events)
    assert result["hallucination"]["fabricated_eliminations"] == 0
    assert result["hallucination_score"] == 0.0


def test_calling_an_eliminated_player_alive_is_flagged() -> None:
    events = base_events()[:9] + [
        ev(11, EventType.ROUND_STARTED, round=2),
        message(12, "bob", "David is still alive as far as I can tell.", round=2),
    ]
    result = analyse(events)
    assert result["hallucination"]["alive_after_elimination"] == 1
    assert result["hallucination_score"] > 0


def test_inventing_a_round_that_never_ran_is_flagged() -> None:
    events = base_events() + [
        message(20, "bob", "Back in round 7 we already agreed on this.")
    ]
    result = analyse(events)
    assert result["hallucination"]["invented_rounds"] == 1


def test_mentioning_the_current_round_is_fine() -> None:
    events = base_events() + [message(20, "bob", "Round 1 has been quiet so far.")]
    assert analyse(events)["hallucination_score"] == 0.0


# ----------------------------------------------------------------------
# Secrecy: role declarations judged against the sender's real role
# ----------------------------------------------------------------------


def test_traitor_self_declaration_is_flagged() -> None:
    events = base_events() + [
        message(20, "alice", "Might as well say it: I am a traitor, and bob is too.")
    ]
    counts = secrecy_counts(events)
    assert counts["traitor_public_declarations"] == 1
    assert counts["flags_total"] >= 1


def test_faithful_claiming_traitor_is_flagged() -> None:
    events = base_events() + [
        message(20, "charlie", "You will not believe it, I am a traitor.")
    ]
    counts = secrecy_counts(events)
    assert counts["false_role_claims"] == 1
    assert counts["traitor_public_declarations"] == 0


def test_faithful_discussing_the_traitor_team_is_not_flagged() -> None:
    events = base_events() + [
        message(20, "charlie", "The traitor team must be coordinating somehow.")
    ]
    assert secrecy_counts(events)["flags_total"] == 0


def test_traitor_affiliation_phrasing_is_flagged() -> None:
    events = base_events() + [
        message(
            20,
            "alice",
            "I see two of our kind are among us, and I intend to keep it that way.",
        )
    ]
    counts = secrecy_counts(events)
    assert counts["traitor_affiliation_phrases"] == 1
    assert counts["traitor_public_declarations"] == 0  # no first-person claim


def test_traitor_naming_a_fellow_traitor_is_flagged_only_in_affiliation() -> None:
    leaking = base_events() + [
        message(20, "alice", "Bob and I are on the same side in this game.")
    ]
    counts = secrecy_counts(leaking)
    assert counts["traitor_named_teammates"] == 1

    # Turning on a rival traitor is the alliance switching the rules
    # invite, so it must not count as an outing.
    distancing = base_events() + [
        message(20, "alice", "I suspect bob is a traitor and we should vote him out.")
    ]
    assert secrecy_counts(distancing)["traitor_named_teammates"] == 0


def test_recruited_player_is_judged_by_the_role_held_at_the_time() -> None:
    events = base_events()[:6] + [
        ev(7, EventType.ROLE_RECRUITED, round=1, actor="charlie", by="alice"),
        message(8, "charlie", "I am a traitor now, so watch out.", round=1),
        ev(9, EventType.ROUND_STARTED, round=2),
        message(10, "charlie", "As a traitor I say we stick together.", round=2),
        ev(11, EventType.GAME_ENDED, rounds=2, winner="traitor"),
    ]
    counts = secrecy_counts(events)
    # Round 1 messages were written while charlie was still faithful.
    assert counts["false_role_claims"] == 1
    assert counts["traitor_public_declarations"] == 1


def test_messages_are_checked_including_private_ones() -> None:
    private = ev(
        20,
        EventType.PRIVATE_MESSAGE,
        round=1,
        phase="private_chat",
        actor="alice",
        targets=["charlie"],  # a faithful player: a real leak
        content="I am a traitor, trust me.",
    )
    result = analyse(base_events() + [private])
    assert result["messages_checked"] == 3
    assert result["private_messages"] == 1
    assert result["secrecy"]["traitor_public_declarations"] == 1


def test_talk_between_traitors_is_not_a_secrecy_flag() -> None:
    # bob and alice are both traitors in base_events, so an admission
    # between them leaks nothing. Same for the traitor channel.
    whisper = ev(
        20,
        EventType.PRIVATE_MESSAGE,
        round=1,
        phase="private_chat",
        actor="alice",
        targets=["bob"],
        content="I am a traitor, so are you. Let us kill david.",
    )
    council = ev(
        21,
        EventType.PRIVATE_MESSAGE,
        round=1,
        phase="traitor_night",
        actor="alice",
        targets=["bob"],
        channel="role_private",
        content="As a traitor I say we frame charlie and I am a traitor.",
    )
    result = analyse(base_events() + [whisper, council])
    assert result["messages_checked"] == 4
    assert result["secrecy"]["flags_total"] == 0


# ----------------------------------------------------------------------
# Diversity and parsing
# ----------------------------------------------------------------------


def test_identical_text_from_two_authors_is_a_duplicate() -> None:
    text = "I think we should be careful about who we trust next."
    events = base_events() + [message(20, "bob", text), message(21, "charlie", text)]
    result = analyse(events)
    assert result["diversity"]["duplicate_messages"] == 1
    assert result["diversity"]["duplicate_rate"] > 0


def test_same_author_repeating_themselves_is_not_a_duplicate() -> None:
    text = "I am still thinking it over."
    events = base_events() + [message(20, "bob", text), message(21, "bob", text)]
    assert analyse(events)["diversity"]["duplicate_messages"] == 0


def test_rejected_actions_are_counted_by_stage() -> None:
    events = base_events() + [
        ev(20, EventType.ACTION_REJECTED, actor="alice", action="vote", stage="output_parse"),
        ev(21, EventType.ACTION_REJECTED, actor="bob", action="vote", reason="self-vote"),
    ]
    result = analyse(events)
    assert result["parsing"]["rejected_actions"] == 2
    assert result["parsing"]["unparseable_actions"] == 1


def test_recruitment_choice_events_do_not_break_the_analysis() -> None:
    # Phase 26 events carry no transcript text, so they must leave every
    # quality signal alone instead of being misread as speech.
    events = base_events() + [
        ev(20, EventType.RECRUIT_CHOICE_MADE, round=1, choice="recruit",
           votes={"alice": "recruit"}),
        ev(21, EventType.RECRUIT_OFFERED, round=1, actor="alice",
           targets=["charlie"], target="charlie", by="alice"),
        ev(22, EventType.RECRUIT_ACCEPTED, round=1, actor="charlie",
           targets=["alice"], by="alice"),
        ev(23, EventType.ROLE_RECRUITED, round=1, actor="charlie", by="alice"),
        ev(24, EventType.RECRUIT_DECLINED, round=1, actor="david",
           targets=["alice"], by="alice"),
        ev(25, EventType.ULTIMATUM_ISSUED, round=1, actor="alice",
           targets=["david"], target="david", by="alice"),
        message(26, "alice", "The tower is stronger after tonight."),
    ]
    result = analyse(events)
    assert result["messages_checked"] == 3  # only real speech is scored
    assert result["hallucination_score"] == 0.0
    assert result["secrecy"]["flags_total"] == 0


# ----------------------------------------------------------------------
# Duplication score and cross-player speech similarity
# ----------------------------------------------------------------------


def two_player_events(text_a: str, text_b: str) -> list[Event]:
    return [
        ev(1, EventType.GAME_STARTED, players=["alice", "bob"]),
        message(2, "alice", text_a),
        message(3, "bob", text_b),
    ]


def test_duplication_score_counts_distinct_texts() -> None:
    clean = analyse(base_events())
    assert clean["diversity"]["distinct_texts"] == 2
    assert clean["diversity"]["duplication_score"] == 0.0

    repeated = base_events() + [
        message(20, "bob", "Hello there friend, I am listening closely."),
        message(21, "charlie", "Hello there friend, I am listening closely."),
    ]
    result = analyse(repeated)
    # four messages, three distinct texts
    assert result["diversity"]["distinct_texts"] == 3
    assert result["diversity"]["duplication_score"] == 0.25


def test_identical_speech_scores_one_for_that_pair() -> None:
    shared = "Watching the harbour lights while the captain checks the nets twice."
    result = analyse(two_player_events(shared, shared))
    similarity = result["speech_similarity"]

    assert similarity["players_compared"] == 2
    assert similarity["pairs"] == 1
    assert similarity["content_words"]["mean"] == 1.0
    assert similarity["content_words"]["max"] == 1.0
    assert similarity["content_words"]["max_pair"] == ["alice", "bob"]
    assert similarity["phrasing"]["max"] == 1.0
    assert set(similarity["per_player"]) == {"alice", "bob"}
    assert similarity["per_player"]["alice"]["content_words"] == 1.0


def test_different_speech_scores_low() -> None:
    result = analyse(
        two_player_events(
            "Alpha beta gamma delta epsilon zeta.",
            "Quartz jupiter nebula vortex mimic pyre.",
        )
    )
    similarity = result["speech_similarity"]
    assert similarity["content_words"]["mean"] == 0.0
    assert similarity["phrasing"]["max"] < 0.5


def test_similarity_needs_two_speakers() -> None:
    events = [
        ev(1, EventType.GAME_STARTED, players=["alice", "bob"]),
        message(2, "alice", "I will watch everyone closely tonight."),
    ]
    similarity = analyse(events)["speech_similarity"]
    assert similarity["players_compared"] == 1
    assert similarity["pairs"] == 0
    assert similarity["content_words"] == {"mean": 0.0, "max": 0.0, "max_pair": None}
    assert similarity["per_player"] == {}


def test_similarity_ignores_players_who_never_spoke() -> None:
    events = base_events() + [message(20, "bob", "I say nothing much at all.")]
    similarity = analyse(events)["speech_similarity"]
    # alice and charlie spoke in the base run, bob joins; david never did.
    assert similarity["players_compared"] == 3
    assert "david" not in similarity["per_player"]


def test_host_is_never_counted_as_a_player() -> None:
    # The seer's answer arrives as a system message from `host`. It is
    # still checked against the record, but it is not one player's
    # speech and must not skew the per-player similarity.
    host_line = ev(
        20,
        EventType.PRIVATE_MESSAGE,
        round=1,
        phase="private_chat",
        actor="host",
        targets=["charlie"],
        channel="role_private",
        content="bob is a traitor",
    )
    result = analyse(base_events() + [host_line])

    assert result["messages_checked"] == 3
    similarity = result["speech_similarity"]
    assert "host" not in similarity["per_player"]
    assert similarity["players_compared"] == 2  # alice and charlie spoke
    assert result["secrecy"]["flags_total"] == 0
    assert result["hallucination_score"] == 0.0
