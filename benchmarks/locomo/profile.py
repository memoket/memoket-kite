"""LoCoMo binding: English casual multi-session dialogs.

Everything here is domain/language configuration. No kernel logic.
"""

import re

from benchmarks.common import settings
from benchmarks.common.policies import PROTOCOL_ADVICE_QUESTION

ATTRIBUTION_ANSWER = "Likely {who} (speaker label; based on: '{quote}')"

# The advice predicate this binding runs: the library's speech-act vocabulary
# widened by the families in `benchmarks.common.policies`. Every pipeline
# consumer reads it through `profile.ADVICE_QUESTION`.
ADVICE_QUESTION = PROTOCOL_ADVICE_QUESTION

SESSION_TAG = "session"

# ---------------------------------------------------------------------------
# What this binding runs. Everything not named here takes the shared benchmark
# baseline in `benchmarks.common.settings`; these four are where LoCoMo's corpus
# differs from LongMemEval's.
_SETTINGS = settings.resolve(
    # Its QA set contains no unanswerable questions, so a bounded inference pass
    # can only recover an answer, never invent a refusal.
    INFER_PASS=True,
    # A "how many" question is answered from the plan's own unit count.
    AGGREGATE_SHORTCIRCUIT=True,
    # Ten long dialogs rather than one haystack per question: more rows are worth
    # admitting per unit, and the pack is capped tighter.
    ENUM_UNIT_CAP=6,
    TOKEN_CAP=1500,
)
# Exported as module constants because the pipeline reads a profile with
# `getattr(profile, NAME)`; `settings.KNOBS` is where each one is documented.
globals().update(_SETTINGS)

KINDS = (
    "event plan preference identity relationship emotion opinion health possession other".split()
)

EVENTS = settings.VOCABULARY["EVENTS"].shared


SEED_ROOTS = [
    "relationships",
    "family",
    "work_career",
    "education",
    "health",
    "activities_hobbies",
    "arts_creativity",
    "travel",
    "life_events",
    "identity_beliefs",
    "possessions",
    "community",
]

STOPWORDS = settings.SHARED_STOPWORDS


def keywords(question: str) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z']{4,}", question.lower()) if w not in STOPWORDS]
    return sorted({w[:6] for w in words})


# `keywords_extra` is a separate function on purpose: keywords() also feeds grep
# injection into cached plans (retrieve._normalize_anchor_queries) and instance
# scoring, so it must not change. The lexical channel admits these extras only
# where the store's posting list for the stem is small, keeping the ranking
# reshuffle surface minimal.


def keywords_extra(question: str) -> list[str]:
    base = set(keywords(question))
    words = [w for w in re.findall(r"[a-zA-Z']{3,}", question.lower()) if w not in STOPWORDS]
    extra: set[str] = set()
    for word in words:
        # Three-letter words fall below keywords()'s floor but the store indexes them.
        if len(word.replace("'", "")) == 3:
            extra.add(word.replace("'", ""))
        if word.endswith("'s") and len(word) > 4:  # possessive base form
            extra.add(word[:-2][:6])
        # Both surface forms via an explicit suffix table, so no stemmer dependency.
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


EXTRACT_PROMPT = """You are encoding one conversation session into a temporal codebook index.

SESSION DATE: {date} ({weekday}), {time}
SPEAKERS: {speakers}

TOPIC TAXONOMY (reuse these codes; codes ending in ? are provisional):
{tree}

KNOWN ENTITIES: {entities}

DIALOG (id|speaker||text):
{dialog}

Extract atomic facts covering EVERY substantive line (names, numbers, foods,
activities, feelings, photos count). Rules:
- Self-contained third-person content. Relative times: resolve month/year level
  ("next month" -> the month AFTER the SESSION DATE above; "last month" -> the
  month before it) but NEVER compute day-of-week references — keep them
  relative: "last Sunday (the Sunday before {date})", no date arithmetic.
- "t": ISO date (YYYY-MM-DD or YYYY-MM) of the EVENT if inferable, else null.
  An event mentioned with a month but no year happened in the SESSION's year
  unless the speaker says otherwise — never guess across years.
- "kind": one of {kinds}.
- "who": lowercase first name of the speaker the fact is ABOUT (its subject) —
  not necessarily the one speaking. When A reports B's deed, who=B and the
  content notes "(reported by A)".
- "conf": high|med|low (clarity + attribution certainty).
- "topics": 1-3 codes, the MOST SPECIFIC applicable. At least ONE topic per
  fact MUST be a specific (non-root) code — a fact carrying only a bare root
  code is an ERROR: coin a specific child of it (root "<area>" -> child
  "<area>_<the particular thing>") and register it in "proposals" with that
  parent — a healthy taxonomy grows a mid layer. Reuse specific codes first.
  CALIBRATION: if the taxonomy above has few or no specific (indented) codes
  for this session's recurring subjects, you MUST propose 2-4 specific child
  codes — an all-roots taxonomy cannot support retrieval.
- "entities": specific people/places/orgs/works mentioned (lowercase canonical).
- "src": supporting dialog ids.
Return ONLY JSON:
{{"facts": [{{"content": str, "kind": str, "who": str, "t": str|null, "conf": str,
             "topics": [str], "entities": [str], "src": [str]}}],
  "proposals": [{{"code": str, "parent": str}}],
  "entity_types": {{"name": "person|org|place|product|work|animal|activity"}}}}
entity_types values MUST be exactly one of those seven — no other word."""


