"""How a stored line becomes a line of evidence a reader can attribute."""

from __future__ import annotations

import re

#: A speaker label is a tag, not prose: bounded so an odd value cannot grow the
#: pack without limit.
SPEAKER_LABEL_CAP = 24

#: The row separators the evidence block uses, quoting, and control characters:
#: a label that kept them would read as structure rather than as a name. This
#: bounds how a label displays; it is not a security boundary.
_NOT_A_LABEL = re.compile(r"[\[\]()|:\"'<>\\/\x00-\x1f\x7f]")


def speaker_label(who: str) -> str:
    """A speaker as a display label: single line, bounded length."""
    return " ".join(_NOT_A_LABEL.sub(" ", str(who or "")).split())[:SPEAKER_LABEL_CAP]


def spoken(who: str, text: str, *, enabled: bool = True) -> str:
    """A dialogue line as the reader must read it: whose words these are.

    Without the speaker, a suggestion the assistant made and a thing the user
    did are the same sentence, and a question about what the user did counts
    both. Rendering and pricing both call this, so the pack is charged what it
    costs. A speaker that normalises to nothing leaves the text as it was, and
    a deployment whose parties are not asymmetric can leave the label off.
    """
    if not enabled:
        return str(text)
    label = speaker_label(who)
    return f"{label}: {text}" if label else str(text)
