"""Frozen native Compile/retrieval over passage indexes, one unchanged reader.

No reference labels or historical plans/answers are readable by this process.
"""
import argparse
import concurrent.futures
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from collections import Counter
from dataclasses import replace
from benchmarks.multihop import profile, protocol as p
from benchmarks.multihop.passage_index import load_index, render_sources
from memoket_kite.pipeline import retrieve as rt
from memoket_kite.providers import llm as provider

CONTRACT_NOTE='''INDEX STORAGE: Each indexed fact is one intact source passage, not an atomic assertion.
Document tags describe passage contents. Stored who and t are empty; kind is other.
Use original text to test relationships and properties. Index tags are not relation edges.
'''

def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0]))
        if path.name == 'predictions.jsonl' and (args[2] & os.O_ACCMODE) == os.O_WRONLY:
            return
        if (path.name in {'references.jsonl', 'scores.json', 'predictions.jsonl', 'id_aliases.jsonl',
                          *(ds + '.json' for ds in p.DATASETS)}
                or path.name.endswith('_OFFLINE.json')
                or 'SCORING_ONLY' in str(path) or 'error-audit' in path.parts):
            raise RuntimeError('Reference labels and diagnoses forbidden during inference')

def render(res,store,question,schema):
    sources=render_sources(res.rows,store)
    # Original source exactly once in native selected order. No fact/source
    # duplication, generated claim, ranking/filtering or semantic postprocessing.
    lines=['COMPILED QUERY STRUCTURE (retrieval plan, not factual assertions):',json.dumps(res.plan,ensure_ascii=False)]
    for source in sources:lines.append('['+source['id']+']\n'+source['text'])
    allowed=[s['id'] for s in sources]
    fmt=copy.deepcopy(schema);props=fmt['json_schema']['schema']['properties']
    for arr in [props['evidence'],props['observations']['items']['properties']['evidence']]:
        arr['items']={'type':'string',**({'enum':allowed} if allowed else {})};arr['maxItems']=len(allowed)
    return profile.ANSWER_PROMPT.format(question=question,evidence='\n\n'.join(lines)),fmt,sources


def render_distinct_passages(res, store, question, schema):
    """Recover evidence slots spent twice on the same rendered passage."""
    if res.aggregates or profile.TOKEN_CAP:
        return render(res, store, question, schema)

    selected = {}
    for row in res.rows:
        selected.setdefault(row.get('unit') or row['id'], row)
    if len(selected) == len(res.rows):
        return render(res, store, question, schema)

    # Retain every originally delivered passage. Walk the existing fused
    # ranking and fill only the slots freed by duplicates in this pack.
    for row in res.candidate_rows:
        if len(selected) >= len(res.rows):
            break
        uid = row.get('unit') or row['id']
        if uid in store.lines_by_unit:
            selected.setdefault(uid, row)
    return render(replace(res, rows=list(selected.values())), store, question, schema)


def init_worker(root,envfile,*,configure_profile=None,render_answer=render):
    global ROOT,STORE,VOCAB,SCHEMA,REAL_HTTP,COUNTS,RENDER_ANSWER
    sys.addaudithook(guard);ROOT=Path(root);p.configure(envfile)
    RENDER_ANSWER = render_answer
    c=p.read(ROOT/'QA_CONTRACT.json')
    profile.COMPILE_REVIEW_SUBJECT_SCOPE = c.get('compile_review_subject_scope', False)
    if c.get('structured_compile_contract', profile.COMPILE_REVIEW_SUBJECT_SCOPE):
        from benchmarks.multihop import compile_contract
        profile.COMPILE_CONTRACT=compile_contract
    else:
        profile.COMPILE_CONTRACT=None
    if configure_profile is not None:
        configure_profile(profile)
    for path,digest in c['runtime_hashes'].items():assert p.sha(path)==digest,path
    freeze=p.read(ROOT/'index/freeze.json');assert freeze['complete']
    assert p.sha(ROOT/'index/index.xml')==freeze['index_sha256']
    STORE,VOCAB=load_index(ROOT/'index/index.xml')
    profile.COMPILE_PROMPT=CONTRACT_NOTE+profile.COMPILE_PROMPT
    profile.KINDS=('other',)
    assert p.sha(ROOT/'ANSWER_PROMPT.txt')==c['reader_prompt_sha256']
    assert p.sha(ROOT/'ANSWER_SCHEMA.json')==c['reader_schema_sha256']
    profile.ANSWER_PROMPT=(ROOT/'ANSWER_PROMPT.txt').read_text()
    SCHEMA=p.read(ROOT/'ANSWER_SCHEMA.json')
    assert profile.COMPILE_MAX_ROUNDS==2 and profile.COMPILE_CANDIDATES==1 and profile.DEFAULT_BUDGET==40
    assert hashlib.sha256(profile.COMPILE_PROMPT.encode()).hexdigest()==c['compile_prompt_sha256']
    REAL_HTTP=provider._http_llm;COUNTS={}
    def bounded(prompt,timeout,temperature=0.0,model='gpt-4.1-mini',*,response_format=None,reasoning_effort=None):
        assert model in {'gpt-6-luna','gpt-4o-mini'}
        assert COUNTS.get(model,0)<{'gpt-6-luna':2,'gpt-4o-mini':1}[model]
        if model=='gpt-6-luna':assert (reasoning_effort or 'medium')=='medium'
        else:assert temperature==0 and reasoning_effort is None
        COUNTS[model]=COUNTS.get(model,0)+1
        return REAL_HTTP(prompt,timeout,temperature,model,response_format=response_format,reasoning_effort=reasoning_effort)
    provider._http_llm=bounded


