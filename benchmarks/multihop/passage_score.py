"""Independent network-disabled EM/F1 after full prediction freeze."""
import argparse,os,sys
from pathlib import Path
from benchmarks.multihop import protocol as p

def main(root,refs):
    sys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError('offline score')) if event in {'socket.connect','socket.getaddrinfo'} else None)
    freeze=p.read(root/'qa/freeze.json');assert freeze['complete']
    assert p.sha(root/'qa/predictions.jsonl')==freeze['predictions_sha256']
    rows=p.records(root/'qa/predictions.jsonl');labels={x['id']:x for x in p.records(refs)}
    cases=[]
    for r in rows:
        score=p.answer_scores(r['answer'],labels[r['id']]['answers']) if r['status']=='ok' else {'em':0.0,'f1':0.0}
        cases.append({**r,**score,'gold':labels[r['id']]['answers']})
    n=len(cases);report={'n':n,'EM':100*sum(r['em'] for r in cases)/n,'F1':100*sum(r['f1'] for r in cases)/n,
        'execution_errors':sum(r['status']!='ok' for r in cases),'LLM_judge':False,'development_set':True,
        'references_sha256':p.sha(refs),'index':p.read(root/'index/verification.json'),'qa_usage':freeze,
        'cases':cases}
    p.dump(root/'SCORED_OFFLINE.json',report)
    print({k:report[k] for k in ['n','EM','F1','execution_errors','LLM_judge']},flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);a.add_argument('--references',type=Path,required=True);args=a.parse_args();main(args.root,args.references)
