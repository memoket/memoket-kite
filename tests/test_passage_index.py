import copy
import tempfile
import unittest
from pathlib import Path
from benchmarks.multihop.adapter import document
from benchmarks.multihop.passage_index import assemble,load_index,render_sources,validate
from memoket_kite.core.algebra import SubQuery,Where,execute

class PassageIndexTests(unittest.TestCase):
    def setUp(self):
        self.source=document('Example Film','Example Film is an Indian film with Actor One. Its later edition appeared in 2016.')
        self.meta={'entities':[{'name':'Example Film','type':'work'},{'name':'Actor One','type':'person'}],
                   'aliases':[], 'topics':[{'code':'film','parent':'arts'}],
                   'facets':{'object':['film'],'place':[],'event':[]}}
    def test_do_not_expand_nationality_or_substring(self):
        bad=copy.deepcopy(self.meta);bad['entities'].append({'name':'India','type':'place'})
        with self.assertRaises(ValueError):validate(bad,self.source)
    def test_unregistered_topic_not_repaired(self):
        bad=copy.deepcopy(self.meta);bad['topics']=[{'code':'film','parent':None}]
        with self.assertRaises(ValueError):validate(bad,self.source)
    def test_no_model_rewrite_field(self):
        bad=copy.deepcopy(self.meta);bad['content']='A rewrite'
        with self.assertRaises(Exception):validate(bad,self.source)
    def test_whole_passage_and_no_false_subject_or_actor_class(self):
        validate(self.meta,self.source)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);sid=self.source['id']
            assemble([self.source],{sid:{'status':'ok','index':self.meta}},out)
            store,vocab=load_index(out/'index.xml');f=next(iter(store.facts.values()))
            self.assertEqual(f.text,self.source['text']);self.assertFalse(f.who);self.assertFalse(f.t)
            self.assertFalse(store.obj_of)
            self.assertIn(f.id,store.by_obj['film'])
            rows,_=execute(store,vocab,[SubQuery(where=Where(entities=['actor_one']))])
            evidence=render_sources(rows+rows,store)
            self.assertEqual(len(evidence),1)
            self.assertEqual(evidence[0]['text'],self.source['title']+'\n'+self.source['text'])
    def test_model_alias_is_registered_and_retrievable(self):
        source=document('Example Film','Example Film, also known as Other Title, is a film.')
        meta=copy.deepcopy(self.meta)
        meta['entities']=[{'name':'Example Film','type':'work'},{'name':'Other Title','type':'work'}]
        meta['aliases']=[{'canonical':'Example Film','alias':'Other Title'}]
        validate(meta,source)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);assemble([source],{source['id']:{'status':'ok','index':meta}},out)
            store,vocab=load_index(out/'index.xml')
            self.assertEqual(vocab.resolve_entity('Other Title'),vocab.resolve_entity('Example Film'))
            self.assertEqual(len(store.facts),1)
    def test_missing_alias_endpoint_is_rejected(self):
        bad=copy.deepcopy(self.meta)
        bad['aliases']=[{'canonical':'Example Film','alias':'Unknown name'}]
        with self.assertRaises(ValueError):validate(bad,self.source)
    def test_ambiguous_surface_is_not_silently_merged(self):
        bad=copy.deepcopy(self.meta)
        bad['aliases']=[{'canonical':'Example Film','alias':'Actor One'}]
        with self.assertRaises(ValueError):validate(bad,self.source)
    def test_event_vocabulary_and_array_cap(self):
        bad=copy.deepcopy(self.meta)
        bad['facets']['event']=['unsupported_event']
        with self.assertRaises(Exception):validate(bad,self.source)
        bad=copy.deepcopy(self.meta);bad['facets']['object']=['a','b','c','d','e']
        with self.assertRaises(Exception):validate(bad,self.source)
    def test_native_spelling_validation_does_not_rewrite(self):
        source=document('A source','Sample- Place and Another –Place are named here.')
        meta={'entities':[{'name':'Sample-Place','type':'place'},{'name':'Another–Place','type':'place'}],
              'aliases':[],'topics':[{'code':'geography','parent':None}],
              'facets':{'object':[],'place':['Sample-Place'],'event':[]}}
        before=copy.deepcopy(meta);validate(meta,source);self.assertEqual(meta,before)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);assemble([source],{source['id']:{'status':'ok','index':meta}},out)
            store,vocab=load_index(out/'index.xml')
            self.assertEqual(next(iter(store.facts.values())).text,source['text'])
            self.assertEqual(vocab.resolve_entity('Sample- Place'),vocab.resolve_entity('Sample-Place'))
    def test_native_validation_is_not_fuzzy_or_transliteration(self):
        source=document('A source','New Place and René appear here.')
        for name in ['Place New','Old Place','Rene']:
            bad={'entities':[{'name':name,'type':'place'}],'aliases':[],
                 'topics':[{'code':'geography','parent':None}],
                 'facets':{'object':[],'place':[],'event':[]}}
            with self.assertRaises(ValueError):validate(bad,source)

