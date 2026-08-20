"""Deterministic answer post-processing: zero-LLM symbolic checks.

What a rule may read is fixed by :class:`PostprocInput`. Two rules, `premise`
and `hedge`, both of which only ever replace an answer with the canonical
refusal, and neither of which is enabled by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from memoket_kite.pipeline.patterns import ADVICE_QUESTION, REFUSAL_LIKE

CANONICAL_REFUSAL = "No information"

# Phrases with which an answer states that its own evidence is absent.
_HEDGE = re.compile(
    r"there is no (evidence|information|record)|no (evidence|information) "
    r"(that|about|of)|it is not (mentioned|specified|stated)",
    re.I,
)


@dataclass(frozen=True)
class PostprocInput:
    """Everything a post-processing rule is allowed to see."""

    question: str
    answer: str
    #: Deterministic verdicts the answer stage recorded, keyed by verdict name.
    typed_verdicts: dict = field(default_factory=dict)
    #: The advice predicate the deployment runs.
    advice: "re.Pattern | None" = None
    #: Whether this deployment permits a rule to answer with a refusal. A
    #: caller that knows the question has an answer sets this False, and the
    #: refusal rules stand down rather than turn a hit into a certain miss.
    allow_refusal: bool = True

    def is_advice(self) -> bool:
        """Whether the question solicits a judgement rather than a fact."""
        return bool((self.advice or ADVICE_QUESTION).search(self.question or ""))


def premise_refusal(data: PostprocInput) -> tuple[str, dict] | None:
    """Absent-subject factual question answered confidently -> refusal."""
    if not data.allow_refusal:
        return None
    if data.is_advice():
        return None
    gate = (data.typed_verdicts or {}).get("premise")
    if gate is None or not getattr(gate, "premise_risk", False):
        return None
    if REFUSAL_LIKE.search(data.answer or "") or not (data.answer or "").strip():
        return None
    return CANONICAL_REFUSAL, {
        "rule": "premise_refusal",
        "subjects": list(getattr(gate, "subjects", ()) or ()),
    }


def hedge_refusal(data: PostprocInput) -> tuple[str, dict] | None:
    """Answer asserts while itself declaring the evidence absent -> refusal."""
    if not data.allow_refusal:
        return None
    if data.is_advice():
        return None
    text = (data.answer or "").strip()
    if not text or len(text) < 40:
        return None
    hedge = _HEDGE.search(text)
    if not hedge:
        return None
    # "asserts": the hedge is not the whole answer — substantive content
    # (>= 25 chars) exists outside the hedge sentence.
    remainder = (text[: hedge.start()] + text[hedge.end() :]).strip(" .,;—-")
    if len(remainder) < 25 or REFUSAL_LIKE.match(text):
        return None
    return CANONICAL_REFUSAL, {"rule": "hedge_refusal", "hedge": hedge.group(0)}


#: The stage's one registry: which rules exist, what they are called, and the
#: order :func:`apply` runs them in. Both produce the same refusal, so the
#: first to fire ends it. Everything else about a rule name — validation, the
#: published set, dispatch — is derived from here, so a rule cannot be
#: declarable without being runnable.
_REGISTRY = (
    ("premise", premise_refusal),
    ("hedge", hedge_refusal),
)

#: Every rule name a policy may declare.
RULES = frozenset(name for name, _ in _REGISTRY)


def _declared(names) -> frozenset[str]:
    """Normalise rule names, refusing any the registry does not define."""
    named = frozenset(str(name).strip().lower() for name in names if str(name).strip())
    if unknown := sorted(named - RULES):
        raise ValueError(f"unknown post-processing rule(s): {', '.join(unknown)}")
    return named


@dataclass(frozen=True)
class PostprocPolicy:
    """Which rules a deployment has declared, resolved once."""

    rules: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Validating on construction rather than in `parse` leaves no way in:
        # a policy assembled by hand cannot name a rule `apply` would silently
        # never run.
        object.__setattr__(self, "rules", _declared(self.rules))

    def __bool__(self) -> bool:
        return bool(self.rules)

    def enables(self, rule: str) -> bool:
        return rule in self.rules

    @classmethod
    def parse(cls, value: str | None) -> "PostprocPolicy":
        """Parse a declaration ("premise,hedge") into a policy."""
        return cls(rules=(value or "").split(","))

    @classmethod
    def resolve(cls, declared: str | None, override: str | None = None) -> "PostprocPolicy":
        """The policy a run executes: the binding's declaration, or an override."""
        # An override of "" is a decision to run nothing, not an absent
        # override: only None means the caller did not express one.
        return cls.parse(override if override is not None else declared)


def apply(data: PostprocInput, policy: PostprocPolicy) -> tuple[str, list[dict]]:
    """Apply the declared rules in a fixed order, returning answer and log."""
    if not policy:
        return data.answer, []
    current = data.answer
    fired: list[dict] = []
    for name, rule in _REGISTRY:
        if not policy.enables(name) or fired:
            continue
        hit = rule(replace(data, answer=current))
        if hit:
            current, log = hit
            fired.append(log)
    return current, fired
