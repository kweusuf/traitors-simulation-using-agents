"""How well did the pointer arm actually work?

A run with `co_generate_gist` on looks fine whether the model wrote useful
pointers or ignored the field entirely, because a missing pointer falls back
to the deterministic clause and nothing downstream fails. That silent
fallback is the thing to catch, so it is measured here rather than assumed:
the co-generation rate is the headline, and a rate low enough to make the
arm meaningless is worth stating as such.
"""
import collections
import json
import sys
from pathlib import Path

run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-en5")
events = [json.loads(l) for l in open(run / "events.jsonl")]
messages = [
    e for e in events
    if e.get("type") in ("PUBLIC_MESSAGE", "PRIVATE_MESSAGE")
]


def main() -> None:
    if not messages:
        print(f"{run.name}: no messages yet")
        return

    with_gist = [e for e in messages if (e.get("payload") or {}).get("gist")]
    rate = len(with_gist) / len(messages)

    print(f"{run.name}: {len(messages)} messages, "
          f"pointer co-generated on {len(with_gist)} ({rate:.0%})")
    if rate < 0.5:
        print("  NOTE: below half. The deterministic fallback is doing the")
        print("        work, so this arm is not really testing the model.")

    # The other half of the question: is the pointer a distillation, or the
    # message's opening clause wearing a different hat?
    copied = 0
    ratios = []
    shared = collections.Counter()
    for e in with_gist:
        gist = " ".join((e["payload"]["gist"]).split())
        content = " ".join((e["payload"].get("content") or "").split())
        if gist.lower() == content[: len(gist)].lower():
            copied += 1
        if content:
            ratios.append(len(gist) / len(content))
        shared[gist.lower()] += 1

    if with_gist:
        print(f"\n  copied from the message head: {copied} "
              f"({copied / len(with_gist):.0%})")
        if ratios:
            ratios.sort()
            print(f"  pointer length / message length: "
                  f"median {ratios[len(ratios) // 2]:.2f}  "
                  f"max {ratios[-1]:.2f}")
        # A pointer every player shares is the failure this feature is most
        # able to cause, and it is invisible without counting.
        worst, n = shared.most_common(1)[0]
        print(f"  most repeated pointer: x{n}  {worst[:60]!r}")

    # Prompt size, because that is the thing the pointers were for.
    calls = [json.loads(l) for l in open(run / "llm_calls.jsonl")]
    if calls:
        sizes = sorted(c.get("prompt_chars") or 0 for c in calls)
        print(f"\n  prompt chars: median {sizes[len(sizes) // 2]}  "
              f"max {sizes[-1]}")


main()