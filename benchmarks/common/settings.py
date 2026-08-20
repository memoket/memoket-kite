"""The behaviour a benchmark binding selects, declared in one place.

A binding is mostly one thing: a set of choices about how the pipeline should
retrieve and answer. Those choices used to be loose constants at the top of each
profile, which made two questions hard to answer — what can be tuned at all, and
where do the two bindings actually differ? Both are answered here.

There are two layers, and keeping them apart is the point:

* The **library** enables almost nothing by default: a deployment opts into
  the retrieval channels and the extra reader passes it wants. (The one
  exception is the aggregate short-circuit, which answers a counting question
  without a reader call and so defaults on as a cost saving.)
* A **benchmark** measures the system as it is meant to be used, so it opts into
  the whole pipeline. `BASELINE` is that opt-in, written once instead of once
  per binding.

A profile then states only what it differs on, and `resolve()` returns the
complete table it exports. Nothing stays implicit: every knob has a value, and
`KNOBS` is also what the run manifest records, so a published score describes a
configuration rather than a set of defaults nobody wrote down.

Ablation is orthogonal, and deliberately narrow: only knobs marked `ARM` answer
to `KITE_ARM`, because that membership is what an arm *means*. Widening it would
silently make two arm scores measure different things; see
`benchmarks.common.experimental`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from benchmarks.common import experimental

#: How a knob may be overridden for an experiment. The distinction is not
#: cosmetic: `KITE_ARM=baseline` switches every ARM knob off at once, so which
#: knobs are ARM decides what an ablation arm *means*, and a published arm score
#: is only comparable to another produced under the same membership.
ARM = "arm"  # participates in KITE_ARM, and in its own KITE_<NAME>
OWN = "own"  # its own KITE_<NAME> only: prices a cost rather than naming a mechanism
SIZE = "size"  # an integer budget, through its own KITE_<NAME>
FIXED = "fixed"  # not overridable: a structural choice a binding owns outright


@dataclass(frozen=True)
class Knob:
    """One setting: what it controls and how an experiment may override it."""

    purpose: str
    override: Literal["arm", "own", "size", "fixed"] = FIXED
    #: Environment-variable stem, when it differs from the knob name.
    env: str = ""

    def __post_init__(self) -> None:
        # A misspelled kind would otherwise fall through to FIXED and quietly
        # remove a knob from every arm it belongs to.
        if self.override not in (ARM, OWN, SIZE, FIXED):
            raise ValueError(f"unknown override kind: {self.override!r}")


#: Retrieval channels and evidence-pack construction.
RETRIEVAL = {
    "LEXICAL_V2": Knob("BM25-lite lexical channel alongside symbolic retrieval", ARM),
    "LEXICAL_EXTRA": Knob("morphological query stems, under a document-frequency guard", ARM),
    "PACK_V2": Knob("rank-fused evidence pack rather than channel concatenation", ARM),
    "DATE_CHANNEL": Knob("retrieve by date window when a question names one", ARM),
    "NEIGHBOR": Knob("render the dialog lines adjacent to each packed row", ARM),
    "ENUM_BASIS": Knob("render a numbered member list under a counting aggregate", ARM),
}

#: How the answer stage spends reader calls.
ANSWERING = {
    "SECOND_PASS": Knob("re-ask the reader once with the evidence it asked for"),
    "ROUND2": Knob("second retrieval round when the first draft looks unsupported", ARM),
    "PLAN_REPAIR": Knob("recompile a plan that returned nothing"),
    "REFUSAL_REPLAN": Knob("retry a refusal with a widened plan"),
    "HYDRATE": Knob("expand packed rows with their source text"),
    "INFER_PASS": Knob("let a bounded inference pass reconsider a refusal"),
    "INFER_V2": Knob("the revised inference prompt", ARM),
    "SPEAKER_LABEL": Knob(
        "name the speaker on a dialogue row and on a quoted source, so a suggestion the "
        "assistant made is not read as something the user did",
        ARM,
    ),
    "DUAL_DATE": Knob(
        "annotate a relative-phrase fact with the session date it was said on, so the "
        "reader does not resolve the phrase a second time",
        ARM,
    ),
    "SUPPORT_CHECK": Knob(
        "a second reader call that downgrades an unsupported answer to a refusal; a "
        "cost knob rather than a mechanism, so it stays outside the arms",
        OWN,
    ),
    "SELF_CONSIST": Knob("extra samples of the answer prompt to draw and reconcile", SIZE),
}

#: Bounds on work. None of these names a mechanism.
BUDGETS = {
    "DEFAULT_BUDGET": Knob("upper bound on evidence rows", SIZE, env="BUDGET"),
    # A binding setting this to 0 admits rows without a size bound; an
    # experiment cannot ask for 0 through the environment, because an empty
    # variable in a shell must not silently uncap a run (see `experimental.size`).
    "TOKEN_CAP": Knob("size the rendered pack is admitted up to; 0 leaves it uncapped", SIZE),
    "ENUM_UNIT_CAP": Knob("rows per unit admitted for a counting question"),
    "ENUM_BUDGET": Knob("rows admitted in total for a counting question"),
    "RECENCY_ANCHOR": Knob("recent units consulted when a question has no anchor"),
    "WHOLESALE_CHARS": Knob("characters of a session rendered wholesale"),
    "PROFILE_PACK": Knob("profile facts rendered for an advice question"),
}

#: Structural choices: neither a channel nor a budget.
STRUCTURE = {
    "INSTANCE_ALIGNMENT": Knob("count aligned real-world instances rather than mentions"),
    "TOPIC_REFINEMENT": Knob("refine broad topic assignments once the taxonomy is complete"),
    "AGGREGATE_SHORTCIRCUIT": Knob(
        "answer a counting question from the plan's own unit count, with no reader call"
    ),
}

KNOBS: dict[str, Knob] = {**RETRIEVAL, **ANSWERING, **BUDGETS, **STRUCTURE}


@dataclass(frozen=True)
class Slot:
    """A vocabulary a binding must supply, and what the pipeline does with it."""

    purpose: str
    #: The value both English bindings share, when there is one. A slot without
    #: a shared value is corpus-specific and every binding states its own.
    shared: Any = None


#: The controlled vocabularies a binding provides. Unlike a knob, the value is
#: language- and corpus-specific, so it stays in the profile; declaring the
#: slots here means a binding cannot silently omit one, and lets a run manifest
#: fingerprint the vocabulary a score was produced under — a changed taxonomy
#: changes what the extractor may emit, exactly as a changed prompt does.
VOCABULARY: dict[str, Slot] = {
    "KINDS": Slot("fact kinds the extractor may assign"),
    "SEED_ROOTS": Slot("root codes a taxonomy starts from"),
    "STOPWORDS": Slot("words a query stem is never built from"),
    "EVENTS": Slot(
        "controlled event facets for personal-life conversations; keeping the "
        "vocabulary explicit stops generic or unsupported labels entering the "
        "symbolic index",
        shared=(
            "trip move purchase sale injury illness recovery award competition "
            "performance graduation job_change wedding birth death adoption meeting "
            "celebration accident release rejection milestone first_time"
        ).split(),
    ),
}

#: Words neither English binding builds a query stem from. A binding may extend
#: this; it may not shrink it, because a stem the store never indexes cannot be
#: retrieved by any channel.
SHARED_STOPWORDS = frozenset(
    """the a an is are was were be been do does did doing what when where who whom
