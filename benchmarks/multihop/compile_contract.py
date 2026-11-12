"""Structured transport for the existing KITE DSL; no query repair or new ops.

Opt-in on the benchmark binding only. Nullable fields are wire-level omissions,
not defaults inferred from questions. Every decoded plan still passes QueryPlan.
"""
from copy import deepcopy

INSTRUCTION = ('\nOUTPUT TRANSPORT: Wrap the native plan in {"plan":...}. '
               'Use JSON null for unused filter fields; null means omitted. '
               'For staged queries, use short ASCII names and reference only earlier stages '
               'with an explicit projection such as $s1.units or $s1.date. '
               'The supplied schema defines the existing DSL limits.\n')

STAGE_NAME = r'[A-Za-z_][A-Za-z0-9_]*'


def obj(props):
    return dict(type='object', properties=props, required=list(props), additionalProperties=False)


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def array(items, **bounds):
    return dict(type='array', items=items, **bounds)


def response_format(allow_stop=False):
    literal = {'type': 'string', 'pattern': r'^[^$]*$'}
    string = {'type': 'string'}
    definitions = {}
    pipe = {'anyOf': [
        obj({'op': {'type': 'string', 'enum': ['sort']},
             'key': {'type': 'string', 'enum': ['t', 'dur']}, 'desc': {'type': 'boolean'}}),
        obj({'op': {'type': 'string', 'enum': ['head', 'tail']},
             'n': {'type': 'integer', 'minimum': 1}}),
        obj({'op': {'type': 'string', 'enum': ['count']}})]}
    for staged in (False, True):
        mode = 'stages' if staged else 'queries'
        unit_ref = {'type': 'string', 'pattern': rf'^\${STAGE_NAME}\.(unit|units)$'}
        date_ref = {'type': 'string', 'pattern': rf'^\${STAGE_NAME}\.date$'}
        unit_value = {'anyOf': [literal, unit_ref]} if staged else literal
        units = array(unit_value)
        if staged:
            units = {'anyOf': [units, unit_ref]}
        time_value = {'anyOf': [literal, date_ref]} if staged else literal
        time = array(time_value, minItems=1, maxItems=2)
        if staged:
            time = {'anyOf': [time, date_ref]}
        entry_refs = []
        for target in ('facts', 'lines', 'units'):
            fields = dict(units=nullable(units), time=nullable(time), grep=nullable(string))
            if target != 'units':
                fields['who'] = nullable(array(literal))
            if target == 'facts':
                for name in ('entities', 'obj', 'place', 'event', 'kind', 'grep_all'):
                    fields[name] = nullable(array(literal))
                fields.update(topics=nullable(array(obj({'code': string, 'closure': {'type': 'boolean'}}))),
                              entity_kind=nullable(string),
                              conf_min=nullable({'type': 'string', 'enum': ['low', 'med', 'high']}))
            key = mode + '_' + target
            definitions[key + '_where'] = obj(fields)
            props = {}
            if staged:
                props['name'] = {'type': 'string', 'pattern': rf'^{STAGE_NAME}$'}
            props.update(select={'type': 'string', 'enum': [target]},
                         where={'$ref': '#/$defs/' + key + '_where'},
                         pipe=array(pipe, maxItems=0 if target == 'lines' else 4))
            definitions[key] = obj(props)
            entry_refs.append({'$ref': '#/$defs/' + key})
        definitions[mode] = obj({
            'intent': {'type': 'string', 'enum': ['factual', 'speculative', 'aggregate', 'enumeration', 'attribution']},
            mode: array({'anyOf': entry_refs}, minItems=1, maxItems=4 if staged else 3)})
    branches = [{'$ref': '#/$defs/queries'}, {'$ref': '#/$defs/stages'}]
    if allow_stop:
        branches.append(obj({'stop': {'type': 'boolean', 'enum': [True]}}))
    schema = obj({'plan': {'anyOf': branches}})
    schema['$defs'] = definitions
    return {'type': 'json_schema', 'json_schema': {'name': 'kite_native_plan', 'strict': True, 'schema': schema}}


def decode(value, *, allow_stop=False):
    """Reject malformed transport. Omit only explicitly null filter fields."""
    from jsonschema import Draft202012Validator
    from memoket_kite.pipeline.compile_plan import _validated_compiled_plan
    Draft202012Validator(response_format(allow_stop)['json_schema']['schema']).validate(value)
    plan = deepcopy(value['plan'])
    for entry in plan.get('stages', plan.get('queries', [])):
        entry['where'] = {k: v for k, v in entry['where'].items() if v is not None}
    return _validated_compiled_plan(plan, allow_stop=allow_stop)
