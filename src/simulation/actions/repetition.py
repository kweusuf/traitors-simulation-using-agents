"""Near-duplicate detection for generated prose (spec section 21).

A small model in a long conversation falls into a rhythm: the same
sentence shape, the same three phrases, recycled. Byte-identical messages
are the loudest case, but the failure that actually costs a game is
*near*-duplication - eight players each saying "I agree with Iris, we need
to watch her", which is distinct text and identical content.

This is a pure comparison module on purpose. It has no view, no
telemetry and no prompt, so the threshold can be measured against a run
that already exists rather than argued about.

Two measures, because they catch different things:

- Normalised equality for exact restatements. Cheap, and unambiguous.
- Jaccard over content words for near-duplicates. O(n) in words, unlike a
  sequence-ratio, which matters when twenty-two agents each compare
  against a dozen prior messages.

Both are deliberately insensitive to word order and length. A player
restating an argument in their own words is still repeating it, and that
is the case worth rejecting.

What the second measure is NOT good at, established by replaying a
completed run rather than assumed: it cannot separate copying from
discussion. 48% of that run's public messages overlap an earlier message
at 0.6, because players legitimately keep returning to the same few
claims, and "lying" swapped for "truthful" scores identically to a
restatement. So the default threshold requires an exact match, and the
overlap score is available for a run that shows a paraphrased collapse.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Function words carry no content, so they inflate similarity between two
# messages that say opposite things ("I agree with X" / "I disagree with X"
# differ by one token here). Dropping them makes a genuine disagreement
# score low, which is the direction that matters: a false negative costs one
# repetitive message, a false positive discards a real turn.
STOPWORDS = frozenset("""
a about after all also am an and any are as at be been before being but by
can cant do does doing done down each for from get got had has have he her
here hers him his how i if im in into is it its just me more most my no nor
not now of off on once one only or other our ours out over own re said same
say says she should so some such than that the their theirs them then there
these they this those to too up us was we were what when where which while
who whom why will with would you your yours
""".split())

_WORD = re.compile(r"[A-Za-z']+")


def normalize(text: str) -> str:
    """Casefolded, whitespace-collapsed form, for the equality check."""
    return " ".join(text.casefold().split())


def content_words(text: str) -> set[str]:
    """Meaningful words, lowercased. Stopwords removed; short words kept.

    Numbers are kept deliberately: "I read 42" against "I read 17" is a real
    difference, and a claim of the same vote twice is a real repeat.
    """
    return {
        w for w in (t.casefold() for t in _WORD.findall(text))
        if w not in STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    """0.0 (unrelated) to 1.0 (identical content).

    Returns 1.0 for a normalised-equal pair even when both are too short to
    score meaningfully, because an exact repeat is an exact repeat at any
    length.
    """
    if not a.strip() or not b.strip():
        return 0.0
    if normalize(a) == normalize(b):
        return 1.0
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        # Two "yes" replies. Not a duplicate worth spending a retry on.
        return 0.0
    return len(wa & wb) / len(wa | wb)


def find_repeat(
    candidate: str,
    history: Iterable[str],
    threshold: float,
    min_words: int = 5,
) -> Optional[tuple[str, float]]:
    """The most similar prior message at or above `threshold`, else None.

    `min_words` is a floor on the candidate, not on the history: a reply
    of two words ("No, definitely.") is a legitimate short answer and
    comparing it by content words produces noise. Short candidates are
    therefore only ever caught by exact equality, which `similarity`
    already handles.
    """
    words = content_words(candidate)
    floor = min_words if len(words) < min_words else 0
    best: Optional[tuple[str, float]] = None
    for prior in history:
        if not prior.strip():
            continue
        score = similarity(candidate, prior)
        if score < threshold:
            continue
        # Below the word floor only an exact match counts: two short
        # answers overlap heavily on function words alone, and "No." is not
        # a repetition of "No." unless it is literally the same string.
        if floor > 0 and normalize(candidate) != normalize(prior):
            continue
        if best is None or score > best[1]:
            best = (prior, score)
    return best


def repeat_reason(prior: str, score: float) -> str:
    """The correction text the model sees, naming what it already said.

    The prior text is quoted back because a bare "you repeated yourself"
    gives a small model nothing to work from, and showing it the text is
    what lets it say the opposite instead of reordering the same words.
    """
    return (
        "you already said this earlier: "
        f'"{prior.strip()}" '
        f"(similarity {score:.0%}). It is your turn to contribute something "
        "nobody else has said: a different read of the evidence, a claim you "
        "are willing to defend, or a direct question to one player. Do not "
        "restate what was just said."
    )