COMPILE_PROMPT = """Translate a question about indexed conversations into a query plan (JSON).

TOPIC TAXONOMY (indentation = hierarchy; ? = provisional):
{tree}

ENTITIES: {entities}
FACT KINDS: {kinds}
UNITS (id=date): {units}
TODAY: {today}

QUESTION: {question}

Plan JSON schema (two shapes; pick ONE):
A) parallel: {{"queries": [{{"select": "facts|lines|units",
   "where": {{"topics": [{{"code": str, "closure": true}}], "entities": [str],
             "kind": [str], "who": [str], "time": ["YYYY-MM-DD",""], "grep": str}},
   "pipe": [{{"op":"sort","key":"t|dur","desc":false}},{{"op":"head","n":5}},{{"op":"count"}}]}}],
 "intent": "factual|speculative|aggregate|enumeration|attribution"}}
B) staged (multi-hop, when a later query's SCOPE depends on an earlier result):
   {{"stages": [{{"name": "anchor", <same subquery fields>}},
               {{<subquery using "$anchor.unit" | "$anchor.units" | "$anchor.date">}}],
    "intent": ...}}
   Example — "When did X first happen, was there a follow-up?":
   {{"stages": [
     {{"name": "first", "select": "facts", "where": {{"grep": "X"}},
       "pipe": [{{"op":"sort","key":"t"}},{{"op":"head","n":1}}]}},
     {{"select": "facts", "where": {{"grep": "X", "time": ["$first.date", ""]}}}}]}}
   Example — "What was agreed at the <meeting>? / what next steps after it?":
   {{"stages": [
     {{"name": "mtg", "select": "units", "where": {{"grep": "<meeting keywords>"}},
       "pipe": [{{"op":"head","n":1}}]}},
     {{"select": "facts", "where": {{"units": "$mtg.units", "kind": ["plan"]}}}}]}}

Rules:
1. 1-3 complementary subqueries; each SIMPLE (max 2 filter kinds per subquery).
2. Prefer taxonomy/entity codes; "closure": true searches the whole subtree.
   Codes are EXACT strings from the lists (no paths like "family/adoption").
   The two speakers are NOT entities — filter them with "who".
3. Always add one wide-net subquery: {{"select":"facts","where":{{"grep":"stem1|stem2"}}}}
   with 1-3 distinctive word stems — truncate before the inflection so one
   stem catches every form of the word.
4. Profile/status questions: add kind filter identity/relationship/preference.
5. Counting questions -> select "units" or "facts" with pipe count.
6. "who said/decided X" questions -> intent "attribution" plus a lines
   subquery grepping the quoted phrase. "who" filters the acting speaker only.
7. Superlative/comparative questions ("longest", "most", "first/earliest one")
   MUST use the staged shape with a sort pipe (sort t / sort dur) + head 1.
8. In an anchor stage, use every constraint the question offers (date, keywords)
   so the anchor resolves to ONE unit.

Return ONLY the JSON plan."""


ANSWER_PROMPT = """Answer the question using ONLY the evidence rows below. TODAY: {today}

QUESTION: {question}

EVIDENCE (chronological; [date weekday] (id, src=dialog ids) text | verbatim quotes):
{evidence}

Kernel policies:
{policies}

Style: give ONLY the core answer as a short phrase ("7 May 2023", "a golden
retriever", "painting and hiking"). Enumerations: every distinct supported item,
comma-separated. Dates like "7 May 2023".
Pick the MOST SPECIFIC supported wording — "red and purple lighting" beats
"colorful lighting"; a brand/model name beats its category; if several rows
describe the same thing at different precision, answer with the most precise.

Return ONLY JSON: {{"answer": str, "evidence": [src ids used]}}"""

# Refusal-only inference from indirect evidence.
INFER_PROMPT = """The evidence below DOES contain enough to answer — the answer
is not stated outright but follows from the clues. TODAY: {today}

QUESTION: {question}

EVIDENCE (chronological; [date weekday] (id, src=dialog ids) text | quotes):
{evidence}

Reason from the clues to the SINGLE most likely answer:
- Combine indirect signals (a named place implies its state/country; being in
  school implies a young age; shared history/references imply a relationship).
- Bring in obvious world knowledge to bridge a description to its name (a park
  described by its features -> that park; a category -> a concrete instance).
- You MUST commit to a concrete best answer. NEVER reply "No information" or
  "cannot be determined" — pick the most plausible specific answer the clues
  support. Do NOT compute weekday/day-level dates; keep any date as stated.

Style: ONLY the core answer as a short phrase; most specific supported wording.
Return ONLY JSON: {{"answer": str, "evidence": [src ids used]}}"""


# The LoCoMo QA set contains no unanswerable questions, so every question is
# answerable by construction and a refusal is always a miss. Declaring this
# turns refusal-emitting rules off here through the same uniform mechanism
# used on any other deployment, rather than through a per-benchmark switch.
def ANSWERABLE_BY_CONSTRUCTION(question: str) -> bool:
    return True


# Deterministic post-processing applied to the generated answer; see
# memoket_kite.pipeline.postproc. The published configuration declares none.
POSTPROC_RULES = ""
