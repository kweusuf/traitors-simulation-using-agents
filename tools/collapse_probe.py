"""Was the template collapse the model, or identical prompts?

Eight different players emitting byte-identical text at temperature 0.7 is
not something a sampling model does when their prompts differ. It is what
it does when their prompts are effectively the same. This checks the one
signal the run folder keeps without storing prompt text: prompt_chars.
"""
import collections
import json
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "runs/uk-s01-en4"

events = [json.loads(l) for l in open(f"{run}/events.jsonl")]
calls = [json.loads(l) for l in open(f"{run}/llm_calls.jsonl")]

def field(event, name):
    """Events carry scalars at top level and message text under payload."""
    if name == "text":
        return (event.get("payload") or {}).get("content")
    return event.get(name)


calls_by_key = {}
for c in calls:
    calls_by_key.setdefault((c.get("round"), c.get("agent_id")), c)

public = [e for e in events if e.get("type") == "PUBLIC_MESSAGE"]
print(f"{run}: {len(public)} public messages, "
      f"{len({field(e,'actor') for e in public})} senders")

counts = collections.Counter(field(e, "text") for e in public)
repeated = {t: c for t, c in counts.items() if c > 1}
print(f"distinct texts: {len(counts)}   texts repeated: {len(repeated)}")
print(f"occurrences that are a repeat of an earlier message: "
      f"{sum(repeated.values()) - len(repeated)}/{len(public)} "
      f"({(sum(repeated.values()) - len(repeated)) / len(public):.0%})")

print("\n--- top repeated texts ---")
for text, n in counts.most_common(5):
    senders = sorted({field(e, "actor") for e in public
                      if field(e, "text") == text})
    print(f"  x{n:2d}  {len(senders)} senders  {text[:66]!r}")

print("\n--- prompt_chars per sender, for the top text ---")
top = counts.most_common(1)[0][0]
rows = []
for e in public:
    if field(e, "text") != top:
        continue
    snd = field(e, "actor")
    c = calls_by_key.get((field(e, "round"), snd))
    rows.append((snd, field(e, "round"),
                 c.get("prompt_chars") if c else "no-call"))
for row in sorted(rows):
    print("   ", row)
lengths = {r[2] for r in rows if isinstance(r[2], int)}
print(f"\ndistinct prompt lengths among those senders: {len(lengths)} {sorted(lengths)}")

print("\n--- across ALL public calls ---")
by_len = collections.defaultdict(set)
for e in public:
    c = calls_by_key.get((field(e, "round"), field(e, "actor")))
    if c:
        by_len[c.get("prompt_chars")].add(field(e, "actor"))
dupes = {k: v for k, v in by_len.items() if len(v) > 1}
print(f"prompt lengths shared by >1 sender: {len(dupes)} of {len(by_len)}")
for k, v in list(sorted(dupes.items()))[:6]:
    print(f"   {k} chars -> {sorted(v)}")


calls_by_key = {}
for c in calls:
    calls_by_key.setdefault((c.get("round"), c.get("agent_id")), c)

public = [e for e in events if e.get("type") == "PUBLIC_MESSAGE"]
print(f"{run}: {len(public)} public messages, "
      f"{len({field(e,'sender') for e in public})} senders")

counts = collections.Counter(field(e, "text") for e in public)
repeated = {t: c for t, c in counts.items() if c > 1}
print(f"distinct texts: {len(counts)}   repeated texts: {len(repeated)}")
print(f"message occurrences that are a repeat: "
      f"{sum(repeated.values()) - len(repeated)}/{len(public)}")

print("\n--- top repeated texts ---")
for text, n in counts.most_common(5):
    senders = sorted({field(e, "sender") for e in public
                      if field(e, "text") == text})
    print(f"  x{n:2d}  {len(senders)} senders  {text[:70]!r}")

print("\n--- prompt_chars per sender, for the top text ---")
top = counts.most_common(1)[0][0]
rows = []
for e in public:
    if field(e, "text") != top:
        continue
    snd = field(e, "sender")
    c = calls_by_key.get((field(e, "round"), snd))
    rows.append((snd, field(e, "round"),
                 c.get("prompt_chars") if c else "no-call"))
for row in sorted(rows):
    print("   ", row)
lengths = {r[2] for r in rows if isinstance(r[2], int)}
print(f"\ndistinct prompt lengths among those senders: {len(lengths)} {sorted(lengths)}")

print("\n--- correlation across ALL public calls ---")
pairs = [(c.get("prompt_chars"), field(e, "sender"))
         for e in public
         for c in [calls_by_key.get((field(e, "round"), field(e, "sender")))]
         if c]
by_len = collections.defaultdict(set)
for length, snd in pairs:
    by_len[length].add(snd)
dupes = {k: v for k, v in by_len.items() if len(v) > 1}
print(f"prompt lengths shared by >1 sender: {len(dupes)} of {len(by_len)}")
for k, v in list(dupes.items())[:5]:
    print(f"   {k} chars -> {sorted(v)}")