class FullCorpusAdmissionTests(unittest.TestCase):
    def test_alias_conflict_preflight_does_not_mutate_registry(self):
        from benchmarks.multihop.passage_index import check_alias_conflicts
        from memoket_kite.core.vocab import Vocab
        vocab=Vocab();vocab.register_entity('Existing Name','person')
        metadata={'entities':[{'name':'Qualified Name','type':'person'},{'name':'Existing Name','type':'person'}],
                  'aliases':[{'canonical':'Qualified Name','alias':'Existing Name'}]}
        before=copy.deepcopy(vocab.__dict__)
        with self.assertRaises(ValueError):check_alias_conflicts(vocab,metadata)
        self.assertEqual(vocab.__dict__,before)

    def test_whole_passage_render_deduplicates_fact_and_source_hits(self):
        from types import SimpleNamespace
        from benchmarks.multihop.passage_qa import render
        from benchmarks.multihop import profile
        source=document('Whole source','Whole source names One and Two; One is linked to Two.')
        metadata={'entities':[{'name':'One','type':'person'},{'name':'Two','type':'person'}],
                  'aliases':[],'topics':[{'code':'people','parent':None}],
                  'facets':{'object':[],'place':[],'event':[]}}
        schema={'json_schema':{'schema':{'properties':{'evidence':{},'observations':{'items':{'properties':{'evidence':{}}}}}}}}
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);assemble([source],{source['id']:{'status':'ok','index':metadata}},out)
            store,_=load_index(out/'index.xml');sid=source['id']
            res=SimpleNamespace(plan={'queries':[]},rows=[{'type':'fact','unit':sid,'id':sid+'F1'},{'type':'line','unit':sid,'id':sid+'L1'}])
            prompt,fmt,sources=render(res,store,'Which entity?',schema)
            self.assertEqual(len(sources),1);self.assertEqual(prompt.count(source['text']),1)
            self.assertEqual(fmt['json_schema']['schema']['properties']['evidence']['items']['enum'],[sid+'L1'])

class SourcePersistenceTests(unittest.TestCase):
    def assert_native_text_lookup(self, store, vocab, source):
        import re
        sid=source['id']
        for select,pattern in [('units','^'+re.escape(source['title'])+'$'),
                               ('facts','^'+re.escape(source['text'])+'$')]:
            rows,_=execute(store,vocab,[SubQuery(select=select,where=Where(grep=pattern))],budget=len(store.units)+1)
            own=[d for d in render_sources(rows,store) if d['unit']==sid]
            self.assertEqual(len(own),1)
            self.assertEqual(own[0]['text'],source['title']+'\n'+source['text'])

    def test_bad_topic_keeps_original_searchable_without_repair(self):
        import json
        source=document('Original Topic','Original Topic mentions Named Person and a complete relationship.')
        meta={'entities':[{'name':'Named Person','type':'person'}],'aliases':[],
              'topics':[{'code':'people','parent':'history'}],
              'facets':{'object':['person'],'place':[],'event':[]}}
        with self.assertRaises(ValueError):validate(meta,source)
        result={'status':'error','raw_index':copy.deepcopy(meta),'error':'root must have null parent'}
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);v=assemble([source],{source['id']:result},out,scoped_probes=True)
            store,vocab=load_index(out/'index.xml')
            self.assert_native_text_lookup(store,vocab,source)
            fact=store.facts[source['id']+'F1'];self.assertFalse(fact.entities);self.assertFalse(fact.topics)
            self.assertNotIn('named_person',vocab.entities)
            self.assertEqual(v['documents'],1);self.assertEqual(v['documents_with_index'],0)
            self.assertEqual(v['raw_only_documents'],1);self.assertEqual(v['source_rejections'],[])
            audit=json.loads((out/'inspection.json').read_text())[0]
            self.assertEqual(audit['raw_index'],meta);self.assertEqual(result['raw_index'],meta)

    def test_missing_or_malformed_response_cannot_discard_source(self):
        for error in ['invalid JSON','HTTP transport failure','empty response']:
            with self.subTest(error=error),tempfile.TemporaryDirectory() as d:
                source=document('Unindexed Source','Unindexed Source still contains the original evidence.')
                out=Path(d);assemble([source],{source['id']:{'status':'error','error':error}},out)
                store,vocab=load_index(out/'index.xml');self.assert_native_text_lookup(store,vocab,source)
                self.assertFalse(vocab.entities)

    def test_conflicting_alias_preserves_source_and_existing_index(self):
        import json,xml.etree.ElementTree as ET
        a=document('Earlier Source','Existing Name appears here.')
        b=document('Later Source','Qualified Name, also called Existing Name, appears in this later source.')
        ma={'entities':[{'name':'Existing Name','type':'person'}],'aliases':[],
            'topics':[{'code':'people','parent':None}], 'facets':{'object':['person'],'place':[],'event':[]}}
        mb=copy.deepcopy(ma);mb['entities'].append({'name':'Qualified Name','type':'person'})
        mb['aliases']=[{'canonical':'Qualified Name','alias':'Existing Name'}]
        validate(ma,a);validate(mb,b)
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'old';new=Path(d)/'new';old.mkdir();new.mkdir()
            assemble([a],{a['id']:{'status':'ok','index':ma}},old)
            v=assemble([a,b],{a['id']:{'status':'ok','index':ma},b['id']:{'status':'ok','index':mb}},new)
            before,bv=load_index(old/'index.xml');after,av=load_index(new/'index.xml')
            self.assertEqual(before.facts[a['id']+'F1'],after.facts[a['id']+'F1'])
            self.assertEqual(ET.tostring(bv.to_xml()),ET.tostring(av.to_xml()))
            self.assertEqual(v['documents'],2);self.assertEqual(v['documents_with_index'],1)
            self.assertEqual(v['raw_only_documents'],1);self.assertEqual(v['metadata_rejections'][0]['stage'],'alias_registration')
            self.assert_native_text_lookup(after,av,b)
            self.assertEqual(av.resolve_entity('Existing Name'),'existing_name')
            self.assertIsNone(av.resolve_entity('Qualified Name'))
            audit=json.loads((new/'inspection.json').read_text())[1]
            self.assertEqual(audit['raw_index'],mb);self.assertEqual(audit['metadata_status'],'raw_only')

if __name__=='__main__':unittest.main()
