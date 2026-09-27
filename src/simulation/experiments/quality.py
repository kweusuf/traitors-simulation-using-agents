"""Deterministic quality checks over a run's own event log (spec 34).

No model is consulted: every signal is derived from what the run
recorded, so the numbers are reproducible from `events.jsonl` alone.
The components are deliberately separate, because "hallucination" in a
social deduction game means different things:

- **hallucination**: generated text that contradicts the game record
  (claiming an elimination that never happened, calling an eliminated
  player alive, inventing a round that never ran, self-declaring a role
  that does not match the dealt one).
- **secrecy**: role declarations that spoil the game, judged against the
  role the sender actually held at that moment (traitor first-person
  self-declaration, traitor affiliation phrasing such as "two of our
  kind", a traitor naming a fellow traitor next to affiliation wording,
  a faithful claiming to be a traitor).
- **diversity**: duplication, both across authors and within one, which
  is how a small model pads discussion. `duplication_score` is
  1 - (distinct texts / messages), so 0.0 means every message is its
  own text and 1.0 means they are all the same.
- **speech similarity**: how alike two players' wording is, measured
  two ways over each player's whole corpus: cosine over content words
  (do they talk about the same things) and cosine over character
  trigrams (do they phrase things the same way). Reported as the mean
  and the most similar pair, plus a per-player average.
- **parsing**: actions the model failed to produce in valid form.

`hallucination_score` is the share of checked messages that contain at
least one record contradiction, from 0.0 (clean) to 1.0 (every message
contradicts the record).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from itertools import combinations
from typing import Any, Optional

from simulation.persistence.event_log import Event, EventType

# Self-declaration, strict (used for both buckets: a traitor outing
# themselves, and a faithful claiming a role they were not dealt).
_SELF_DECLARATION = (
    r"\bi am (?:a |an |the )?traitors?\b",
    r"\bi'm (?:a |an |the )?traitors?\b",
    r"\bas a traitor\b",
    r"\bmy fellow traitors\b",
    r"\bwe are (?:all )?(?:the )?traitors\b",
    r"\bi am one of the traitors\b",
    r"\bi am on the traitor (?:team|side)\b",
    r"\bi am a member of the traitor team\b",
)

# Affiliation phrasing, loose. Only counted when the sender really is
# a traitor, because faithful players discuss the traitor team in the
# abstract all the time.
_AFFILIATION = (
    r"\bmember of the traitor team\b",
    r"\b(?:our|my) traitor (?:team|side|allies)\b",
    r"\bwe have \d+ traitors?\b",
    r"\bthere (?:are|were) \d+ traitors?\b",
    r"\bof our kind\b",
    r"\btwo of us\b",
)

# Words that turn a player name next to them into an affiliation
# statement rather than an accusation.
_AFFILIATION_CONTEXT = re.compile(
    r"traitor|traitors|ally|allies|our kind|our team|same side|we are|the two of us",
    re.IGNORECASE,
)

# ...and the words that make it play rather than a leak: a traitor may
# publicly turn on a rival traitor, which is exactly the alliance
# switching the rules invite, so that must not count as an outing.
_ACCUSATION_CONTEXT = re.compile(
    r"against|suspect|suspicious|accus|banish|vote|eliminat|target|kill|"
    r"liar|lying|fake|pretend|trap|watch",
    re.IGNORECASE,
)

_ELIMINATION_CLAIM = r"{name}\s+(?:was|is|has been)\s+(?:banished|murdered|killed|eliminated|voted out)"
_STILL_ALIVE_CLAIM = r"{name}\s+(?:is|remains|is still)\s+alive"
_ROUND_CLAIM = re.compile(r"\bround\s+(\d+)\b", re.IGNORECASE)

# Word overlap would otherwise be dominated by glue words, so the
# content-word comparison drops the commonest English ones.
_STOPWORDS = frozenset(
    """a an and are as at be but by been can did do does for from had has have
    he her him his i if in into is it its me my no not of on or our out she so
    that the their them then there these they this to too up us was we were
    what when which who will with you your""".split()
)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _content_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z']+", text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _trigrams(text: str) -> list[str]:
    flat = _normalise(text).replace(" ", "_")
    return [flat[i : i + 3] for i in range(max(0, len(flat) - 2))]


def _cosine(left: Counter, right: Counter) -> float:
    if not left or not right:
        return 0.0
    dot = sum(count * right.get(term, 0) for term, count in left.items())
    if not dot:
        return 0.0
    norm_left = math.sqrt(sum(count * count for count in left.values()))
    norm_right = math.sqrt(sum(count * count for count in right.values()))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def _speech_similarity(by_player: dict[str, list[str]]) -> dict[str, Any]:
    """How alike each pair of players is, on content and on phrasing."""
    method = (
        "cosine over each player's whole corpus: content words with "
        "stopwords removed, and character trigrams for phrasing"
    )
    corpora = {pid: " ".join(texts) for pid, texts in by_player.items() if texts}
    players = sorted(corpora)

    def empty() -> dict[str, Any]:
        return {"mean": 0.0, "max": 0.0, "max_pair": None}

    if len(players) < 2:
        return {
            "method": method,
            "players_compared": len(players),
            "pairs": 0,
            "content_words": empty(),
            "phrasing": empty(),
            "per_player": {},
        }

    content = {pid: Counter(_content_tokens(corpora[pid])) for pid in players}
    phrasing = {pid: Counter(_trigrams(corpora[pid])) for pid in players}
    pairs = list(combinations(players, 2))

    content_scores = [
        (a, b, round(_cosine(content[a], content[b]), 4)) for a, b in pairs
    ]
    phrasing_scores = [
        (a, b, round(_cosine(phrasing[a], phrasing[b]), 4)) for a, b in pairs
    ]

    def stats(scores: list[tuple[str, str, float]]) -> dict[str, Any]:
        values = [score for _, _, score in scores]
        top = max(scores, key=lambda item: (item[2], item[0], item[1]))
        return {
            "mean": round(sum(values) / len(values), 4),
            "max": top[2],
            "max_pair": [top[0], top[1]],
        }

    per_player: dict[str, dict[str, float]] = {}
    for pid in players:
        mine_content = [s for a, b, s in content_scores if pid in (a, b)]
        mine_phrasing = [s for a, b, s in phrasing_scores if pid in (a, b)]
        per_player[pid] = {
            "content_words": round(sum(mine_content) / len(mine_content), 4),
            "phrasing": round(sum(mine_phrasing) / len(mine_phrasing), 4),
        }

    return {
        "method": method,
        "players_compared": len(players),
        "pairs": len(pairs),
        "content_words": stats(content_scores),
        "phrasing": stats(phrasing_scores),
        "per_player": per_player,
    }


_SAMPLE_EXCERPT = 180
_NAME_WINDOW = 80  # characters around a name searched for context


def _excerpt(text: str) -> str:
    clean = " ".join(text.split())
    return clean[:_SAMPLE_EXCERPT]


def _matches(patterns: tuple[str, ...], text: str) -> list[str]:
    lowered = text.lower()
    return [p for p in patterns if re.search(p, lowered)]


def _declares_role(text: str) -> bool:
    return bool(_matches(_SELF_DECLARATION, text))


def analyse(events: list[Event]) -> dict[str, Any]:
    """Score a run's messages against its own record."""
    roster: list[str] = []
    base_roles: dict[str, str] = {}
    recruited_round: dict[str, int] = {}
    eliminated_round: dict[str, int] = {}
    total_rounds = 0
    messages: list[Event] = []
    rejected = 0
    unparseable = 0

    for event in events:
        if event.type is EventType.GAME_STARTED:
            roster = list(event.payload.get("players", []))
            base_roles = {pid: "faithful" for pid in roster}
        elif event.type is EventType.ROLE_ASSIGNED and event.actor:
            base_roles[event.actor] = str(event.payload.get("role", ""))
        elif event.type is EventType.ROLE_RECRUITED and event.actor:
            recruited_round[event.actor] = event.round
        elif event.type is EventType.PLAYER_ELIMINATED and event.actor:
            eliminated_round[event.actor] = event.round
        elif event.type in (EventType.PUBLIC_MESSAGE, EventType.PRIVATE_MESSAGE):
            messages.append(event)
        elif event.type is EventType.ROUND_STARTED:
            total_rounds = max(total_rounds, event.round)
        elif event.type is EventType.GAME_ENDED:
            rounds = event.payload.get("rounds")
            if isinstance(rounds, int):
                total_rounds = max(total_rounds, rounds)
        elif event.type is EventType.ACTION_REJECTED:
            rejected += 1
            if event.payload.get("stage") == "output_parse":
                unparseable += 1

    def role_at(player_id: str, round_number: int) -> str:
        """The role the player held when that message was written.

        A player recruited at the end of round R was still faithful
        while round R was being discussed, so their round R messages
        must be judged as a faithful player's.
        """
        joined = recruited_round.get(player_id)
        if joined is not None and round_number > joined:
            return "traitor"
        return base_roles.get(player_id, "")

    hall_samples: list[dict[str, Any]] = []
    secrecy_samples: list[dict[str, Any]] = []
    fabrications_by_message = 0
    fabricated_eliminations = 0
    alive_after_elimination = 0
    invented_rounds = 0
    traitor_declarations = 0
    traitor_affiliations = 0
    traitor_named_teammates = 0
    false_role_claims = 0
    content_seen: dict[str, str] = {}
    duplicate_messages = 0
    by_player: dict[str, list[str]] = {}
    distinct_texts: set[str] = set()

    def note(bucket: list[dict[str, Any]], kind: str, event: Event, detail: str) -> None:
        if len(bucket) < 20:
            bucket.append(
                {
                    "kind": kind,
                    "round": event.round,
                    "sender": event.actor,
                    "detail": detail,
                    "excerpt": _excerpt(str(event.payload.get("content", ""))),
                }
            )

    for event in messages:
        content = str(event.payload.get("content", ""))
        sender = event.actor or ""
        violations = 0
        if sender:
            by_player.setdefault(sender, []).append(content)
        if content.strip():
            distinct_texts.add(_normalise(content))

        # 1. Claims the record contradicts.
        for name in roster:
            if name not in eliminated_round and re.search(
                _ELIMINATION_CLAIM.format(name=name), content, re.IGNORECASE
            ):
                fabricated_eliminations += 1
                violations += 1
                note(
                    hall_samples,
                    "fabricated_elimination",
                    event,
                    f"claims {name} was eliminated; {name} never was",
                )
            if (
                name in eliminated_round
                and eliminated_round[name] < event.round
                and re.search(_STILL_ALIVE_CLAIM.format(name=name), content, re.IGNORECASE)
            ):
                alive_after_elimination += 1
                violations += 1
                note(
                    hall_samples,
                    "alive_after_elimination",
                    event,
                    f"calls {name} alive; eliminated in round {eliminated_round[name]}",
                )

        for match in _ROUND_CLAIM.finditer(content):
            claimed = int(match.group(1))
            if total_rounds and claimed > total_rounds:
                invented_rounds += 1
                violations += 1
                note(
                    hall_samples,
                    "invented_round",
                    event,
                    f"mentions round {claimed}; the game reached round {total_rounds}",
                )

        # 2. Role secrecy, judged against the role the sender actually
        # held at that moment in the game.
        sender_role = role_at(sender, event.round) if sender else ""
        if sender and sender_role == "traitor":
            strict = _matches(_SELF_DECLARATION, content)
            loose = _matches(_AFFILIATION, content)
            if strict:
                traitor_declarations += 1
                note(
                    secrecy_samples,
                    "traitor_public_declaration",
                    event,
                    f"{sender} is a traitor and said so in a "
                    f"{event.type.value.lower()} (matched {strict[0]})",
                )
            elif loose:
                traitor_affiliations += 1
                note(
                    secrecy_samples,
                    "traitor_affiliation_phrase",
                    event,
                    f"{sender} is a traitor and used affiliation wording "
                    f"(matched {loose[0]})",
                )
            # Naming a fellow traitor next to affiliation wording is an
            # outing in all but the literal first person. Naming them
            # next to an accusation is play, not a leak, and is ignored.
            for fellow in roster:
                if fellow == sender or role_at(fellow, event.round) != "traitor":
                    continue
                for match in re.finditer(re.escape(fellow), content, re.IGNORECASE):
                    window = content[
                        max(0, match.start() - _NAME_WINDOW) : match.end() + _NAME_WINDOW
                    ]
                    if _AFFILIATION_CONTEXT.search(window) and not (
                        _ACCUSATION_CONTEXT.search(window)
                    ):
                        traitor_named_teammates += 1
                        note(
                            secrecy_samples,
                            "traitor_named_teammate",
                            event,
                            f"{sender} named fellow traitor {fellow} next to "
                            "affiliation wording",
                        )
                        break
        elif sender and sender_role == "faithful" and _declares_role(content):
            false_role_claims += 1
            note(
                secrecy_samples,
                "faithful_claimed_traitor",
                event,
                f"{sender} is faithful but claims to be a traitor",
            )

        # 2. Diversity: identical text from a different author.
        if content:
            first_author = content_seen.get(content)
            if first_author is None:
                content_seen[content] = sender
            elif first_author != sender:
                duplicate_messages += 1

        if violations:
            fabrications_by_message += 1

    checked = len(messages)
    public = sum(1 for e in messages if e.type is EventType.PUBLIC_MESSAGE)
    role_claim_total = traitor_declarations + false_role_claims

    return {
        "messages_checked": checked,
        "public_messages": public,
        "private_messages": checked - public,
        "hallucination_score": round(fabrications_by_message / checked, 4)
        if checked
        else 0.0,
        "hallucination": {
            "messages_with_contradictions": fabrications_by_message,
            "fabricated_eliminations": fabricated_eliminations,
            "alive_after_elimination": alive_after_elimination,
            "invented_rounds": invented_rounds,
            "samples": hall_samples,
        },
        "secrecy": {
            "traitor_public_declarations": traitor_declarations,
            "traitor_affiliation_phrases": traitor_affiliations,
            "traitor_named_teammates": traitor_named_teammates,
            "false_role_claims": false_role_claims,
            "role_claim_total": role_claim_total,
            "flags_total": (
                traitor_declarations
                + traitor_affiliations
                + traitor_named_teammates
                + false_role_claims
            ),
            "samples": secrecy_samples,
        },
        "diversity": {
            "duplicate_messages": duplicate_messages,
            "duplicate_rate": round(duplicate_messages / checked, 4)
            if checked
            else 0.0,
            "distinct_texts": len(distinct_texts),
            "duplication_score": round(1 - len(distinct_texts) / checked, 4)
            if checked
            else 0.0,
        },
        "speech_similarity": _speech_similarity(by_player),
        "parsing": {
            "rejected_actions": rejected,
            "unparseable_actions": unparseable,
        },
    }
