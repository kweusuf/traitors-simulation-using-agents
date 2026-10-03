"""What is actually in the prompt, section by section.

`prompt_chars` in a run folder says a prompt was 35,000 characters. It
does not say what those characters were spent on, which is the difference
between "the transcript is too big" and "the transcript is a reasonable
size and something else is eating the budget".

Sections are measured by rendering the same view with parts excluded and
differencing, so each number is real builder output rather than an
estimate.

Usage:  python tools/prompt_composition.py runs/uk-s01-en5 [round]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.actions.actions import ActionType  # noqa: E402
from simulation.agents.goals import Goals  # noqa: E402
from simulation.agents.prompts import PromptBuilder  # noqa: E402
from simulation.agents.persona import Persona  # noqa: E402
from simulation.communication.channels import Channel, Message  # noqa: E402
from simulation.communication.visibility import AgentView  # noqa: E402
from simulation.engine.state import GamePhase, Role  # noqa: E402

PUBLIC = "PUBLIC_MESSAGE"
PRIVATE = "PRIVATE_MESSAGE"


def to_messages(events: list[dict], kind: str, upto: int) -> list[Message]:
    channel = Channel.PUBLIC if kind == PUBLIC else Channel.PRIVATE
    return [
        Message(
            message_id=e["payload"]["message_id"],
            sender_id=e["actor"],
            recipients=list(e.get("targets") or []),
            channel=channel,
            content=e["payload"]["content"],
            round_number=e["round"],
        )
        for e in events
        if e.get("type") == kind and e["round"] <= upto
    ]


def build_view(events: list[dict], target_round: int) -> AgentView:
    """One agent's view as of the first public call in the target round."""
    this_round = [
        e for e in events
        if e.get("type") == PUBLIC and e.get("round") == target_round
    ]
    # Read as a sender early in the round, before its messages land: the
    # transcript they saw is the history that caused any repetition.
    reader = this_round[0]["actor"] if this_round else "aaron"
    alive = [e["actor"] for e in events if e.get("type") == "ROLE_ASSIGNED"]
    out = [e["actor"] for e in events if e.get("type") == "PLAYER_ELIMINATED"]
    return AgentView(
        agent_id=reader,
        game_id="reconstructed",
        round_number=target_round,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={p: Role.FAITHFUL for p in alive},
        alive_players=sorted(set(alive) - set(out)),
        eliminated_players=sorted(set(out)),
        public_transcript=to_messages(events, PUBLIC, target_round),
        private_conversations=to_messages(events, PRIVATE, target_round),
    )


def measure(builder: PromptBuilder, view: AgentView,
            action: ActionType) -> dict[str, int]:
    """Chars per section, by differencing against a stripped render."""
    def size(v: AgentView) -> int:
        return len(builder.build_user(v, action, ["aaron"]))

    header = len(view.render())
    full = size(view)
    without_public = size(view.model_copy(update={"public_transcript": []}))
    without_private = size(view.model_copy(
        update={"private_conversations": []}))
    system = len(builder.build_system(
        "aaron", Role.FAITHFUL,
        Persona(description="A careful player."), Goals(),
    ))
    targets = len(", ".join(sorted(view.alive_players)))
    # The public block includes its own "Public transcript:" header line,
    # which is part of the section rather than framing.
    pub = full - without_public - header - targets
    priv = full - without_private - header - targets
    return {
        "system prompt (fixed)": system,
        "state header": header,
        "legal targets": targets,
        "public transcript": pub,
        "private threads": priv,
        # Everything that is neither transcript: the phase and action
        # instructions, the originality and secrecy rules, and memory.
        "instructions + memory": full - header - targets - pub - priv,
        "TOTAL user prompt": full,
        "TOTAL with system": full + system,
    }

def main() -> None:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-en5")
    events = [json.loads(l) for l in open(run / "events.jsonl")]
    rounds = sorted({e["round"] for e in events if e.get("round")})
    target = int(sys.argv[2]) if len(sys.argv) > 2 else rounds[-1]
    limit = 40  # what the season config uses

    builder = PromptBuilder(transcript_limit=limit,
                            anti_echo_instructions=True)
    view = build_view(events, target)
    print(f"{run.name}  round {target}  reader '{view.agent_id}'  "
          f"transcript_limit={limit}")
    print(f"history: {len(view.public_transcript)} public, "
          f"{len(view.private_conversations)} private messages\n")

    sizes = measure(builder, view, ActionType.PUBLIC_MESSAGE)
    total = sizes["TOTAL with system"]
    print(f"{'section':26} {'chars':>8}  {'share':>6}")
    print("-" * 44)
    for name, chars in sizes.items():
        share = f"{chars / total:.0%}" if total else "-"
        print(f"{name:26} {chars:8d}  {share:>6}")

    body = sum(len(m.content) for m in view.public_transcript)
    shown = view.public_transcript[-limit:]
    windowed = sum(len(m.content) for m in shown)
    if windowed:
        print(f"\npublic transcript text: {body:,} chars in all, "
              f"{windowed:,} in the last {len(shown)} shown")
        print(f"  per message: {windowed // len(shown)} chars "
              f"(~{windowed // len(shown) // 4} tokens)")
    print(f"\nAt ~4 chars per token the prompt is ~{total // 4:,} tokens.")


if __name__ == "__main__":
    main()