def worker(q):
    COUNTS.clear();dest=ROOT/'qa/individual'/q['id'];dest.mkdir(parents=True,exist_ok=False)
    os.environ.update(KITE_LLM_LOG=str(dest/'calls.jsonl'),KITE_LLM_ITEM=q['id'],KITE_LLM_PHASE='passage-full-qa')
    row=dict(q);stage='compile_retrieval'
    try:
        res=rt._run_retrieval(STORE,VOCAB,profile,q['question'],model='gpt-6-luna',budget=40)
        p.dump(dest/'retrieval.json',res.as_dict());stage='evidence_delivery'
        prompt,fmt,sources=RENDER_ANSWER(res,STORE,q['question'],SCHEMA)
        (dest/'answer_prompt.txt').write_text(prompt);p.dump(dest/'answer_schema.json',fmt);p.dump(dest/'evidence.json',sources)
        stage='answer';raw=provider._http_llm(prompt,180,temperature=0,model='gpt-4o-mini',response_format=fmt)
        (dest/'answer_response.txt').write_text(raw)
        data=json.loads(raw);assert set(data)=={'observations','comparison','evidence','answer'} and isinstance(data['answer'],str)
        row.update(status='ok',answer=data['answer'],response=data,evidence_units=[s['unit'] for s in sources],plan=res.plan)
    except Exception as e:row.update(status='error',answer=None,error_stage=stage,error_type=type(e).__name__,error=str(e))
    row.update(physical_call_attempts=dict(COUNTS),usage=p.usage(dest/'calls.jsonl'))
    p.dump(dest/'result.json',row);return row


def main(args, *, initializer=init_worker):
    sys.addaudithook(guard);root=args.root;c=p.read(root/'QA_CONTRACT.json')
    qs=p.read(root/'QUESTIONS.json')
    assert len(qs)==len({q['id'] for q in qs})==c['questions'] and all(set(q)=={'id','question','ordinal'} for q in qs)
    assert p.sha(root/'QUESTIONS.json')==c['questions_sha256'];assert not (root/'qa').exists()
    (root/'qa').mkdir();p.dump(root/'qa/START.json',{'pid':os.getpid(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    rows={}
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers,initializer=initializer,initargs=(str(root),str(args.env_file))) as pool:
        futures=[pool.submit(worker,q) for q in qs]
        for future in concurrent.futures.as_completed(futures):
            row=future.result();rows[row['id']]=row
            p.dump(root/'qa/STATE.json',{'phase':'evaluating','completed':len(rows),'planned':len(qs),
                'statuses':dict(Counter(r['status'] for r in rows.values())),
                'HTTP_calls_recorded':sum(r['usage']['HTTP_calls'] for r in rows.values()),
                'tokens_recorded':sum(r['usage']['tokens']['total_tokens'] for r in rows.values()),
                'unknown_completed':sum(r['usage']['unknown_usage'] for r in rows.values()),'in_flight_usage_not_in_totals':True})
            if len(rows)%25==0:print(len(rows),len(qs),flush=True)
    for path,digest in c['runtime_hashes'].items():assert p.sha(path)==digest,path
    assert p.sha(root/'index/index.xml')==p.read(root/'index/freeze.json')['index_sha256']
    predictions_hash = hashlib.sha256()
    with (root/'qa/predictions.jsonl').open('xb') as out:
        for q in qs:
            line = (json.dumps(rows[q['id']], ensure_ascii=False) + '\n').encode('utf-8')
            out.write(line)
            predictions_hash.update(line)
    p.dump(root/'qa/freeze.json',{'complete':True,'n':len(rows),'predictions_sha256':predictions_hash.hexdigest(),
        'HTTP_calls':sum(r['usage']['HTTP_calls'] for r in rows.values()),
        'tokens':sum(r['usage']['tokens']['total_tokens'] for r in rows.values()),
        'unknown_usage':sum(r['usage']['unknown_usage'] for r in rows.values())})

def cli(**options):
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);a.add_argument('--env-file',type=Path,required=True);a.add_argument('--workers',type=int,default=16)
    main(a.parse_args(), **options)

if __name__=='__main__':
    cli()
