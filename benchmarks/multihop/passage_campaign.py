"""One-shot local job: wait for index freeze, run all QA, then offline score."""
import argparse,datetime,fcntl,json,os,subprocess,sys,time
from pathlib import Path

def read(path):return json.loads(path.read_text())
def dump(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)
def main(a):
    r=a.root;lock=(r/'campaign.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not (r/'CAMPAIGN_START.json').exists(),'Do not repeat campaign'
    dump(r/'CAMPAIGN_START.json',{'pid':os.getpid(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    def state(phase,**kw):dump(r/'CAMPAIGN_STATE.json',{'phase':phase,'pid':os.getpid(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**kw})
    state('waiting_for_index')
    index_pid=read(r/'JOB.json')['pid']
    while not (r/'index/freeze.json').exists():
        try:os.kill(index_pid,0)
        except ProcessLookupError:
            state('failed_index_process_exited',error='Inspect index.log; no automatic restart');return
        time.sleep(5)
    assert read(r/'index/freeze.json')['complete']
    env=os.environ.copy();env['PYTHONPATH']='src:.'
    rt=r/'qa-runtime'
    with (r/'qa.log').open('xb') as log:
        cmd=[sys.executable,'-m','benchmarks.multihop.passage_qa','--root',str(r),'--env-file',str(a.env_file),'--workers','16']
        job=subprocess.Popen(cmd,cwd=rt,env=env,stdout=log,stderr=subprocess.STDOUT)
        state('evaluating',qa_pid=job.pid)
        code=job.wait()
    if code:state('failed_evaluation',exit_code=code);return
    state('scoring_offline')
    refs=read(r/'SCORING_CONTRACT.json')
    import hashlib
    assert hashlib.sha256(Path(refs['references']).read_bytes()).hexdigest()==refs['sha256']
    with (r/'score.log').open('xb') as log:
        code=subprocess.call([sys.executable,'-m','benchmarks.multihop.passage_score','--root',str(r),'--references',refs['references']],cwd=rt,env=env,stdout=log,stderr=subprocess.STDOUT)
    if code:state('failed_scoring',exit_code=code);return
    score=read(r/'SCORED_OFFLINE.json')
    state('complete',n=score['n'],EM=score['EM'],F1=score['F1'],errors=score['execution_errors'])
    report=f"2Wiki full passage-index evaluation\n\nAll {score['index']['attempted_sources']} corpus sources attempted; {score['index']['documents']} indexed.\nExposed development questions: {score['n']}\nEM: {score['EM']:.2f}%\nF1: {score['F1']:.2f}%\nExecution errors: {score['execution_errors']} (counted zero)\nNo LLM judge, no answer postprocessing, no vectors.\n"
    (r/'REPORT.txt').write_text(report)
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--env-file',type=Path,required=True);main(ap.parse_args())
