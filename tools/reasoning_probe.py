"""Is the room reasoning, or still parroting?

Two questions a run's summary will not answer on its own:

- Did the prompt's vocabulary leak into the fiction? "Ledger" was an
  internal artefact that 18 of 24 early messages discussed as if it were a
  thing in the game. Any internal term reaching the speeches is a prompt
  bug, and it is invisible in aggregate metrics.
- Are players citing what others actually said, or talking past each other?
  Repetition fell across the pointer arm, but a model can avoid repeating
  itself while still never reading anyone.

Usage:
  python tools/reasoning_probe.py [runs/uk-s01-ledger]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Words that only exist because of how the harness is built. If these appear
# in a message, the prompt leaked its own machinery into the fiction.
INTERNAL_TERMS = ("ledger", "suspect list", "your list", "reason_summary")

# A message that engages with a specific claim made by a named player.
REPLY = re.compile(
    r"\b(you (?:said|mentioned|claimed|argued|noted|deflected|asked)|"
    r"when you|you keep|you have (?:not|never))",
    re.IGNORECASE,
)


def main() -> None:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-ledger")
    msgs = []
    for line in open(run / "events.jsonl"):
        event = json.loads(line)
        if event.get("type") != "PUBLIC_MESSAGE":
            continue
        content = (event.get("payload") or {}).get("content") or ""
        if content.strip():
            msgs.append((event.get("actor"), content.strip()))

    if not msgs:
        raise SystemExit(f"no public messages in {run}")

    blob = " ".join(t for _, t in msgs).casefold()
    print(f"{run.name}: {len(msgs)} public messages\n")

    print("internal terms leaking into speech:")
    for term in INTERNAL_TERMS:
        n = blob.count(term)
        flag = "  LEAK" if n else ""
        print(f"  {term:16} {n:4d}{flag}")

    replies = [t for _, t in msgs if REPLY.search(t)]
    print(f"\nengaging with a named player's claim: "
          f"{len(replies)}/{len(msgs)} ({len(replies) / len(msgs):.0%})")

    lengths = sorted(len(t) for _, t in msgs)
    print(f"message length: median={lengths[len(lengths) // 2]}  "
          f"max={lengths[-1]}")

    distinct = len({t for _, t in msgs})
    print(f"distinct texts: {distinct}/{len(msgs)} "
          f"({distinct / len(msgs):.0%})")

    # Vote coherence: how often a player votes for the person they have been
    # naming in public. Cheap proxy for "the read reached the ballot".
    tally: dict[str, list[str]] = {}
    for actor, text in msgs:
        for other in {m[0] for m in msgs if m[0] != actor}:
            if re.search(rf"\b{re.escape(other)}\b", text, re.IGNORECASE):
                tally.setdefault(actor, []).append(other)
    speak = [a for a, names in tally.items() if len(set(names)) >= 3]
    print(f"players naming 3+ distinct others: {len(speak)}/{len(msgs) and 22}")


if __name__ == "__main__":
    main()