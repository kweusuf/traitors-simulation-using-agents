"""Prompt construction, separate from agent logic (spec section 20).

Builds system + user messages from persona, goals, the agent-specific
information view, and recent memory. Everything the model sees comes
from an already-projected `AgentView`, so hidden information cannot
enter the prompt.
"""

from __future__ import annotations

import json
from typing import Optional

from simulation.actions.actions import (
    ACTIONS_REQUIRING_CONTENT,
    ACTIONS_REQUIRING_TARGET,
    CONTENT_CHOICES,
    ActionType,
)
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role
from simulation.models.base import ChatMessage

ACTION_JSON_HINT = (
    'Respond with JSON only, e.g. {"action": "...", "target": "...", '
    '"content": "...", "confidence": 0.8}. No other text.'
)

# Values of `game.language` that mean "no language rule at all". Anything
# else adds the directive, so a config only has to name a language to get
# it and the default keeps every existing game byte-for-byte identical.
_ENGLISH = frozenset({"", "english", "en", "none", "default"})

# Where a player keeps a running position rather than acting. Voting is in
# here because the ballot is the point the ledger exists for; the traitor
# night and the murder pick are not, since a traitor's read of the room is
# not what they are acting on that night.
_LEDGER_PHASES = frozenset({
    ActionType.PUBLIC_MESSAGE,
    ActionType.PRIVATE_MESSAGE,
    ActionType.TRAITOR_MESSAGE,
    ActionType.ACCUSE,
    ActionType.DEFEND,
    ActionType.REBUT,
    ActionType.VOTE,
    ActionType.SHARE_INFORMATION,
})

# Actions where the player is speaking to the room and can move others.
_SPEECH_ACTIONS = frozenset({
    ActionType.PUBLIC_MESSAGE,
    ActionType.PRIVATE_MESSAGE,
    ActionType.ACCUSE,
    ActionType.DEFEND,
    ActionType.REBUT,
    ActionType.SHARE_INFORMATION,
})


def _window(messages, limit: int) -> tuple[list, int]:
    """Newest `limit` entries plus how many were dropped (0 = no limit)."""
    if not limit or len(messages) <= limit:
        return list(messages), 0
    return list(messages[-limit:]), len(messages) - limit


def action_json_hint(
    action_type: ActionType, want_gist: bool = False
) -> str:
    """Example JSON for one action type.

    The example is action-specific because a generic example lets the
    model drop fields (e.g. `content` on a private message) that the
    schema marks optional but the game rules require. `target` and
    `content` are always shown because the structured-output schema
    always requires them; actions without a target say so explicitly
    instead of leaving the model to guess.

    With `want_gist` the example carries a pointer too. It is shown with a
    visibly-too-short placeholder for the same reason `content` shows `?` on
    a closed-choice action: the model copies whatever the example holds, so a
    realistic-looking pointer here would become every player's pointer, and a
    pointer everyone shares is worth nothing.
    """
    example: dict[str, object] = {"action": action_type.value}
    example["target"] = "bob" if action_type in ACTIONS_REQUIRING_TARGET else None
    if action_type in ACTIONS_REQUIRING_CONTENT:
        # A closed-choice action shows a placeholder that is plainly not an
        # answer, never one of the accepted values. The model copies
        # whatever sits in this example, so an example holding "end" made
        # the end vote a unanimous `end` in every season run - which ends
        # the game on any surviving traitor's terms. The response schema
        # constrains the field to the accepted tokens (CONTENT_CHOICES), so
        # the answer is the agent's own and prose cannot appear here.
        example["content"] = (
            "?" if action_type in CONTENT_CHOICES else "your message here"
        )
    else:
        example["content"] = ""
    if want_gist:
        example["gist"] = "one short line"
    example["confidence"] = 0.8
    return (
        f"Respond with JSON only, e.g. {json.dumps(example)}. "
        "Include every field shown in the example. No other text."
    )


