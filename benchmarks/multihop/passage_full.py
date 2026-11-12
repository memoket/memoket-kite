"""Full-corpus single-pass indexing. No question, reference or answer inputs."""
import argparse
import concurrent.futures
import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import sys
from collections import Counter
from benchmarks.multihop import adapter, passage_index as ix, protocol as p


def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def build(args, *, prompt=ix.PROMPT, response_format=ix.FORMAT, project_metadata=None,
          admit_metadata=None):
    sys.addaudithook(ix.labels_guard)
    schema = response_format['json_schema']['schema']
    if args.reuse and (prompt != ix.PROMPT or schema != ix.SCHEMA
                       or project_metadata is not None or admit_metadata is not None):
        raise ValueError('This index policy requires fresh source-only metadata')
    corpus=p.read(args.corpus)
    sources=[adapter.document(x['title'],x['text']) for x in corpus]
    assert len(sources)==len({s['id'] for s in sources})
    sources.sort(key=lambda d:hashlib.sha256(('passage-index-v1\0'+d['id']).encode()).hexdigest())
    reused=p.read(args.reuse/'manifest.json')['sources'] if args.reuse else []
    byid={s['id']:s for s in sources}
    assert all(byid[s['id']]==s for s in reused)
    sources=reused+[s for s in sources if s['id'] not in {d['id'] for d in reused}]
    assert not args.output.exists(),'Output exists; inspect state before any resume'
    args.output.mkdir(parents=True)
    lock=(args.output/'RUN.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    p.dump(args.output/'START.json',{'pid':os.getpid(),'utc':stamp()})
    (args.output/'documents').mkdir()
    p.dump(args.output/'manifest.json',{'sources':sources,'corpus_sha256':p.sha(args.corpus),
        'count':len(sources),'scope':'complete supplied shared corpus; exposed development QA separately',
        'model':'gpt-6-luna','reasoning_effort':'none','temperature':0,'max_new_calls_per_document':1,
        'schema':schema,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'implementation_sha256':p.sha(ix.__file__),'runner_sha256':p.sha(__file__),
        'reuse':str(args.reuse) if args.reuse else None,'reused_responses':len(reused),
        'failed_metadata':'record and omit metadata; always store original; never auto repair or retry',
        'alias_conflicts':('omit conflicting bindings before registry mutations; always store original'
                           if admit_metadata else
                           'reject conflicting metadata before registry mutations; always store original'),
        'vectors':False,'semantic_repair':False,
        'field_admission':project_metadata is not None,'QA_or_labels_read':False})
    (args.output/'prompt_template.txt').write_text(prompt);p.dump(args.output/'schema.json',schema)
    results={}
    for source in reused:
        src=args.reuse/'documents'/source['id'];dst=args.output/'documents'/source['id']
        expected=ix.PROMPT.format(roots=', '.join(ix.profile.SEED_ROOTS),events=', '.join(ix.profile.EVENTS),source=__import__('json').dumps({'title':source['title'],'text':source['text']},ensure_ascii=False))
        assert (src/'prompt.txt').read_text()==expected
        assert p.read(args.reuse/'schema.json')==ix.SCHEMA
        shutil.copytree(src,dst)
        r={'id':source['id'],'status':'error','usage':p.usage(src/'calls.jsonl'),'reused':True}
        try:
            r['raw_index']=p.read(src/'response.txt')
            r.update(index=ix.validate(r['raw_index'],source),status='ok')
        except Exception as e:r.update(error_type=type(e).__name__,error=str(e))
        p.dump(dst/'result.json',r);results[source['id']]=r
    def progress(phase):
        p.dump(args.output/'STATE.json',{'phase':phase,'pid':os.getpid(),'utc':stamp(),'planned':len(sources),
           'completed':len(results),'statuses':dict(Counter(x['status'] for x in results.values())),
           'new_HTTP_calls_recorded':sum(x['usage']['HTTP_calls'] for x in results.values() if not x.get('reused')),
           'new_tokens_recorded':sum(x['usage']['tokens']['total_tokens'] for x in results.values() if not x.get('reused')),
           'unknown_usage_completed':sum(x['usage']['unknown_usage'] for x in results.values()),
           'reused':len(reused),'in_flight_usage_not_yet_in_totals':phase=='indexing'})
    progress('indexing')
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(ix.worker,(s,str(args.output),str(args.env_file)),
                             prompt_template=prompt, response_format=response_format,
                             project_metadata=project_metadata)
                 for s in sources if s['id'] not in results]
        for future in concurrent.futures.as_completed(futures):
            r=future.result();results[r['id']]=r;progress('indexing')
            if len(results)%50==0:print(len(results),len(sources),flush=True)
    progress('assembling')
    verification=ix.assemble(sources,results,args.output,scoped_probes=True,admit_metadata=admit_metadata)
    if project_metadata is not None:
        verification['field_rejections'] = [
            {'id': r['id'], 'fields': r['field_rejections']}
            for r in results.values() if r.get('field_rejections')
        ]
    verification.update(attempted_sources=len(sources),index_validation_failures=[{'id':r['id'],'error':r['error']} for r in results.values() if r['status']!='ok'],
        reuse_count=len(reused),new_HTTP_calls=sum(x['usage']['HTTP_calls'] for x in results.values() if not x.get('reused')),
        usage_all_attempts={'HTTP_calls':sum(x['usage']['HTTP_calls'] for x in results.values()),
            'tokens':sum(x['usage']['tokens']['total_tokens'] for x in results.values()),
            'unknown':sum(x['usage']['unknown_usage'] for x in results.values())})
    p.dump(args.output/'verification.json',verification)
    p.dump(args.output/'freeze.json',{'complete':True,'utc':stamp(),'index_sha256':p.sha(args.output/'index.xml'),
        'all_sources_attempted':True,'documents_stored':verification['documents'],'documents_indexed':verification['documents_with_index'],'source_count':len(sources)})
    progress('index_frozen_ready_for_evaluation')

def main(**policy):
    a=argparse.ArgumentParser();a.add_argument('--corpus',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    a.add_argument('--reuse',type=Path);a.add_argument('--env-file',type=Path,required=True);a.add_argument('--workers',type=int,default=16)
    args=a.parse_args();assert 1<=args.workers<=32;build(args, **policy)

if __name__=='__main__':
    main()
