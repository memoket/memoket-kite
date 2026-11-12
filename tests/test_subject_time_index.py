"""General regressions for native precision and opt-in subject identity lookup."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from memoket_kite.core.algebra import Store,Trace,_valid_event_time,_time_ok,_match_facts,parse_plan
from memoket_kite.core.vocab import Vocab

class SubjectTimeTests(unittest.TestCase):
    def test_precision_and_invalid_dates(self):
        for text in ['0001','1784','2024-02','2024-02-29']:
            self.assertTrue(_valid_event_time(text),text)
        for text in ['0000','178','1784-00','1784-13','2023-02-29','2024-02-30','2024-01-01T00:00:00']:
            self.assertFalse(_valid_event_time(text),text)
        self.assertTrue(_time_ok('1784','1784','1784'))
        self.assertFalse(_time_ok('1784','1785','1785'))
        # Coarse dates denote overlap, not an invented January date.
        self.assertTrue(_time_ok('1784','1784-09','1784-10'))
        self.assertFalse(_time_ok('1784-03','1784-09','1784-10'))

    def make_store(self,speakers=''):
        vocab=Vocab();vocab.register_entity('Cedar Quinn','person');vocab.alias_entity('C. Quinn','cedar_quinn')
        vocab.register_entity('Rowan Quinn','person')
        root=ET.Element('codebook',speakers=speakers);root.append(vocab.to_xml());tl=ET.SubElement(root,'timeline')
        u=ET.SubElement(tl,'session',id='u',date='2000-01-01')
        ET.SubElement(u,'line',id='uL1',who='source').text='Cedar Quinn was born in 1784; Rowan Quinn was born in 1785.'
        for ident,who,t,ents in [('a','cedar quinn','1784','cedar_quinn'),('b','rowan quinn','1785','cedar_quinn rowan_quinn')]:
            ET.SubElement(u,'fact',id=ident,who=who,t=t,kind='event',event='birth',src='uL1',entities=ents).text=who+' birth'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bank.xml';ET.ElementTree(root).write(p);s,v=Store.load([str(p)])
        return s,v

    def match(self,s,v,who):
        sub=parse_plan({'queries':[{'select':'facts','where':{'who':[who],'kind':['event']}}]},v,s.speakers)[0]
        return _match_facts(s,v,sub.where,Trace(),legal_kinds={'event'})

    def test_registered_alias_only_default_unchanged(self):
        s,v=self.make_store()
        self.assertEqual(self.match(s,v,'C. Quinn'),set())
        self.assertEqual(self.match(s,v,'Cedar Quinn'),{'a'})
        s.resolve_subject_aliases=True
        self.assertEqual(self.match(s,v,'C. Quinn'),{'a'})
        self.assertEqual(self.match(s,v,'cedar_quinn'),{'a'})
        self.assertEqual(self.match(s,v,'Rowan Quinn'),{'b'})
        self.assertEqual(self.match(s,v,'Quinn'),set())
        self.assertEqual(s.facts['a'].t,'1784')
        self.assertEqual(s.facts['a'].who,'cedar quinn')
        # Row b mentions Cedar, but its subject is Rowan: no false subject join.
        self.assertNotIn('b',self.match(s,v,'C. Quinn'))

    def test_speaker_name_does_not_follow_entity_alias(self):
        s,v=self.make_store('c._quinn');s.resolve_subject_aliases=True
        self.assertEqual(self.match(s,v,'c._quinn'),set())

if __name__=='__main__':unittest.main()

class SharedFieldSemanticsTests(unittest.TestCase):
    @staticmethod
    def ref(source,name,last=False):
        from benchmarks.multihop.passage_subject_time_pilot import name_references
        refs=name_references(source,name)
        return refs[-1] if last else refs[0]

    def payload(self,source,kind='event',events=()):
        from benchmarks.multihop.passage_subject_time_pilot import source_spans
        from benchmarks.multihop.field_semantics import EVENT_MEANINGS
        body=[x for x in source_spans(source) if x['segment']=='TEXT']
        mention=body[0]['id']+':'+body[0]['id'];name=self.ref(source,source['title'])
        fact={'src':mention,'who':{'mention':mention,'entity':name},'entities':[],
              'kind':kind,'event':[EVENT_MEANINGS[e] for e in events],'t':'2041','conf':'high'}
        return {'entities':[{'name':name,'type':'person','aliases':[]}],
                'relationships':[fact] if kind=='relationship' else [],'facts':[] if kind=='relationship' else [fact]}

    def test_extractor_and_compiler_share_definition(self):
        from benchmarks.multihop import profile,passage_subject_time_pilot as pilot
        from benchmarks.multihop.field_semantics import FIELD_SEMANTICS,COMPILE_FIELD_RULES,compiler_profile
        self.assertIn(FIELD_SEMANTICS,pilot.PROMPT);self.assertIn(FIELD_SEMANTICS,profile.EXTRACT_PROMPT)
        bound=compiler_profile();self.assertIn(FIELD_SEMANTICS,bound.COMPILE_PROMPT)
        self.assertIn(COMPILE_FIELD_RULES,bound.COMPILE_PROMPT)
        self.assertNotIn('Stored who and t are empty',bound.COMPILE_PROMPT)
        self.assertNotIn('{field_use_placeholder}',bound.COMPILE_PROMPT)
        self.assertEqual(bound.COMPILE_CANDIDATES,1)
        self.assertNotIn('maxItems',pilot.SCHEMA['properties']['facts'])
        self.assertNotIn('maxItems',pilot.SCHEMA['properties']['relationships'])

    def test_kind_and_event_are_independent(self):
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar plans to move in 2041.'}
        validate(self.payload(source,'plan',['move']),source)

    def test_inconsistent_event_kind_rejected_without_repair(self):
        import copy,jsonschema
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar was born in 2041.'}
        data=self.payload(source,'identity',['birth']);before=copy.deepcopy(data)
        with self.assertRaises(jsonschema.ValidationError):validate(data,source)
        self.assertEqual(data,before);validate(self.payload(source,'event',['birth']),source)

    def test_declared_alias_keeps_source_identity(self):
        import copy
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar Quinn','text':'Cedar Quinn, also called Cedar, moved in 2041.'}
        data=self.payload(source,'event',['move']);alias=self.ref(source,'Cedar',True)
        data['entities'][0]['aliases']=[alias];data['facts'][0]['who']['entity']=alias
        before=copy.deepcopy(data);validate(data,source);self.assertEqual(data,before)
        data['facts'][0]['who']['mention']='w999:w999'
        with self.assertRaisesRegex(ValueError,'outside'):validate(data,source)

    def test_reference_transport_preserves_source_and_semantics(self):
        import copy
        from benchmarks.multihop.passage_subject_time_pilot import validate,decode_index
        source={'title':'Cedar','text':'Cedar wrote poems in 2041. A book followed.'}
        data=self.payload(source,'identity');data['facts'][0]['src']=self.ref(source,'Cedar wrote poems in 2041')
        before=copy.deepcopy(data);validate(data,source)
        native=decode_index(data,source,{'topics':[],'facets':{'object':[],'place':[],'event':[]}})
        self.assertEqual(data,before);self.assertEqual(native['facts'][0]['content'],'Cedar wrote poems in 2041')
        self.assertEqual(native['facts'][0]['who'],'Cedar')
        for key in ['kind','t','conf']:self.assertEqual(native['facts'][0][key],data['facts'][0][key])
        data['facts'][0]['src']='w4:w1'
        with self.assertRaisesRegex(ValueError,'contiguous'):validate(data,source)
        data['facts'][0]['src']='w0:w999'
        with self.assertRaisesRegex(ValueError,'outside'):validate(data,source)

    def test_uniform_refs_cannot_use_title_shortcut_or_cite_title_as_fact(self):
        import jsonschema
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar wrote in 2041.'};data=self.payload(source)
        data['entities'][0]['name']='T'
        with self.assertRaises(jsonschema.ValidationError):validate(data,source)
        data=self.payload(source);data['facts'][0]['src']=self.ref(source,'Cedar')
        with self.assertRaisesRegex(ValueError,'title'):validate(data,source)
        data=self.payload(source);data['facts'][0]['src']='w0:w1'
        with self.assertRaisesRegex(ValueError,'boundary'):validate(data,source)

    def test_name_reference_cannot_rewrite_source_spelling(self):
        import jsonschema
        from benchmarks.multihop.passage_subject_time_pilot import validate,decode_index,entity_hints
        source={'title':'Cedar','text':'Cedar visited Public Libraries in 2041.'}
        data=self.payload(source);ref=self.ref(source,'Public Libraries')
        data['entities'].append({'name':ref,'type':'org','aliases':[]});data['facts'][0]['entities']=[ref]
        validate(data,source)
        native=decode_index(data,source,{'topics':[],'facets':{'object':[],'place':[],'event':[]}})
        self.assertEqual(native['facts'][0]['entities'],['Public Libraries'])
        hints=entity_hints(source,{'entities':[{'name':'Public Libraries','type':'org'},{'name':'Public Library','type':'org'}]})
        self.assertEqual(hints[0]['source_refs'],[ref]);self.assertEqual(hints[1]['source_refs'],[])
        data['entities'][1]['name']='Public Library'
        with self.assertRaises(jsonschema.ValidationError):validate(data,source)

    def test_participant_is_declared_and_does_not_become_subject(self):
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar met Rowan in 2041.'};data=self.payload(source)
        ref=self.ref(source,'Rowan');data['entities'].append({'name':ref,'type':'person','aliases':[]})
        data['facts'][0]['entities']=[ref];validate(data,source)
        self.assertEqual(data['facts'][0]['who']['entity'],self.ref(source,'Cedar'))
        data['facts'][0]['entities']=[self.ref(source,'2041')]
        with self.assertRaisesRegex(ValueError,'participant'):validate(data,source)

    def test_year_cannot_be_calculated_from_unanchored_relative_time(self):
        import jsonschema
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar is a 2041 novel about a journey six years earlier.'}
        data=self.payload(source,'event',['move']);data['facts'][0]['t']='2035'
        with self.assertRaises(jsonschema.ValidationError):validate(data,source)
        data['facts'][0]['t']=None;validate(data,source)

    def test_native_posting_uses_explicit_subject_and_participants_only(self):
        import copy
        from benchmarks.multihop.passage_subject_time_pilot import native_fact_attributes
        v=Vocab();code=v.register_entity('Cedar Quinn','person');other=v.register_entity('Rowan Quinn','person')
        f={'who':'Cedar Quinn','entities':['Rowan Quinn'],'kind':'relationship','t':None,'event':[],'conf':'high','content':'Cedar Quinn met Rowan Quinn.'}
        before=copy.deepcopy(f);attr=native_fact_attributes('doc',2,f,v);self.assertEqual(f,before)
        self.assertEqual(attr['entities'].split(),[code,other]);self.assertEqual(attr['who'],'Cedar Quinn')
        f['who']='unnamed subject';f['entities']=[]
        self.assertEqual(native_fact_attributes('doc',2,f,v)['entities'],'')
        f['entities']=['Missing']
        with self.assertRaisesRegex(ValueError,'participant'):native_fact_attributes('doc',2,f,v)

    def test_repeated_name_addresses_are_same_literal_identity(self):
        from benchmarks.multihop.passage_subject_time_pilot import validate,decode_index
        source={'title':'Cedar','text':'Cedar met Rowan in 2041. Cedar left.'};data=self.payload(source)
        ref=self.ref(source,'Cedar',True);data['entities'][0]['aliases']=[ref];data['facts'][0]['who']['entity']=ref
        validate(data,source);native=decode_index(data,source,{'topics':[],'facets':{'object':[],'place':[],'event':[]}})
        self.assertEqual(native['aliases'],[]);self.assertEqual(native['facts'][0]['who'],'Cedar')

    def test_every_event_choice_maps_without_semantic_repair(self):
        from benchmarks.multihop.passage_subject_time_pilot import validate,decode_index
        from benchmarks.multihop.field_semantics import EVENT_MEANINGS
        for code in EVENT_MEANINGS:
            source={'title':'Cedar','text':'Cedar recorded an event in 2041.'};data=self.payload(source,'event',[code]);validate(data,source)
            native=decode_index(data,source,{'topics':[],'facets':{'object':[],'place':[],'event':[]}})
            self.assertEqual(native['facts'][0]['event'],[code])

    def test_distinct_event_times_require_distinct_records(self):
        import jsonschema,copy
        from benchmarks.multihop.passage_subject_time_pilot import validate
        source={'title':'Cedar','text':'Cedar (2041–2099).'};data=self.payload(source,'event',['birth','death'])
        with self.assertRaises(jsonschema.ValidationError):validate(data,source)
        data=self.payload(source,'event',['birth']);death=copy.deepcopy(data['facts'][0]);death['event']=['a person or animal dying'];death['t']='2099'
        data['facts'].append(death);validate(data,source)

    def test_local_binding_is_model_selected_not_passage_default(self):
        from benchmarks.multihop.passage_subject_time_pilot import validate,decode_index
        source={'title':'Cedar','text':'Cedar. Another version named Rowan appeared in 2041.'};data=self.payload(source,'event',['release'])
        data['entities'][0]['type']='work';ref=self.ref(source,'Rowan')
        data['entities'].append({'name':ref,'type':'work','aliases':[]})
        data['facts'][0]['who']={'mention':self.ref(source,'Another version named Rowan'),'entity':ref}
        validate(data,source);native=decode_index(data,source,{'topics':[],'facets':{'object':[],'place':[],'event':[]}})
        self.assertEqual(native['facts'][0]['who'],'Rowan');self.assertEqual(native['facts'][0]['t'],'2041')

    def test_source_audits_forbidden_during_extraction(self):
        from benchmarks.multihop.passage_index import labels_guard
        with self.assertRaises(RuntimeError):labels_guard('open',('SOURCE_ASSERTIONS_OFFLINE.json',))