class PromptBuilder:
    """Builds prompts, optionally showing only the tail of the transcript.

    `transcript_limit` bounds how many transcript lines each prompt
    carries (0 keeps everything). Late-game prompts reach tens of
    thousands of characters and every one of them is re-evaluated on
    every call, so this is the main lever on call latency.
    """

    def __init__(
        self,
        transcript_limit: int = 0,
        language: str = "english",
        anti_echo_instructions: bool = True,
        want_gist: bool = False,
        gist_required: bool = False,
        suspicion_ledger: bool = False,
        ledger_lines: Optional[list[str]] = None,
    ) -> None:
        self.suspicion_ledger = suspicion_ledger
        # Rendered once per turn by the runtime and handed in, rather than
        # built here: the ledger lives on the agent, and reaching for it from
        # the prompt builder would couple the two.
        self._ledger_lines = list(ledger_lines or [])
        self.transcript_limit = transcript_limit
        self.language = language
        # Whether to ask for a one-line pointer alongside the message, and
        # whether to reject a reply that arrives without one. They are
        # separate because the experiment needs the optional arm: a pointer
        # is a bonus, and making it mandatory turns "the model ignored the
        # extra field" into a validation failure and a retry, which at ~90s
        # a call is expensive enough to distort the very latency the pointer
        # is meant to improve.
        self.want_gist = want_gist
        self.gist_required = gist_required
        # The stylistic constraints on public speech. Switchable because a
        # small model given several style rules at once tends to collapse
        # onto the single template that satisfies all of them: a run with
        # these on had eight different players emit byte-identical
        # messages. Turning them off is the control for that experiment,
        # so the finding can be attributed rather than assumed.
        self.anti_echo_instructions = anti_echo_instructions

    def _language_rule(self) -> list[str]:
        """The directive that moves an agent's speech into another language.

        Only free prose moves. The engine reads `content` as a machine
        token on several actions (recruit/murder, accept/decline,
        end/banish) and rejects anything else, so the rule names those
        words explicitly and tells the agent to leave them in English.
        """
        if self.language.strip().lower() in _ENGLISH:
            return []
        return [
            "",
            "Language (hard rule): speak only in "
            f"{self.language} - Roman script, natural code-switching "
            "between Hindi and English, the way the contestants talk. "
            "Never reply in pure English or in Devanagari. Keep your "
            "reasoning to yourself; this applies to what you say to the "
            "other players, including your first line.",
            "",
            "Exception (harder rule): when an action's `content` is a "
            "single decision word, it must stay exactly that English "
            "word and nothing else - `recruit`, `murder`, `accept`, "
            "`decline`, `end` or `banish`. Any explanation goes in a "
            "different field or is left out entirely. This applies "
            "even though you are speaking Hinglish everywhere else.",
        ]

    def _converted_rule(self, converted: bool) -> list[str]:
        """Coaching for a player converted mid-game.

        Without this, every recruit in both runs was banished within a
        round or two. They were open, accusing faithful players until the
        night they were offered the team, and then had to start deceiving
        with no cover to hide behind. The room reads that change instantly.
        """
        if not converted:
            return []
        return [
            "You were a faithful player until the traitors recruited you, "
            "and the room watched you say so in public. Nothing on the "
            "record has changed: keep believing and saying what you already "
            "said, and keep accusing the players you were already accusing. "
            "A sudden change in who you vote for, who you defend, or how "
            "much you speak is the single most obvious tell there is.",
            "",
            "Play your cover, not the truth. Keep naming a suspect and keep "
            "arguing against them exactly as before. Do not go quiet, do not "
            "suddenly defend someone you attacked, and do not start agreeing "
            "with everyone. You now have a team, but for one more round at "
            "least you should be indistinguishable from the faithful player "
            "the room already knows.",
        ]

    def build_system(
        self,
        agent_id: str,
        role: Role,
        persona: Persona,
        goals: Goals,
        converted: bool = False,
    ) -> str:
        lines = [
            f"You are {agent_id}, a player in a text-based social deduction game.",
            f"Your role: {role.value}",
            "",
            *persona.instructions(),
            "",
            *goals.lines(),
            "",
            "Role secrecy (hard rule): your role is hidden from every other "
            "player. In any public message you must never say or hint that "
            "you are a traitor, never name anyone as a traitor, and never "
            "repeat the roles you privately know as if the group could see "
            "them. Never claim a role you were not given.",
            "",
            "Accusation is the game, secrecy is about knowledge. You may "
            "and should name one suspect and argue in public why their "
            "behaviour looks like a traitor's, and you must answer an "
            "accusation made against you. The line is knowledge, not "
            "suspicion: say what you think and why, never that you know, "
            "and never reveal or hint at your own role.",
            "",
            "Originality (hard rule): every message must be yours alone. "
            "React to one specific thing a named player actually said or "
            "did, take a position on it, and never repeat or rephrase "
            "wording already visible in the transcript above. A generic "
            "observation about caution, patience or how noisy the group "
            "is could have been written by anyone and tells you nothing.",
            "",
            *self._converted_rule(converted),
            "",
            *self._language_rule(),
            "",
        ]
        if role is Role.TRAITOR:
            lines.append(
                "As a traitor you may switch sides: work with your traitor "
                "allies to banish every faithful player and win as a team, or "
                "side with the faithful against a rival traitor and win alone "
                "as the last traitor standing. Both paths keep your own "
                "identity secret: you act through your votes and your "
                "reasoning in discussion, never by announcing that you or "
                "anyone else is a traitor. The other traitors may back you "
                "or may plot against you."
            )
            lines.append("")
        lines.append("Stay in character. Reason only from the information you are given.")
        return "\n".join(lines)

    def build_user(
        self,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]],
        memory_items: Optional[list[dict]] = None,
        extra_instruction: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> str:
        lines = [
            view.render(),
            "",
            f"Current phase: {view.phase.value}.",
            f"Required action type: {action_type.value}.",
        ]
        if "shield" in view.items:
            lines.append(
                "You hold the shield: it blocks the next murder attempt on "
                "you, the attempt is not refunded to the traitors, and you "
                "choose whether to disclose that you have it."
            )
        if "dagger" in view.items:
            lines.append(
                "You hold the dagger: your vote counts twice, and it is "
                "spent the first time you vote."
            )
        if "seer" in view.items:
            lines.append(
                "You hold the seer: you may check one player's true role "
                "once, using the seer_check action during the private_chat "
                "phase. The answer arrives as a private message from the "
                "host and nobody else sees it."
            )
        if action_type is ActionType.SEER_CHECK:
            lines.append(
                "seer_check tells you one player's true role: name the one "
                "living player you most need to read. This is your only "
                "check, so spend it on the player whose allegiance would "
                "change your game."
            )
        if action_type is ActionType.NOMINATE:
            lines.append(
                "Nominate the player you would most want gone: only the "
                "nominated group can be murdered tonight, so put the players "
                "your team cannot afford to keep in front of the knife."
            )
        if action_type is ActionType.RECRUIT:
            if view.phase is GamePhase.TRAITOR_NIGHT:
                lines.append(
                    "Recruitment window: tonight the traitors offer one "
                    "living faithful player a place on the team instead of "
                    "murdering. Name the player who most strengthens the "
                    "traitors; they are told of the offer and may refuse."
                )
            else:
                lines.append(
                    "You are a banished traitor making one final choice: recruit a "
                    "living faithful player onto the traitor team. They become a "
                    "traitor immediately and are told their new role."
                )
        if action_type is ActionType.RECRUIT_DECISION:
            lines.append(
                "Recruitment decision: a traitor left the tower at the round "
                "table, so the traitors may recruit one living faithful player "
                "tonight instead of murdering. Recruiting costs tonight's "
                "murder: nobody dies and the kill is not moved to anyone else. "
                "A lone traitor can force a recruit even if the offer is "
                "refused, because refusing a lone traitor's offer is fatal. "
                "Answer with content 'recruit' or 'murder'."
            )
        if action_type is ActionType.RECRUIT_RESPONSE:
            lines.append(
                "The traitors have offered you a place among them. The offer "
                "is real: accepting makes you a traitor immediately and you "
                "win as one of them. You are allowed to decline. If the "
                "traitors are down to a single player, refusing is fatal: "
                "that lone traitor murders you tonight instead. Answer with "
                "content 'accept' or 'decline'."
            )
        if action_type is ActionType.END_VOTE:
            lines.append(
                "The endgame vote: you may end the game now, or force one "
                "more banishment. Ending is only right when you are "
                "confident that no traitor remains among you; if you still "
                "suspect anyone, force the banishment. Answer with content "
                "'end' or 'banish'."
            )
        if action_type is ActionType.ACCUSE:
            lines.append(
                "The open nomination: name the one living player you most "
                "suspect, and say in the content why their behaviour reads "
                "as a traitor to you. This goes on the public record and "
                "the room will answer it, so make the reason specific: a "
                "claim they made that does not hold up, a vote that did "
                "not fit what they had been saying, or a contradiction "
                "between their public and private words. Take a position; "
                "a hedged nomination wastes the round table."
            )
        if action_type is ActionType.REBUT:
            lines.append(
                "You have been nominated. Answer the room in this message: "
                "state directly why the case against you is wrong, then "
                "name the suspect you think is the real traitor and why, "
                "so the room has somewhere else to look. Do not repeat "
                "what you already said in the debate, and do not reveal "
                "or hint at your own role."
            )
        if action_type is ActionType.PUBLIC_MESSAGE:
            lines.extend(
                [
                    "This message goes to every player. Speak as one player among "
                    "many: no claims about your own role, no naming anyone as a "
                    "traitor, and no repeating role information you only privately "
                    "know. Name the player you are responding to and say something "
                    "this conversation has not heard yet; do not echo phrasing "
                    "from the transcript.",
                    "",
                    # Mechanical, so always on: the marker is factual, and
                    # without it the model answered itself.
                    "You cannot reply to yourself: your own earlier lines are "
                    "marked '(you)' in the transcript, and you are not someone "
                    "else to answer. Address only another player.",
                ]
            )
            if self.anti_echo_instructions:
                lines.extend(
                    [
                        "",
                        "Do not open by paraphrasing whoever spoke last. Opening "
                        "with \"X, you've been...\" or \"X, tumne toh...\" turns "
                        "the discussion into everyone agreeing with the most "
                        "recent speaker. Instead, either name a different player "
                        "and make a specific claim about them, or put forward "
                        "something no one has raised yet.",
                        "",
                        "Take a position rather than validating the room. "
                        "Agreeing with what was just said is the easy move and "
                        "it tells the others nothing about you. If you think "
                        "someone is lying, say so and say what they said that "
                        "does not fit. If you are unsure, say what would change "
                        "your mind.",
                    ]
                )
        if action_type is ActionType.PRIVATE_MESSAGE:
            lines.append(
                "This message goes to one player only. Address them by name "
                "and continue something the two of you actually said; do not "
                "reuse phrasing from the public transcript."
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            # Without this block the model reaches for the public
            # transcript, because that is the only history in front of
            # it: a one-to-one that quotes a third player's post is the
            # observable symptom of a private chat that never happened.
            threads = view.pair_threads()
            if threads:
                lines.append(
                    "Each thread below is your own private history with that "
                    "one player and nobody else can read it. Pick one thread "
                    "and reply inside it: answer what that player last said "
                    "to you or push something you two raised earlier. Do not "
                    "start a new topic from the room, and do not mention "
                    "anyone the thread does not already involve."
                )
        if action_type is ActionType.TRAITOR_MESSAGE:
            lines.append(
                "Traitors only, no faithful player can read this. Argue the "
                "kill: is tonight's victim the biggest threat to your team, "
                "or the death that makes the most convenient suspect? Agree "
                "who takes the blame at the round table and which innocent "
                "you will push the faithful to banish."
            )
        if action_type is ActionType.TRAITOR_KILL:
            lines.append(
                "Pick tonight's victim for a reason that fits your plan: the "
                "player most dangerous to your team, or a player whose death "
                "puts an innocent in the frame for the next banishment. Say "
                "the reason in reason_summary so it matches what you argued "
                "in the traitor council."
            )
        if (
            action_type is ActionType.PUBLIC_MESSAGE
            and view.own_role is Role.TRAITOR
            and view.phase is GamePhase.ROUND_TABLE
        ):
            lines.append(
                "Round table: choose one innocent to take the fall and build "
                "the case against them in public, so the faithful banish one "
                "of their own. Stay inside the secrecy rule, never hint that "
                "you are coordinating, and if suspicion is turning on you, "
                "defend yourself before pushing anyone else."
            )
        if self.suspicion_ledger and action_type in _LEDGER_PHASES:
            lines.extend(self._ledger_lines)
        if action_type in _SPEECH_ACTIONS and self.suspicion_ledger:
            # The agenda is not only for the ballot. A player arguing a case
            # they have not written down reasons from the last message they
            # read, which is what made the room sound like one voice.
            lines.append(
                "You have a position. Do not spend this message summarising "
                "the room. Make the case for the person at the top of your "
                "list, name the specific thing they did, and try to get "
                "others to agree with you by name before the vote. If someone "
                "has already made the case you wanted, build on it instead of "
                "repeating it - and if you have someone you cleared, say so "
                "and defend them by name, because silence lets the table talk "
                "them out of the room."
            )
        if action_type is ActionType.VOTE and view.own_role is Role.TRAITOR:
            lines.append(
                "Vote with the faithful against the innocent you have been "
                "framing, unless the vote is on you or a fellow traitor: then "
                "vote for whichever faithful keeps both of you safe."
            )
        if action_type is ActionType.VOTE and view.own_role is Role.FAITHFUL:
            # The traitor has had vote instructions all along and the
            # faithful did not, which is most of why banishment tracked
            # chance: the ballot asked for a bare `target` with nothing
            # telling the model to deduce anything first. The ledger is
            # shown above; this is what connects it to the vote.
            lines.append(
                "Vote from your list, not from the last thing you read. If "
                "someone has just made a strong case against your top "
                "suspect, you may move - but say what changed your mind. If "
                "you would rather protect someone you have cleared, you may "
                "vote for them, and you will have to justify it. Never vote "
                "for a name that is neither in your ledger nor defended in "
                "this round's discussion. Use reason_summary to name the "
                "specific thing that settled it."
            )
        if legal_targets:
            lines.append("Legal targets: " + ", ".join(sorted(legal_targets)))
        else:
            lines.append(
                'Legal targets: none (this action takes no target; '
                'set "target" to null).'
            )

        if view.public_transcript:
            shown, dropped = _window(
                view.public_transcript, self.transcript_limit
            )
            lines.append("")
            lines.append("Public transcript:")
            for msg in shown:
                # Mark the reader's own past words. Rendering every line
                # identically left the model unable to tell what it had
                # already said from what others had, and it answered
                # itself - "Ivan, you noted that..." sent by Ivan.
                who = (
                    f"{msg.sender_id} (you)"
                    if agent_id is not None and msg.sender_id == agent_id
                    else msg.sender_id
                )
                lines.append(f"  [{msg.round_number}] {who}: {msg.content}")
            if dropped:
                lines.append(
                    f"  (earlier {dropped} public messages are omitted; "
                    "eliminations are listed above)"
                )
        threads = (
            view.pair_threads()
            if action_type is ActionType.PRIVATE_MESSAGE
            else {}
        )
        if threads:
            # A thread's lines are windowed on their own so one busy pair
            # cannot crowd the others out of the prompt.
            per_thread = (
                max(1, self.transcript_limit // len(threads))
                if self.transcript_limit
                else 0
            )
            lines.append("")
            lines.append("Your private conversations, one thread per player:")
            for peer, thread in sorted(threads.items()):
                lines.append(f"  Your conversation with {peer}:")
                shown, dropped = _window(thread, per_thread)
                for msg in shown:
                    who = "you" if msg.sender_id == view.agent_id else peer
                    lines.append(
                        f"    [{msg.round_number}] {who}: {msg.content}"
                    )
                if dropped:
                    lines.append(
                        f"    (earlier {dropped} messages in this thread "
                        "are omitted)"
                    )
        elif view.private_conversations:
            shown, dropped = _window(
                view.private_conversations, self.transcript_limit
            )
            lines.append("")
            lines.append("Your private conversations:")
            for msg in shown:
                peers = ", ".join(
                    r for r in msg.recipients if r != view.agent_id
                ) or "everyone"
                lines.append(
                    f"  [{msg.round_number}] {msg.sender_id} -> {peers}: {msg.content}"
                )
            if dropped:
                lines.append(f"  (earlier {dropped} private messages are omitted)")
        if memory_items:
            lines.append("")
            lines.append("What you remember:")
            for item in memory_items:
                # The subject's status is stated inline so a memory about
                # someone who has left the game can never be mistaken for
                # someone still in it.
                status = ""
                subject = item.get("subjects") or ()
                if subject and subject[0] in (view.eliminated_players or ()):
                    status = " (eliminated)"
                elif subject and subject[0] in (view.alive_players or ()):
                    status = " (still playing)"
                lines.append(f"  - {item['content']}{status}")

        if extra_instruction:
            lines.append("")
            lines.append(extra_instruction)

        if self.want_gist:
            # Asked for on its own line rather than folded into the JSON hint,
            # because the hint states the format and this states the intent,
            # and a model told to do two things at once needs both said plainly.
            lines.append("")
            lines.append(
                "Also in `gist`: one short line naming what you just said, "
                "in your own words, for your own notes later. It is a "
                "pointer, not a summary of your reasoning - the player who "
                "reads it in three rounds should grasp the claim without "
                "seeing this message again. Under 25 words. Do not repeat "
                "your message verbatim and do not leave it empty."
            )

        lines.append("")
        lines.append(action_json_hint(action_type, want_gist=self.want_gist))
        return "\n".join(lines)

    def build(
        self,
        *,
        agent_id: str,
        role: Role,
        persona: Persona,
        goals: Goals,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
        memory_items: Optional[list[dict]] = None,
        extra_instruction: Optional[str] = None,
        agent: Optional["Agent"] = None,
    ) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="system",
                content=self.build_system(
                    agent_id,
                    role,
                    persona,
                    goals,
                    converted=bool(
                        getattr(agent, "converted_round", None) is not None
                    ),
                ),
            ),
            ChatMessage(
                role="user",
                content=self.build_user(
                    view,
                    action_type,
                    legal_targets,
                    memory_items,
                    extra_instruction,
                    agent_id=agent_id,
                ),
            ),
        ]
