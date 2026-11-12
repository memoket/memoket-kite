"""Shared extraction/Compile semantics for source-bound document indexes only."""
from types import SimpleNamespace

EVENT_MEANINGS = {
    'birth':'a person or animal being born',
    'death':'a person or animal dying',
    'marriage':'a wedding or marital union between people',
    'founding':'initial establishment of an organization or settlement',
    'publication':'publication of a written work',
    'release':'a work or product made publicly available',
    'election':'selection by voting in an election',
    'competition':'participating in or winning a contest',
    'discovery':'finding something previously unknown',
    'conflict':'fighting or an explicit dispute',
    'move':'relocation of a residence or base',
}
EVENT_DEFINITION = '; '.join(k+' = '+v for k,v in EVENT_MEANINGS.items())
FIELD_SEMANTICS = '''FIELD SEMANTICS:
kind: identity = a referent's property; relationship = a link between referents,
including roles and nested possessive links; event = an asserted occurrence or
state transition (including birth/death); plan = an intended occurrence;
preference/opinion = a preference or attributed judgment; other = none of these.
who is the subject of the selected predicate, not every participant. entities
indexes its other named participants; mentions alone do not establish a role.
event is optional: use [] when no defined category applies. Only event/plan
assertions take these labels; a label does not prove the occurrence happened.
t is this assertion's time about who; preserve YYYY, YYYY-MM or YYYY-MM-DD.
Use null if unstated, not another referent's date. A work year is an attribute,
not proof of a release/publication event.
'''

COMPILE_FIELD_RULES = '''FIELD USE:
Use who for assertion subjects; only registered entity aliases establish name
equivalence. Entities mark mentions, not subject or relation-role proof.
Use event to find a requested event category. Add kind only when the question
needs an assertion-type distinction, not mechanically beside every event filter.
For relationships, retrieve the actual predicate and endpoints; relationship
alone specifies neither the relation nor its direction. Filters intersect.
Use time for source events, not ingestion dates. A coarse date is imprecise,
not January or day one. Source evidence, not index labels, establishes the answer.
'''

INDEX_STORAGE = '''INDEX STORAGE:
Each unit retains its complete original source. Document-wide postings coexist
with source-quoted assertion indexes carrying who/kind/t/event. Empty fields
are unknown, not negative facts. Retrieved sources are delivered intact.
'''


def compiler_profile():
    """Isolated pilot binding; historical and conversation profiles unchanged."""
    from benchmarks.multihop import profile
    values={k:v for k,v in vars(profile).items() if not k.startswith('__')}
    values.update(COMPILE_PROMPT=INDEX_STORAGE+profile.COMPILE_PROMPT+"\nEVENT CATEGORIES: "+EVENT_DEFINITION,
                  COMPILE_CANDIDATES=1,COMPILE_MAX_ROUNDS=1,
                  COMPILE_CONTRACT=None,DEFAULT_BUDGET=40)
    return SimpleNamespace(**values)
