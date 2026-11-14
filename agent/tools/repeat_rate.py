"""Repeat rate, split by channel.

The headline "38% of messages" figure counted public and private together.
Whether a private DM repeats text is a different question from whether
the room speaks in templates, so they are reported apart.
"""
import collections
import json

for run in ("uk-s01-en4", "uk-s01-en3"):
    events = [json.loads(l) for l in open(f"runs/{run}/events.jsonl")]
    print(run)
    for label, kind in (("public", "PUBLIC_MESSAGE"),
                        ("private", "PRIVATE_MESSAGE")):
        msgs = [e for e in events if e.get("type") == kind]
        if not msgs:
            print(f"  {label:8} none")
            continue
        texts = [(e.get("payload") or {}).get("content") for e in msgs]
        counts = collections.Counter(texts)
        repeats = sum(c - 1 for c in counts.values() if c > 1)
        senders = {e.get("actor") for e in msgs}
        print(f"  {label:8} n={len(msgs):4d} senders={len(senders):3d} "
              f"distinct={len(counts):4d} repeat_occurrences={repeats:4d} "
              f"({repeats / len(msgs):.0%})")
