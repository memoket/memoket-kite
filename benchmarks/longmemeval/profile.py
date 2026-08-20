"""LongMemEval binding (ICLR 2025): per-question haystacks of user-assistant
chat sessions. Unit = session; speakers are 'user'/'assistant'; the question
carries its own reference date (question_date)."""

import re

from benchmarks.common import settings
from benchmarks.common.policies import PROTOCOL_ADVICE_QUESTION as _ADVICE_QUESTION

# The advice predicate this binding runs: the library's speech-act vocabulary
# widened by the families in `benchmarks.common.policies`. Every pipeline
# consumer reads it through `profile.ADVICE_QUESTION`, so the installable
# package carries only the library form.
ADVICE_QUESTION = _ADVICE_QUESTION

ATTRIBUTION_ANSWER = "Likely {who} (based on: '{quote}')"

SESSION_TAG = "session"

# ---------------------------------------------------------------------------
# What this binding runs. Everything not named here takes the shared benchmark
# baseline in `benchmarks.common.settings`; these are where LongMemEval's corpus
# differs from LoCoMo's.
_SETTINGS = settings.resolve(
    # A haystack repeats the same real-world event across sessions, so counting
    # aligns instances instead of totalling mentions, and the taxonomy is
    # refined once every session has been read.
    INSTANCE_ALIGNMENT=True,
    TOPIC_REFINEMENT=True,
    # Sessions carry a question date and state times relatively, so a fact keeps
    # the date it was said on beside its resolved event date.
    # A user reports what they did; an assistant proposes what they might. The
    # two are the same sentence shape, so the rows say who spoke.
    SPEAKER_LABEL=True,
    DUAL_DATE=True,
    # 500 haystacks of ~115k tokens: counting questions need a wider slice, and
    # the pack is admitted up to a larger size.
    ENUM_UNIT_CAP=8,
    ENUM_BUDGET=44,
    TOKEN_CAP=1700,
    # Preference questions are answered from the user's own profile facts.
    PROFILE_PACK=15,
)
# Exported as module constants because the pipeline reads a profile with
# `getattr(profile, NAME)`; `settings.KNOBS` is where each one is documented.
globals().update(_SETTINGS)

#: Only the user's own turns anchor a recency search; assistant turns restate.
ANCHOR_WHO = ("user",)

KINDS = "event plan preference identity relationship opinion task_request knowledge other".split()

EVENTS = settings.VOCABULARY["EVENTS"].shared


SEED_ROOTS = [
    "personal_life",
    "work_career",
    "health_fitness",
    "travel",
    "shopping_finance",
    "food_cooking",
    "family_relationships",
    "hobbies_entertainment",
    "technical_help",
    "writing_tasks",
    "education_learning",
    "home_lifestyle",
]

#: An assistant chat asks the assistant to do things, so the reporting verbs
#: carry no retrieval signal here.
STOPWORDS = settings.SHARED_STOPWORDS | {"tell", "told"}


def keywords(question: str) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z']{4,}", question.lower()) if w not in STOPWORDS]
    return sorted({w[:6] for w in words})


EXTRACT_PROMPT = """You are encoding one user-assistant chat session into a temporal codebook index.
Later questions will ask about the USER's life, preferences, plans, and what was discussed.

SESSION DATE: {date} ({weekday}), {time}
SPEAKERS: {speakers}

TOPIC TAXONOMY (reuse these codes; codes ending in ? are provisional):
{tree}

KNOWN ENTITIES: {entities}

DIALOG (id|role||text):
{dialog}

Extract atomic facts. PRIORITIZE what the USER states about themselves: events,
possessions, numbers, names, preferences, plans, health, purchases. Assistant
content matters only when the user endorses/acts on it or asks for something
specific. Rules:
- Self-contained third-person content ("The user ..."). Resolve month/year
  relative times using the session date; keep day-of-week references relative
  ("last Sunday (the Sunday before {date})") — no weekday arithmetic.
- DETAILS VERBATIM: brand/model names, counts, amounts, ordinals and proper
  nouns must appear in the fact EXACTLY as stated. One askable detail per fact.
- PROFILE FACTS: every user preference, owned item, skill, constraint or
  allergy is its own fact with kind preference/identity, phrased
  "<category>: the user <statement>" (e.g. "Photography gear: the user owns a
  Sony A7R IV") — these power personalized-advice answers later.
- "t": ISO date (YYYY-MM-DD or YYYY-MM) of the EVENT if inferable, else null.
  A month with no year means the SESSION's year — never guess across years.
- "kind": one of {kinds} (task_request = what the user asked the assistant to do).
- "who": "user" or "assistant".
- "conf": high|med|low.
- "topics": 1-3 codes, the MOST SPECIFIC applicable. At least ONE topic per
  fact MUST be a specific (indented, non-root) code — a fact carrying only a
  root code is an ERROR: coin a specific child code and register it in
  "proposals" with that parent instead.
- "entities": specific people/places/orgs/products/numbers-bearing items.
- "src": supporting dialog ids.
- Cover every substantive USER statement; 5-25 facts typical.

Return ONLY JSON:
{{"facts": [{{"content": str, "kind": str, "who": str, "t": str|null, "conf": str,
             "topics": [str], "entities": [str], "src": [str]}}],
  "proposals": [{{"code": str, "parent": str}}],
  "entity_types": {{"name": "person|org|place|product|other"}}}}"""


