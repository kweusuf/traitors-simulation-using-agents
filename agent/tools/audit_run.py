"""Audit one run's event log for player-behaviour defects.

Exact-match checks, not semantic ones, on purpose: "named a player who does
not exist" and "replied to a thread they never received" are lookup
questions, and answering them by lookup gives exact counts you can measure
a fix against. Paraphrase detection uses word n-gram Jaccard, which needs no
model and no index.

Usage:
    python tools/audit_run.py runs/<game-id> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Words the model writes that look like proper nouns but are not names.
# Sentence-initial connectors live here: without them, ordinary openers
# ("Otherwise, ...", "However, ...") are flagged as invented players.
STOPWORD_CAPS = {
    "You", "Your", "Youre", "What", "Which", "Who", "When", "Where", "Why",
    "How", "I", "Im", "Its", "It", "Is", "A", "An", "The", "And", "But",
    "If", "So", "That", "This", "These", "Those", "There", "Their",
    "They", "We", "Us", "Our", "He", "She", "His", "Her", "Him",
    "Since", "Until", "While", "After", "Before", "Then", "Than",
    "Still", "Even", "Also", "Just", "Only", "Most", "Some", "Each",
    "Someone", "Something", "Anything", "Nothing", "Everyone", "Nobody",
    "Because", "Though", "Unless", "Whether", "Either", "Neither",
    "Otherwise", "Whatever", "Therefore", "However", "Hence", "Thus",
    "Meanwhile", "Nevertheless", "Nonetheless", "Instead",
    "Public", "Round", "Table", "Night", "Morning", "Good", "Bad",
    "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "First", "Second", "Third", "Again", "Instead",
}


@dataclass
class Finding:
    check: str
    round: int
    seq: int
    actor: str
    target: str
    detail: str
    extra: dict = field(default_factory=dict)


def shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Audit:
    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        self.events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.by_seq = {e["sequence"]: e for e in self.events}
        self.roster: list[str] = []
        self.traitors: set[str] = set()
        self.converted: set[str] = set()
        self.death: dict[str, tuple[int, str]] = {}
        self.death_seq: dict[str, int] = {}
        self.alive_at: dict[int, set[str]] = {}
        self.findings: list[Finding] = []
        self.duplicate_pairs: list = []
        self.duplicate_stats: dict = {}
        self.leaks = 0
        self._boot()

    # ------------------------------------------------------------------
    # Board reconstruction
    # ------------------------------------------------------------------
    def _boot(self) -> None:
        for e in self.events:
            t = e["type"]
            if t == "GAME_STARTED":
                self.roster = list((e.get("payload") or {}).get("players") or [])
            elif t == "ROLE_ASSIGNED":
                p = e.get("payload") or {}
                if p.get("role") == "traitor":
                    self.traitors.add(e["actor"])
            elif t == "ROLE_RECRUITED":
                self.converted.add(e["actor"])
            elif t == "PLAYER_ELIMINATED":
                self.death[e["actor"]] = (
                    int(e["round"]),
                    (e.get("payload") or {}).get("method", "?"),
                )
                self.death_seq[e["actor"]] = int(e["sequence"])

        # Who was alive at the start of each round.
        alive = set(self.roster)
        by_round: dict[int, list] = defaultdict(list)
        for e in self.events:
            by_round[int(e["round"])].append(e)
        for rnd in sorted(by_round):
            self.alive_at[rnd] = set(alive)
            for e in by_round[rnd]:
                if e["type"] == "PLAYER_ELIMINATED":
                    alive.discard(e["actor"])

    def known(self, name: str) -> bool:
        return name.lower() in {p.lower() for p in self.roster}

    def add(self, check, e, detail, target="", **extra):
        self.findings.append(
            Finding(
                check=check,
                round=int(e["round"]),
                seq=int(e["sequence"]),
                actor=e.get("actor") or "?",
                target=target,
                detail=detail,
                extra=extra,
            )
        )

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    def run_all(self) -> None:
        self.check_invented_names()
        self.check_dead_players_discussed()
        self.check_speaking_after_death()
        self.check_self_reply()
        self.check_cross_thread_reply()
        self.check_private_content_leak()
        self.check_duplicate_messages()
        self.check_rejected_targets()
        self.check_role_claims()

    def messages(self):
        return [
            e
            for e in self.events
            if e["type"] in ("PUBLIC_MESSAGE", "PRIVATE_MESSAGE")
        ]

    def check_invented_names(self) -> None:
        """A name that is not in the roster, stated as if it were a player.

        Capitalisation alone is far too loose - the models open with
        "Round", "Watchers", "Analysis" and every one of those is not a
        person. A token is only a candidate if its lowercase form never
        appears anywhere in the run either, which is what separates an
        invented name from an ordinary word that merely started a
        sentence. That needs no dictionary and no model.
        """
        known = {p.lower() for p in self.roster}
        # Deliberately NOT lowercased: the test is whether a token ever
        # appears in lowercase anywhere in the run. An invented name is
        # always capitalised; an ordinary word the models happen to start a
        # sentence with also appears lowercase somewhere else.
        raw = " ".join(
            ((e.get("payload") or {}).get("content") or "") for e in self.events
        )
        lowercase_words = set(re.findall(r"\b[a-z]{3,}\b", raw))

        for e in self.messages():
            body = (e.get("payload") or {}).get("content") or ""
            caps = set(re.findall(r"\b[A-Z][a-z]{2,}\b", body))
            invented = sorted(
                c
                for c in caps
                if c not in STOPWORD_CAPS
                and c.lower() not in known
                and c.lower() not in lowercase_words
            )
            for name in invented:
                self.add(
                    "invented_player", e,
                    f"named '{name}', not in the roster and never used "
                    f"lowercase anywhere in this run",
                    target=name,
                )

    def invented_players_summary(self) -> dict:
        counts = Counter(
            f.target for f in self.findings if f.check == "invented_player"
        )
        first_seen: dict[str, int] = {}
        for f in self.findings:
            if f.check == "invented_player":
                first_seen.setdefault(f.target, f.seq)
        affected = {
            f.target
            for f in self.findings
            if f.check == "invented_player"
        }
        # How many messages mention each phantom at all.
        mentions: dict[str, int] = {}
        for e in self.events:
            body = ((e.get("payload") or {}).get("content") or "").lower()
            for name in affected:
                if re.search(rf"\b{name.lower()}\b", body):
                    mentions[name] = mentions.get(name, 0) + 1
        return {
            "distinct_invented": sorted(counts),
            "times_named": dict(counts.most_common()),
            "first_sequence": first_seen,
            "messages_mentioning": dict(
                sorted(mentions.items(), key=lambda kv: -kv[1])
            ),
        }

    def check_dead_players_discussed(self) -> None:
        """A player who has left, discussed as if still in the game."""
        for e in self.messages():
            body = (e.get("payload") or {}).get("content") or ""
            alive = self.alive_at.get(int(e["round"]), set(self.roster))
            for pid in self.roster:
                if pid in alive:
                    continue
                if not re.search(rf"\b{re.escape(pid)}\b", body, re.I):
                    continue
                died_round, method = self.death.get(pid, (0, "?"))
                self.add(
                    "dead_player_discussed", e,
                    f"discussed '{pid}', who left in round {died_round} "
                    f"by {method}",
                    target=pid,
                    stale_rounds=int(e["round"]) - died_round,
                )

    def check_speaking_after_death(self) -> None:
        """A message recorded after the sender's own elimination event.

        Compared on sequence, not round: a player eliminated during round
        5 legitimately spoke earlier in that same round, and comparing
        rounds alone flags every one of them.
        """
        for e in self.messages():
            actor = e.get("actor")
            if actor not in self.death:
                continue
            death_seq = self.death_seq.get(actor)
            if death_seq is not None and int(e["sequence"]) > death_seq:
                self.add(
                    "spoke_after_death", e,
                    f"'{actor}' left at seq {death_seq} but spoke at "
                    f"seq {e['sequence']}",
                    target=actor,
                )

    def check_self_reply(self) -> None:
        for e in self.messages():
            body = ((e.get("payload") or {}).get("content") or "").strip()
            sender = (e.get("actor") or "").lower()
            if not sender or not body:
                continue
            first = body.split()[0].strip(",.:;!?\"'").lower()
            if first == sender:
                self.add("self_reply", e, "opens by addressing its own sender",
                         target=e["actor"])

    def check_cross_thread_reply(self) -> None:
        """Answering a conversation you were not part of.

        The private chat runs an opening wave and then a reply wave. A
        reply is only legitimate if the replier actually received the
        thread they are answering; replying to a message addressed to
        somebody else means the content reached a player who had no
        access to it.
        """
        inbox: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for e in self.events:
            if e["type"] != "PRIVATE_MESSAGE":
                continue
            tgt = (e.get("targets") or [None])[0]
            if not tgt:
                continue
            sender = e["actor"]
            rnd = int(e["round"])
            box = inbox[rnd]
            if box.get(sender):
                # This sender has an inbox: it is a reply.
                if tgt not in box[sender]:
                    self.add(
                        "cross_thread_reply", e,
                        f"replied to '{tgt}' but had mail only from "
                        f"{sorted(set(box[sender]))}",
                        target=tgt,
                        had_mail_from=sorted(set(box[sender])),
                    )
            box.setdefault(tgt, []).append(sender)

    def check_private_content_leak(self) -> None:
        """A public or private message quoting someone else's private words.

        Compares each message against private messages the sender could not
        have seen, using shared word shingles rather than exact quotes: a
        model that paraphrases a confession still leaks it.
        """
        privates = [
            e
            for e in self.events
            if e["type"] == "PRIVATE_MESSAGE"
            and (e.get("payload") or {}).get("content")
        ]
        by_key = {}
        for e in privates:
            tgt = (e.get("targets") or [None])[0]
            if not tgt:
                continue
            by_key[(int(e["round"]), e["actor"], tgt)] = shingles(
                e["payload"]["content"]
            )
        for e in self.messages():
            sender = e.get("actor")
            mine = shingles((e.get("payload") or {}).get("content") or "")
            if len(mine) < 12:
                continue
            for (rnd, a, b), theirs in by_key.items():
                if rnd != int(e["round"]):
                    continue
                if sender in (a, b):
                    continue  # their own words
                if len(theirs) < 12:
                    continue
                overlap = jaccard(mine, theirs)
                if overlap >= 0.45:
                    self.add(
                        "private_leak", e,
                        f"{overlap:.0%} word overlap with a private message "
                        f"from {a} to {b}",
                        target=b,
                        overlap=round(overlap, 3),
                    )
                    self.leaks += 1

    def check_duplicate_messages(self) -> None:
        """Two players producing near-identical text: ranked, not counted.

        A frequency here would be meaningless. Every message in this game
        discusses the same handful of names, so similarity runs high by
        default and a threshold measures on-topic-ness rather than
        repetition. What is useful is the ranking: the worst pairs are
        real defects, the tail is just the register of the game.
        """
        msgs = [e for e in self.messages() if e.get("payload", {}).get("content")]
        sigs = [(e, shingles(e["payload"]["content"])) for e in msgs]
        sigs = [(e, s) for e, s in sigs if len(s) >= 15]
        sigs.sort(key=lambda pair: len(pair[1]))
        pairs: list[tuple[float, dict, dict]] = []
        for i, (ei, si) in enumerate(sigs):
            for ej, sj in sigs[i + 1 :]:
                if len(sj) > 2.2 * len(si):
                    break
                sim = jaccard(si, sj)
                if sim >= 0.45:
                    pairs.append((sim, ei, ej))
        pairs.sort(key=lambda row: -row[0])
        self.duplicate_pairs = pairs[:25]
        for sim, ei, ej in pairs[:25]:
            self.add(
                "near_duplicate", ei,
                f"{sim:.0%} word overlap with seq {ej['sequence']} "
                f"from {ej.get('actor')}",
                target=str(ej.get("actor")),
                seq2=ej["sequence"],
                overlap=round(sim, 3),
            )
        self.duplicate_stats = {
            "pairs_over_0.45": len(pairs),
            "pairs_over_0.6": sum(1 for p in pairs if p[0] >= 0.6),
            "pairs_over_0.7": sum(1 for p in pairs if p[0] >= 0.7),
            "messages_compared": len(sigs),
        }

    def check_rejected_targets(self) -> None:
        """Illegal targets, classified by what went wrong."""
        for e in self.events:
            if e["type"] != "ACTION_REJECTED":
                continue
            reason = (e.get("payload") or {}).get("reason", "")
            chosen = ""
            m = re.search(r"target '([^']+)'", reason)
            if m:
                chosen = m.group(1)
            kind = "other"
            if "not legal" in reason:
                alive = self.alive_at.get(int(e["round"]), set(self.roster))
                if chosen in self.death:
                    kind = "dead player"
                elif chosen.lower() == (e.get("actor") or "").lower():
                    kind = "self"
                elif chosen and not self.known(chosen):
                    kind = "invented player"
                elif chosen in alive:
                    kind = "wrong phase"
                else:
                    kind = "case or typo"
            elif "valid JSON" in reason or "not valid" in reason:
                kind = "unparseable output"
            self.add("rejected_action", e, f"[{kind}] {reason[:150]}",
                     target=chosen, kind=kind)

    def check_role_claims(self) -> None:
        """A player asserting a role, or another player's role, wrongly."""
        traitors = self.traitors | self.converted
        patterns = re.compile(
            r"\b(?:i am|i'm|im) (?:the |a |your )?traitor\b"
            r"|\b(?:i was|i have been) (?:recruited|a traitor)\b",
            re.I,
        )
        for e in self.messages():
            body = (e.get("payload") or {}).get("content") or ""
            sender = e.get("actor")
            m = patterns.search(body)
            if not m:
                continue
            claimed_traitor = bool(patterns.search(body))
            if sender in traitors:
                self.add("traitor_admitted_role", e,
                         "a traitor stated their own role in public",
                         target=sender)
            elif claimed_traitor:
                self.add("faithful_false_role_claim", e,
                         f"a faithful player claimed to be a traitor", target=sender)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", help="a run directory containing events.jsonl")
    ap.add_argument("--json", help="write the findings to this path")
    args = ap.parse_args()

    audit = Audit(Path(args.run_dir))
    audit.run_all()

    total_msgs = len(audit.messages())
    by_check = Counter(f.check for f in audit.findings)
    exact = {
        "invented_player", "dead_player_discussed", "spoke_after_death",
        "self_reply", "cross_thread_reply", "rejected_action",
        "traitor_admitted_role", "faithful_false_role_claim",
    }
    print(f"run: {args.run_dir}")
    print(f"events {len(audit.events)}  messages {total_msgs}")
    print()
    print("EXACT-MATCH CHECKS (counts are meaningful)")
    width = max((len(c) for c in by_check), default=10)
    for check, n in by_check.most_common():
        if check not in exact:
            continue
        rate = n / total_msgs * 100 if total_msgs else 0
        print(f"  {check:<{width}}  {n:>5}  {rate:5.1f}% of messages")
    print()
    inv = audit.invented_players_summary()
    if inv["distinct_invented"]:
        print("INVENTED PLAYERS")
        print(f"  names: {inv['distinct_invented']}")
        for name, n in inv["times_named"].items():
            print(f"    {name}: named {n}x, first at seq "
                  f"{inv['first_sequence'][name]}, mentioned in "
                  f"{inv['messages_mentioning'].get(name, 0)} events")
    print()
    print("RANKED, NOT COUNTED (similarity, no calibrated threshold)")
    ds = audit.duplicate_stats
    if ds:
        print(f"  near-duplicate pairs: {ds['pairs_over_0.45']} over 0.45, "
              f"{ds['pairs_over_0.6']} over 0.60, {ds['pairs_over_0.7']} over 0.70 "
              f"(of {ds['messages_compared']} comparable messages)")
        print(f"  private-overlap pairs: {audit.leaks} over 0.45")
        worst = [f for f in audit.findings if f.check == "near_duplicate"][:5]
        for f in worst:
            print(f"    r{f.round} seq{f.seq} {f.actor}: {f.detail}")

    print()
    print("PER-ROUND TREND (exact checks only)")
    trend: dict[int, Counter] = defaultdict(Counter)
    for f in audit.findings:
        if f.check in exact:
            trend[f.round][f.check] += 1
    checks = sorted({c for c in by_check if c in exact})
    header = "  round " + "".join(f"{c[:11]:>13}" for c in checks)
    print(header)
    for rnd in sorted(trend):
        row = "".join(f"{trend[rnd][c]:>13}" for c in checks)
        print(f"  {rnd:>5}{row}")

    if args.json:
        payload = [f.__dict__ for f in audit.findings]
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {len(payload)} findings to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())