import copy
import unittest
from unittest.mock import patch
from benchmarks.multihop import compile_contract as c
from memoket_kite.pipeline.compile_plan import _compile_uncached


def wire(kind='queries', target='facts', name='s1'):
    schema=c.response_format()['json_schema']['schema']
    fields=schema['$defs'][kind+'_'+target+'_where']['properties']
    e={'select':target,'where':{k:None for k in fields},'pipe':[]}
    if kind=='stages':e['name']=name
    e['where']['grep']='anchor'
    return {'plan':{'intent':'factual',kind:[e]}}


class ContractTests(unittest.TestCase):
    def test_only_null_filters_are_omitted(self):
        w=wire();w['plan']['queries'][0]['where']['entities']=[]
        before=copy.deepcopy(w)
        self.assertEqual(c.decode(w),{'intent':'factual','queries':[{'select':'facts','where':{'grep':'anchor','entities':[]},'pipe':[]}]})
        self.assertEqual(w,before)

    def test_limits_are_rejected_not_truncated(self):
        for kind,n in [('queries',4),('stages',5)]:
            w=wire(kind);w['plan'][kind]*=n
            with self.subTest(kind=kind),self.assertRaises(Exception):c.decode(w)

    def test_flat_reference_rejected(self):
        w=wire();w['plan']['queries'][0]['where']['units']='$q1.units'
        with self.assertRaises(Exception):c.decode(w)

    def test_backward_reference_exec_contract(self):
        w=wire('stages');second=copy.deepcopy(w['plan']['stages'][0]);second['name']='s2';second['where']['units']='$s1.units';w['plan']['stages'].append(second)
        self.assertEqual(c.decode(w)['stages'][1]['where']['units'],'$s1.units')
        second['where']['units']='$s3.units'
        with self.assertRaises(RuntimeError):c.decode(w)

    def test_invalid_stage_spelling_is_rejected_by_the_wire_schema(self):
        from jsonschema.exceptions import ValidationError

        for invalid in ('$s1', '$bad-name.units', '$s1.date'):
            w = wire('stages')
            second = copy.deepcopy(w['plan']['stages'][0])
            second['name'] = 's2'
            second['where']['units'] = invalid
            w['plan']['stages'].append(second)
            with self.subTest(reference=invalid), self.assertRaises(ValidationError):
                c.decode(w)

    def test_sort_requires_native_pipe_fields(self):
        from jsonschema.exceptions import ValidationError

        w = wire()
        entry = w['plan']['queries'][0]
        entry['pipe'] = [{'sort': 't'}]
        with self.assertRaises(ValidationError):
            c.decode(w)
        entry['pipe'] = [{'op': 'sort', 'key': 't', 'desc': False}]
        self.assertEqual(c.decode(w)['queries'][0]['pipe'], entry['pipe'])

    def test_unknown_keys_rejected(self):
        w=wire();w['plan']['queries'][0]['where']['answer']='x'
        with self.assertRaises(Exception):c.decode(w)

    def test_stop_only_on_feedback(self):
        with self.assertRaises(Exception):c.decode({'plan':{'stop':True}})
        self.assertEqual(c.decode({'plan':{'stop':True}},allow_stop=True),{'stop':True})

    def test_one_call_with_structured_format(self):
        with patch('memoket_kite.pipeline.compile_plan.llm_json',return_value=wire()) as llm:
            _compile_uncached('question','gpt-6-luna',contract=c)
        self.assertEqual(llm.call_count,1)
        self.assertEqual(llm.call_args.kwargs['retries'],0)
        self.assertTrue(llm.call_args.kwargs['response_format']['json_schema']['strict'])

    def test_default_caller_unchanged(self):
        raw={'queries':[{'where':{'grep':'anchor'}}]}
        with patch('memoket_kite.pipeline.compile_plan.llm_json',return_value=raw) as llm:
            self.assertEqual(_compile_uncached('question','gpt-4o-mini'),raw)
        self.assertEqual(llm.call_args.kwargs,dict(model='gpt-4o-mini',temperature=0.0))

if __name__=='__main__':unittest.main()