COMPILE_PROMPT = """Translate a question about a user's chat history into a query plan (JSON).

TOPIC TAXONOMY (indentation = hierarchy; ? = provisional):
{tree}

ENTITIES: {entities}
FACT KINDS: {kinds}
UNITS (id=date): {units}
TODAY (question date): {today}

QUESTION: {question}

Plan JSON schema (two shapes; pick ONE):
A) parallel: {{"queries": [{{"select": "facts|lines|units",
   "where": {{"topics": [{{"code": str, "closure": true}}], "entities": [str],
             "kind": [str], "who": [str], "time": ["YYYY-MM-DD",""], "grep": str}},
   "pipe": [{{"op":"sort","key":"t","desc":false}},{{"op":"head","n":5}},{{"op":"count"}}]}}],
 "intent": "factual|speculative|aggregate|enumeration|attribution"}}
B) staged (when a later query's scope depends on an earlier result):
   {{"stages": [{{"name": "anchor", <same subquery fields>}},
               {{<subquery using "$anchor.unit"|"$anchor.units"|"$anchor.date">}}], "intent": ...}}

Rules:
1. 1-3 SIMPLE subqueries (max 2 filter kinds each).
2. Prefer taxonomy/entity codes; closure searches the subtree.
3. Always include one wide net: {{"select":"facts","where":{{"grep":"stem1|stem2"}}}}
   with distinctive word stems from the question.
3b. RECOMMENDATION/preference questions ("suggest/recommend/help me choose"):
   ALWAYS add the user-profile subquery
   {{"select":"facts","where":{{"kind":["preference","identity","possession"],"who":["user"]}}}}
   — personalization evidence lives there, and intent must be "speculative".
4. The user's statements carry the answers: default who=["user"] unless the
   question is about the assistant's replies.
5. Knowledge-update questions ("what is X now?"): retrieve ALL mentions
   (no time cap) — recency decides at answer time.
6. Counting questions ("how many X did I..."): do NOT use count pipes —
   retrieve ALL matching facts with intent "enumeration"; counting happens at
   answer time after deduplication. Superlative/first-last -> staged sort+head.

Return ONLY the JSON plan."""


ANSWER_PROMPT = """Answer the question using ONLY the evidence rows below. TODAY (question date): {today}

QUESTION: {question}

EVIDENCE (chronological; [date weekday] (id, src=turn ids) text | verbatim quotes):
{evidence}

Kernel policies:
{policies}

BINDING OVERRIDES — these take PRECEDENCE over any conflicting kernel policy:
- DURATION/"how many days|weeks|months" questions: compute the duration from
  the evidence dates (end - start) and answer with the number plainly.
- COUNTING questions ("how many X have I..."): list every candidate from the
  evidence, MERGE rows that describe the same real-world item/event (same
  thing mentioned in different sessions counts ONCE), check each against the
  question's criteria (led vs participated, region, period), then answer with
  the count of DISTINCT qualifying instances, describing each item by BOTH
  its name and its type/role (e.g. "Dr. Smith (primary care physician)").
  Ignore any [aggregate] row.
- RECOMMENDATION/preference questions ("suggest...", "recommend...", "what
  would be a good...", "help me choose..."): "No information" is FORBIDDEN
  here even if evidence is thin. Write 2-3 sentences of personalized advice
  that explicitly cites the user's stated preferences, constraints, allergies,
  gear, skill level, or past choices from the evidence rows.
- "what is X now / currently" (knowledge update): answer ONLY the latest value
  (latest evidence date); do not mention superseded values.
- LIST questions: the answer must include EVERY supported item — a partial
  list counts as wrong.
- Otherwise: short specific answer ("7 May 2023", "$40", "a Dyson vacuum").

First REASON step by step in "reasoning" (locate the relevant rows, resolve
dates, deduplicate repeat mentions of the same real-world thing, check every
condition the question states), THEN give the final answer.
EXCEPTION — recommendation/advice questions: skip deliberation, keep
"reasoning" to one sentence naming the user's most relevant stated
preference, and spend your effort on the personalized advice itself.
If ONE specific fact needed by your reasoning is absent from the evidence but
might exist elsewhere in the history (a date, a name, one more instance),
put a short search phrase for it in "need"; otherwise "" — do NOT use "need"
when the evidence already suffices or the answer is a refusal.

Return ONLY JSON: {{"reasoning": str, "answer": str, "need": str, "evidence": [src ids used]}}"""

