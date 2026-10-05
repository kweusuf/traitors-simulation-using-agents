"""Refuse to start a new game while a known bug is open.

A run started with a known bug silently corrupts its own data, and the
corruption surfaces much later in the results - by then it is indistinguishable
from a finding. Two examples from this repository, both caught only by
reading logs closely:

- The phantom detector rejected ordinary prose ("However, I think...") as a
  phantom player, burning a retry each time.
- `RECRUIT_DECLINED.by` names the recruiter, so the event reads as if the
  traitor refused an offer to himself. That was read as a bug in the
  mechanic when it was a bug in the log.

Resuming is deliberately exempt. The rounds already played cannot be
un-played, and blocking a resume over a bug found *in the run being resumed*
would strand it. This gates new runs only.

Usage:
  python tools/bug_gate.py            # check and report
  python tools/bug_gate.py --quiet    # exit status only, for the CLI
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TODO = Path(__file__).resolve().parents[1] / "docs" / "todo.md"

# A bug is a heading that opens with a checkbox: `### [ ] some title`. The
# mark lives in the heading rather than on a list line so the title is
# always visible in a table of contents, and so a bug cannot be introduced
# without somewhere to tick it.
HEADING = re.compile(
    r"^(?P<hashes>#{1,6})\s+\[(?P<mark>[ xX])\]\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
# Severity is the nearest preceding heading that is not itself a bug.
SECTION = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)

BLOCKING = {"blocking"}


def open_bugs() -> list[tuple[str, str]]:
    """(severity, title) for every unticked bug, in file order."""
    if not TODO.exists():
        raise SystemExit(f"missing {TODO}")
    text = TODO.read_text()
    # Bug headings are also section headings, so they must not be treated as
    # the severity governing the bugs after them - otherwise the title of one
    # bug becomes the severity of the next.
    bug_positions = {m.start() for m in HEADING.finditer(text)}
    sections = [
        (m.start(), m.group("title").strip().lower())
        for m in SECTION.finditer(text)
        if m.start() not in bug_positions
    ]
    found: list[tuple[str, str]] = []
    for match in HEADING.finditer(text):
        if match.group("mark") == "x":
            continue
        # The severity is whatever heading governed this one.
        severity = ""
        for pos, title in sections:
            if pos < match.start():
                severity = title
            else:
                break
        found.append((severity, match.group("title")))
    return found


def main() -> None:
    quiet = "--quiet" in sys.argv
    bugs = open_bugs()
    blocking = [b for b in bugs if b[0] in BLOCKING]

    if quiet:
        raise SystemExit(1 if blocking else 0)

    print(f"{TODO.relative_to(TODO.parents[1])}: "
          f"{len(bugs)} open, {len(blocking)} blocking")
    for severity, title in bugs:
        print(f"  [{'BLOCKING' if severity in BLOCKING else severity}] {title}")

    if blocking:
        print(
            f"\n{len(blocking)} blocking bug(s). A new game will corrupt its "
            f"own results.\nFix them, tick the box in docs/todo.md, then run "
            f"again.\n"
            f"Resuming an existing game is not blocked - the rounds already "
            f"played cannot be un-played."
        )
        raise SystemExit(1)
    print("\nno blocking bugs; a new game may start")


if __name__ == "__main__":
    main()