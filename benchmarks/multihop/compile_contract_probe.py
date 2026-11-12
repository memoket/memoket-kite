"""One fresh Compile per frozen input. No Answer calls, no reference access."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
from benchmarks.multihop import compile_contract as contract, protocol as p
from benchmarks.multihop.passage_qa import guard
from memoket_kite.pipeline.compile_plan import _compile_uncached


def worker(root, case, env_file):
    sys.addaudithook(guard)
    p.configure(env_file)
    root=Path(root); dest=root/'outputs'/case['prompt_file'].removesuffix('.txt');dest.mkdir(exist_ok=False)
    text=(root/case['prompt_file']).read_text()
    assert hashlib.sha256(text.encode()).hexdigest()==case['prompt_sha256']
    os.environ.update(KITE_LLM_LOG=str(dest/'calls.jsonl'),KITE_LLM_ITEM=case['id'],KITE_LLM_PHASE='compile-contract-probe')
    row={k:v for k,v in case.items() if k!='prompt_sha256'}
    try:
        plan=_compile_uncached(text+contract.INSTRUCTION,'gpt-6-luna',allow_stop=case['allow_stop'],contract=contract)
        row.update(status='valid',plan=plan)
    except Exception as e:
        row.update(status='error',error_type=type(e).__name__,error=str(e))
    row['usage']=p.usage(dest/'calls.jsonl')
    assert row['usage']['HTTP_calls']<=1
    p.dump(dest/'result.json',row)
    return row


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--env-file',type=Path,required=True);ap.add_argument('--workers',type=int,default=5);a=ap.parse_args()
    assert a.env_file.is_file()
    sys.addaudithook(guard)
    cases=p.read(a.root/'INPUTS.json')
    assert 1<=len(cases)<=20
    (a.root/'outputs').mkdir(exist_ok=False)
    with concurrent.futures.ProcessPoolExecutor(max_workers=min(a.workers,5)) as pool:
        futures=[pool.submit(worker,str(a.root),c,str(a.env_file)) for c in cases]
        rows=[]
        for f in concurrent.futures.as_completed(futures):
            row=f.result();rows.append(row);p.dump(a.root/'RESULTS.json',rows);print(row['ordinal'],row['status'],flush=True)
    p.dump(a.root/'COMPLETE.json',{'n':len(rows),'valid':sum(x['status']=='valid' for x in rows),'HTTP_calls':sum(x['usage']['HTTP_calls'] for x in rows),'tokens':sum(x['usage']['tokens']['total_tokens'] for x in rows),'unknown':sum(x['usage']['unknown_usage'] for x in rows),'answer_calls':0})

if __name__=='__main__':main()