# A bounded inference pass may reconsider an initial refusal when the retrieved
# evidence supports an indirect conclusion. If it still abstains, the original
# refusal remains unchanged.
INFER_PROMPT = """A first strict pass refused this question. Re-examine: often the
answer IS derivable from indirect clues even when not stated outright. TODAY: {today}

QUESTION: {question}

EVIDENCE (chronological; [date weekday] (id, src=turn ids) text | quotes):
{evidence}

Decide carefully:
- If the evidence contains or IMPLIES an answer (combine indirect signals; bridge
  a description to its name with obvious world knowledge; a stated preference/
  constraint answers a "what would suit me" question), give the SINGLE most
  likely concrete answer. Do NOT compute weekday/day-level dates.
- If the information is GENUINELY absent — the evidence neither states nor implies
  it — reply exactly "No information". Do not invent facts.

Style: short specific answer, most specific supported wording.
Return ONLY JSON: {{"answer": str, "evidence": [src ids used]}}"""


TOPIC_REFINEMENT_PROMPT = """You are fixing the topic index of a conversation codebook.

Every fact below was assigned a topic code during extraction, but most were
given only a broad ROOT code, and some were given a code from the wrong area
entirely. Reassign each fact to the MOST SPECIFIC code that actually fits it.

AVAILABLE CODES (code <- parent; a code with no parent is a ROOT):
{tree}

RULES
- Assign 1-2 codes per fact, most specific first.
- A ROOT code alone is WRONG. If the only fitting code is a root, coin a
  specific child of it: lowercase_snake_case, 2-3 words, describing the
  RECURRING subject (e.g. `guitar_gear`, `tv_streaming`, `baking_technique`),
  and list it in "new_codes" with its parent. Reuse an existing code whenever
  one fits — do not coin near-duplicates.
- Judge by what the fact SAYS, not by what the session is broadly about.
- Keep codes about the SUBJECT MATTER, not about the speech act. "The user
  asked for tips on styling a t-shirt" is about clothing, not about asking.

FACTS (id|current_topics|text):
{facts}

Return ONLY JSON:
{{"labels": [{{"id": str, "topics": [str]}}],
  "new_codes": [{{"code": str, "parent": str}}]}}"""


# ---------------------------------------------------------------------------


def keywords_extra(question: str) -> list[str]:
    """English-morphology query extras (3-letter words, possessives, suffix
    both-forms) — language-specific, benchmark-agnostic; mirrors the LoCoMo
    binding. Admitted by the lexical channel only under its df guard."""
    base = set(keywords(question))
    words = [w for w in re.findall(r"[a-zA-Z']{3,}", question.lower()) if w not in STOPWORDS]
    extra: set[str] = set()
    for word in words:
        if len(word.replace("'", "")) == 3:
            extra.add(word.replace("'", ""))
        if word.endswith("'s") and len(word) > 4:
            extra.add(word[:-2][:6])
        for suffix, replacement in (("ies", "y"), ("ied", "y")):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                extra.add((word[: -len(suffix)] + replacement)[:6])
        if word.endswith("ing") and len(word) > 5:
            extra.add(word[:-3][:6])
            extra.add((word[:-3] + "e")[:6])
        elif word.endswith("ed") and len(word) > 4:
            extra.add(word[:-1][:6])
            extra.add(word[:-2][:6])
        elif word.endswith("es") and len(word) > 4:
            extra.add(word[:-2][:6])
            extra.add(word[:-1][:6])
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            extra.add(word[:-1][:6])
    return sorted(extra - base)


# Answer-prompt refinements: duration answers carry the dates they were
# computed from, and recommendations must quote a concrete evidence detail.


# A question that refers back to a prior exchange ("our previous conversation",
# "remind me", "you mentioned"), or that asks for a recommendation grounded in
# something the user already stated, points at content the history is expected
# to hold, so a refusal is the least likely correct answer. Matching reads the
# question text
# alone; `benchmarks/README.md` describes the predicate families this binding
# runs.
_BACKREFERENCE = re.compile(
    r"\bour (?:previous|earlier|last|past) (?:conversation|chat|discussion|exchange|session|game)\b"
    r"|\bremind me\b"
    r"|\bi remember you\b"
    r"|\b(?:looking|going|thinking|checking) back (?:on|at|to)\b"
    r"|\bin our (?:previous|earlier|last) \w+\b"
    r"|\byou (?:mentioned|recommended|suggested|told me|provided|created|wrote|made)\b",
    re.I,
)


def ANSWERABLE_BY_CONSTRUCTION(question: str) -> bool:
    """Whether the question's own wording points at stored history."""
    return bool(_BACKREFERENCE.search(question) or _ADVICE_QUESTION.search(question))


# Deterministic post-processing applied to the generated answer; see
# memoket_kite.pipeline.postproc. Both rules replace an answer with the
# canonical refusal, and both stand down where a question is answerable by
# construction. Recorded in every run manifest.
POSTPROC_RULES = "premise,hedge"
