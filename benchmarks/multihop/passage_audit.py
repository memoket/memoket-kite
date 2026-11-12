"""Offline final verification; never imported by inference."""
import argparse,hashlib,json,sys
from collections import Counter
from pathlib import Path
from benchmarks.multihop import protocol as p

def main(root):
    sys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError('offline audit')) if event in {'socket.connect','socket.getaddrinfo'} else None)
    freeze=p.read(root/'qa/freeze.json');assert freeze['complete']
    assert p.sha(root/'qa/predictions.jsonl')==freeze['predictions_sha256']
    rows=p.records(root/'qa/predictions.jsonl');manifest=p.read(root/'index/manifest.json');sources={s['id']:s for s in manifest['sources']}
    bank=p.read(root/'index/verification.json');inspection=p.read(root/'index/inspection.json')
    response_docs=Path(manifest.get('response_documents',root/'index/documents'))
    admitted={r['id'] for r in inspection};assert len(admitted)==bank['documents']
    for r in inspection:
        assert r['source']==sources[r['id']]
        raw_path=response_docs/r['id']/'response.txt'
        try: raw_index=p.read(raw_path) if raw_path.exists() else None
        except json.JSONDecodeError: raw_index=None
        assert r['raw_index']==raw_index
        assert len(r['delivery'])==1 and r['delivery'][0]['text']==r['source']['title']+'\n'+r['source']['text']
    physical=Counter();models=Counter();qaids=set();fail=[];empty=0
    for r in rows:
        dest=root/'qa/individual'/r['id'];logs=p.records(dest/'calls.jsonl');requests={x['call_id']:x['request'] for x in logs if x['event']=='request'}
        assert not qaids.intersection(requests);qaids.update(requests)
        calls=Counter(req['model'] for req in requests.values());assert calls['gpt-6-luna']<=2 and calls['gpt-4o-mini']<=1
        physical.update(calls)
        for log in logs:
            if log['event']=='response':
                response=log['response'];models[response.get('model','unknown')]+=1
        if r['status']=='ok':
            evidence=p.read(dest/'evidence.json');assert len(evidence)==len({x['unit'] for x in evidence})
            for x in evidence:
                s=sources[x['unit']];assert x['unit'] in admitted and x['text']==s['title']+'\n'+s['text']
            raw=p.read(dest/'answer_response.txt');assert raw['answer']==r['answer']
            receipt=next(x['response'] for x in logs if x['event']=='response' and requests[x['call_id']]['model']=='gpt-4o-mini')
            assert json.loads(receipt['choices'][0]['message']['content'])==raw
            if r['answer'].strip().lower()=='no information':empty+=1
        else:fail.append({k:r.get(k) for k in ['id','ordinal','error_stage','error_type','error']})
    refs=p.read(root/'SCORING_CONTRACT.json');labels={r['id']:r for r in p.records(refs['references'])}
    indexed_titles={sources[s]['title'] for s in admitted}
    title_coverage=[]
    for r in rows:
        expected=set(labels[r['id']].get('support_titles',[]));got={sources[s]['title'] for s in r.get('evidence_units',[])}
        title_coverage.append({'id':r['id'],'all_support_titles_indexed':bool(expected) and expected<=indexed_titles,'all_support_titles_delivered':bool(expected) and expected<=got})
    out={'n':len(rows),'actual_requests':dict(physical),'actual_response_models':dict(models),
         'compile_call_distribution':dict(Counter(r['physical_call_attempts'].get('gpt-6-luna',0) for r in rows)),
         'staged_final_plan_count':sum(bool(r.get('plan',{}).get('stages')) for r in rows),
         'whole_originals_verified':len(inspection),'full_qa_source_delivery_and_unmodified_answers_verified':True,
         'no_information_answers':empty,'execution_failures':fail,
         'support_title_coverage_only_not_relation_completeness':{
           'all_indexed':sum(r['all_support_titles_indexed'] for r in title_coverage),
           'all_delivered':sum(r['all_support_titles_delivered'] for r in title_coverage)},
         'title_coverage_cases':title_coverage}
    p.dump(root/'VERIFICATION_OFFLINE.json',out)
    print({k:v for k,v in out.items() if not isinstance(v,list)})
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);main(a.parse_args().root)
