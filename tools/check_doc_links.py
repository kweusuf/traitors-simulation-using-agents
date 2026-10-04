"""Do the cross-links in docs/ actually resolve?

A doc set that links to itself is only useful if the links work. GitHub
slugifies a heading by lowercasing it, dropping punctuation, and turning
spaces into hyphens - which is not quite what a shell `tr` does, and not
quite what an eye does either. So reproduce the real algorithm and compare
against every anchor the docs link to.

Reports anchors that resolve to nothing. Those are silent failures: the
link renders as ordinary text and the reader concludes the note does not
exist.

Usage:
  python tools/check_doc_links.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"

PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
SPACES = re.compile(r"[\s_]+")
LINK = re.compile(r"\]\(([^)#]*)(#[^)#]+)?\)")


def slugify(heading: str) -> str:
    """GitHub's heading slug: lowercase, no punctuation, hyphens for spaces."""
    text = heading.strip().lower()
    text = PUNCT.sub("", text)
    return SPACES.sub("-", text).strip("-")


def anchors_in(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            out.add(slugify(line.lstrip("#").strip()))
    return out


def main() -> None:
    files = sorted(DOCS.glob("*.md"))
    slugs = {p.name: anchors_in(p) for p in files}
    broken: list[str] = []

    for source in files:
        for text, anchor in LINK.findall(source.read_text()):
            if not anchor:
                continue
            target = source if not text else DOCS / text
            if not target.exists():
                broken.append(f"{source.name}: missing file {text}")
                continue
            if anchor[1:] not in slugs.get(target.name, set()):
                broken.append(f"{source.name}: {text}{anchor} does not resolve")

    for line in broken:
        print(f"BROKEN {line}")
    print(
        f"\n{len(files)} docs checked, {len(broken)} broken link(s)."
        if broken
        else f"\n{len(files)} docs checked, every anchor resolves."
    )
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()