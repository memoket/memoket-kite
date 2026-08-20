"""Deterministic, LLM-free verdicts about a question and its answer.

``GateContext`` is the premise gate, and what the premise rule reads. A
question whose subject anchors have zero document frequency in the store asks
about something memory has never seen; on a workload that contains
unanswerable questions, that is a false premise rather than a retrieval
failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from memoket_kite.core.algebra import _tokens
from memoket_kite.pipeline.patterns import ADVICE_QUESTION

_POSSESSIVE = re.compile(r"\b([A-Z][a-z]{2,})'s\s+\w+")
# Maximal capitalized runs ("Porsche 991 Turbo S", "Shinjuku") — the dominant
# premise-anchor shape on first-person questions, where possessives are rare.
_PROPER_RUN = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z0-9]{1,})*\b")
# Weekday, month and relative-time words are held structurally rather than as
# fact text, so their document frequency is always zero and says nothing about
# whether a question's premise holds. Stemmed with the same tokenizer the
# lookup uses, or the longer names would never match.
_TIME_WORD_STEMS = frozenset(
    _tokens(
        "monday tuesday wednesday thursday friday saturday sunday january"
        " february march april may june july august september october november"
        " december today tomorrow yesterday weekend"
    )
)
_QUESTION_STARTERS = frozenset(
    "what when where which who whom whose how why did do does have has had was"
    " were is are am can could would should will the".split()
)


def _stem_df(stem: str, store) -> int:
    return len(getattr(store, "by_token", {}).get(stem, ()) or ())


@dataclass
class GateContext:
    """Per-question premise-gate state, computed once, LLM-free."""

    subjects: list[str] = field(default_factory=list)
    premise_risk: bool = False

    @classmethod
    def build(cls, question: str, store, intent: str = "", advice=None) -> "GateContext":
        subjects: list[str] = []
        seen: set[str] = set()
        # Subjects come from the question text alone, not from the compiled
        # plan: plan entities live inside each query and never at the top
        # level, so a top-level plan lookup yields an empty subject list.
        candidates = _POSSESSIVE.findall(question or "")
        for match in _PROPER_RUN.finditer(question or ""):
            if match.start() == 0:
                continue  # sentence-initial capitalization is not a name
            candidates.append(match.group(0))
        for name in candidates:
            key = name.strip().lower()
            if not key or key in seen or key in _QUESTION_STARTERS:
                continue
            seen.add(key)
            subjects.append(name.strip())
        risk = False
        for subject in subjects:
            stems = [
                stem for stem in _tokens(subject) if len(stem) >= 3 and stem not in _TIME_WORD_STEMS
            ]
            if stems and all(_stem_df(stem, store) == 0 for stem in stems):
                risk = True
                break
        # Intent conditioning: a missing entity is evidence of a false premise
        # only on factual and retrospective questions, which assert that the
        # entity exists in memory. Speculative and advice questions instead
        # supply the entity themselves ("would Tim enjoy C.S. Lewis?"), so its
        # absence from the store is expected and carries no premise signal.
        if risk and (intent == "speculative" or (advice or ADVICE_QUESTION).search(question or "")):
            risk = False
        return cls(subjects=subjects, premise_risk=risk)
