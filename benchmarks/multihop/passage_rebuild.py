"""Offline source-persistence repair using frozen source-index receipts only.

No model calls, QA files, references, semantic metadata repair, or alias merge.
"""
import argparse
import datetime
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from benchmarks.multihop import passage_index as ix, protocol as p
from memoket_kite.core.algebra import SubQuery, Where, execute


def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo'}:
        raise RuntimeError('Rebuild must remain offline')
    ix.labels_guard(event,args)
    if event=='open' and isinstance(args[0],str) and Path(args[0]).name.endswith('_OFFLINE.json'):
        raise RuntimeError('No QA diagnoses in rebuilding')


def rebuild(source_run,out):
    sys.addaudithook(guard)
    freeze=p.read(source_run/'freeze.json')
    assert freeze['complete'] and p.sha(source_run/'index.xml')==freeze['index_sha256']
    manifest=p.read(source_run/'manifest.json');sources=manifest['sources']
    results={};hashes={}
    for source in sources:
        d=source_run/'documents'/source['id'];result=p.read(d/'result.json')
        assert p.read(d/'source.json')==source
        raw=d/'response.txt'
        if raw.exists():
            hashes[source['id']]=p.sha(raw)
            try:result['raw_index']=p.read(raw)
            except json.JSONDecodeError:result['raw_index']=None
        results[source['id']]=result
    out.mkdir(parents=True,exist_ok=False)
    for path in [Path(ix.__file__),Path(__file__)]:shutil.copy2(path,out/path.name)
    p.dump(out/'manifest.json',{'sources':sources,'corpus_sha256':manifest['corpus_sha256'],
        'origin_index':str(source_run.resolve()),'origin_index_sha256':freeze['index_sha256'],
        'response_documents':str((source_run/'documents').resolve()),'raw_response_sha256':hashes,
        'new_API_calls':0,'source_persistence':'every original; metadata admission policy unchanged',
        'index_prompt_and_model_output_unchanged':True,'QA_or_labels_read':False,
        'code_sha256':p.sha(ix.__file__),'rebuild_sha256':p.sha(__file__)})
    verification=ix.assemble(sources,results,out,scoped_probes=True)
    old,old_vocab=ix.load_index(source_run/'index.xml');new,new_vocab=ix.load_index(out/'index.xml')
    assert set(new.units)=={s['id'] for s in sources}
    assert ET.tostring(old_vocab.to_xml())==ET.tostring(new_vocab.to_xml()),'Existing registry changed'
    assert all(new.facts[fid]==fact for fid,fact in old.facts.items()),'Existing indexed records changed'
    assert all(new.lines[lid]==line for lid,line in old.lines.items()),'Existing source lines changed'
    restored=set(new.units)-set(old.units);lookups=[]
    for i,source in enumerate(s for s in sources if s['id'] in restored):
        sid=source['id'];fact=new.facts[sid+'F1']
        assert not fact.entities and not fact.topics
        for select,text in [('units',source['title']),('facts',source['text'])]:
            pattern='^'+re.escape(text)+'$'
            rows,_=execute(new,new_vocab,[SubQuery(select=select,where=Where(grep=pattern))],budget=len(new.units))
            assert any(row.get('unit',row['id'])==sid for row in rows)
            delivered=ix.render_sources(rows,new)
            own=next(d for d in delivered if d['unit']==sid)
            assert own['text']==source['title']+'\n'+source['text']
        lookups.append({'id':sid,'title_query':True,'body_query':True,'whole_original_delivered':True})
        if (i+1)%100==0:print('Verified recovered source queries',i+1,'/',len(restored),flush=True)
    assert p.sha(source_run/'index.xml')==freeze['index_sha256']
    for sid,digest in hashes.items():assert p.sha(source_run/'documents'/sid/'response.txt')==digest
    verification.update(new_API_calls=0,origin_index_unchanged=True,model_outputs_unchanged=True,
        existing_indexed_records_unchanged=len(old.facts),existing_vocabulary_unchanged=True,
        recovered_sources=len(restored),unscoped_title_and_body_queries_verified=len(lookups),
        source_text_and_scoped_delivery_checks=len(sources),
        note='Exact original-title/body queries verify availability, not QA retrieval recall or EM/F1.')
    p.dump(out/'verification.json',verification);p.dump(out/'recovered_queries.json',lookups)
    p.dump(out/'freeze.json',{'complete':True,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'index_sha256':p.sha(out/'index.xml'),'documents_stored':len(new.units),
        'documents_indexed':verification['documents_with_index'],'new_API_calls':0})
    print({k:v for k,v in verification.items() if not isinstance(v,list)},flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--source-run',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    args=a.parse_args();rebuild(args.source_run,args.output)