which why how i you he she they we it this that these those his her their my your our its
of in on at to for with about from and or not have has had will would could should might
likely there here go going went get got make made say said take took""".split()
)


def require_vocabulary(binding: object) -> None:
    """Fail a binding that does not supply every declared vocabulary."""
    missing = [name for name in VOCABULARY if getattr(binding, name, None) in (None, (), [])]
    if missing:
        raise ValueError(
            f"{getattr(binding, '__name__', binding)} declares no {', '.join(sorted(missing))}"
        )


#: What every benchmark binding runs. A benchmark measures the assembled
#: pipeline, so it turns the retrieval channels and the answer stages on and
#: sizes the packs for long conversations. A binding overrides only where its
#: corpus differs; those overrides are the interesting part of a profile and
#: are meant to be readable as a short list.
BASELINE: dict[str, Any] = {
    "LEXICAL_V2": True,
    "LEXICAL_EXTRA": True,
    "PACK_V2": True,
    "DATE_CHANNEL": True,
    "NEIGHBOR": True,
    "ENUM_BASIS": True,
    "SECOND_PASS": True,
    "ROUND2": True,
    "PLAN_REPAIR": True,
    "REFUSAL_REPLAN": True,
    "HYDRATE": True,
    "INFER_PASS": False,
    "INFER_V2": False,
    "SPEAKER_LABEL": False,
    "DUAL_DATE": False,
    "SUPPORT_CHECK": False,
    "SELF_CONSIST": 0,
    "DEFAULT_BUDGET": 40,
    "TOKEN_CAP": 0,
    "ENUM_UNIT_CAP": 3,
    "ENUM_BUDGET": 28,
    "RECENCY_ANCHOR": 8,
    "WHOLESALE_CHARS": 6000,
    "PROFILE_PACK": 0,
    "INSTANCE_ALIGNMENT": False,
    "TOPIC_REFINEMENT": False,
    "AGGREGATE_SHORTCIRCUIT": False,
}


def resolve(**overrides: Any) -> dict[str, Any]:
    """Every knob's effective value for one binding.

    A binding passes only what it differs on. Each knob is then read according
    to its own `override` kind: ARM knobs answer to `KITE_ARM` and to their own
    variable, SIZE and OWN knobs to their own variable alone, and FIXED knobs
    to neither — a binding owns those outright.
    """
    unknown = set(overrides) - set(KNOBS)
    if unknown:
        raise KeyError(f"unknown binding settings: {', '.join(sorted(unknown))}")
    settings: dict[str, Any] = {}
    for name, knob in KNOBS.items():
        value = overrides.get(name, BASELINE[name])
        variable = knob.env or name
        if knob.override == SIZE:
            settings[name] = experimental.size(variable, value)
        elif knob.override == ARM:
            settings[name] = experimental.flag(variable, value)
        elif knob.override == OWN:
            settings[name] = experimental.enabled(variable, value)
        else:  # FIXED: the binding owns it outright
            settings[name] = value
    if experimental.single_call():
        # A compound arm rather than a knob: it switches several mechanisms off
        # together, which is why it is applied to the resolved table.
        settings.update(
            SELF_CONSIST=0,
            SUPPORT_CHECK=False,
            SECOND_PASS=False,
            REFUSAL_REPLAN=False,
            INFER_PASS=False,
            ROUND2=False,
            PLAN_REPAIR=False,
        )
    # A radius, not a switch: the renderer walks a distance, while the arm
    # switches the mechanism as a whole.
    settings["NEIGHBOR_RADIUS"] = 1 if settings.pop("NEIGHBOR") else 0
    return settings


#: What a run manifest records: every knob a binding can set, plus the settings
#: a binding declares outside this table. Deriving it here means the two lists
#: cannot drift — a binding cannot set something the manifest would not report.
RECORDED: tuple[str, ...] = (
    *(name for name in KNOBS if name != "NEIGHBOR"),
    "NEIGHBOR_RADIUS",
    #: Which deterministic post-processing rules the binding enables.
    "POSTPROC_RULES",
    #: Which speakers may anchor a recency search, when a binding restricts it.
    "ANCHOR_WHO",
)

#: Vocabularies are fingerprinted rather than copied into the manifest: the
#: lists are long, and what a score needs to state is that they did not move.
FINGERPRINTED: tuple[str, ...] = tuple(VOCABULARY)
