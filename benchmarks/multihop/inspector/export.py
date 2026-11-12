"""Export a read-only source/extraction inspector. No inference or data mutation."""
import hashlib,json,sys,collections,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from memoket_kite.core.algebra import Store
from memoket_kite.core.vocab import norm_code
BANK=ROOT/'artifacts/codebooks/multihop/full-native-v8/2wikimultihopqa.xml'
DATA=ROOT/'artifacts/datasets/multihop'
OUT=ROOT/'artifacts/visualizations/wiki2-source-inspector'
def read(p):return json.loads(p.read_text())
def put(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,separators=(',',':')))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def did(title,text):return 'd'+hashlib.sha256((title+'\0'+text).encode()).hexdigest()[:20]
store,vocab=Store.load([BANK]);rawq=read(DATA/'2wikimultihopqa.json');corpus=read(DATA/'2wikimultihopqa_corpus.json');bank_sha=digest(BANK)
source={did(x['title'],x['text']):x for x in corpus};by_title=collections.defaultdict(list)
for i,d in source.items():by_title[d['title']].append(i)
refs=collections.defaultdict(list);qindex=[];unmapped=[]
for n,q in enumerate(rawq,1):
 contexts=[]
 for title,sentences in q['context']:
  text=''.join(sentences);i=did(title,text);mode='exact'
  if i not in source:
   spaced=did(title,' '.join(sentences))
   if spaced in source:i=spaced;mode='sentence_spacing'
   else:
    i=None;mode='unmapped'
    unmapped.append({'question':q['_id'],'title':title})
  contexts.append({'title':title,'sentences':sentences,'doc_id':i,'mapping':mode,'same_title_candidates':by_title.get(title,[]) if not i else []})
  if i:refs[i].append({'id':q['_id'],'ordinal':n,'question':q['question']})
 qi={'id':q['_id'],'ordinal':n,'question':q['question'],'type':q['type'],'count':len(contexts)};qindex.append(qi)
 put(OUT/'data/questions'/f"{q['_id']}.json",{**qi,'contexts':contexts,'raw':q})
sessions={s.attrib['id']:s for s in ET.parse(BANK).getroot().findall('./timeline/session')}
docs=[];duplicates=0
for i,src in source.items():
 facts=sorted((store.facts[f] for f in store.by_unit.get(i,())),key=lambda f:int(f.id.rsplit('F',1)[-1]))
 counts=collections.Counter(' '.join(f.text.split()).casefold() for f in facts);repeat=sum(n-1 for n in counts.values());duplicates+=repeat
 codes=sorted({e for f in facts for e in f.entities});titlecode=vocab.resolve_entity(src['title']);postings=set()
 if titlecode:
  for c in {titlecode}|vocab.entities[titlecode].aliases:postings.update(store.by_entity.get(c,()))
 mismatch=bool(titlecode) and not any(store.facts[f].unit==i for f in postings)
 mapped=[]
 for f in facts:
  mapped.append({'id':f.id,'content':f.text,'who':f.who,'subject_code':vocab.resolve_entity(f.who),'entities':list(f.entities),'topics':list(f.topics),'kind':f.kind,'t':f.t,'conf':f.conf,'src':list(f.src),'facets':{'object':list(f.obj),'place':list(f.place),'event':list(f.event),'duration':f.dur},'same_text_count':counts[' '.join(f.text.split()).casefold()]})
 entities={c:{'code':c,'name':vocab.entities[c].name,'type':vocab.entities[c].etype,'aliases':sorted(vocab.entities[c].aliases),'relations':sorted(vocab.entities[c].rels)} for c in set(codes)|{f['subject_code'] for f in mapped if f['subject_code']}|({titlecode} if titlecode else set()) if c in vocab.entities}
 topics={t:{'code':t,'parents':sorted(vocab.topics[t].parents),'status':vocab.topics[t].status} for f in facts for t in f.topics if t in vocab.topics}
 row={'id':i,'title':src['title'],'fact_count':len(facts),'entity_count':len(codes),'chars':len(src['text']),'repeat':repeat,'title_mismatch':mismatch,'question_count':len(refs[i]),'search':(src['title']+' '+src['text']+' '+' '.join(codes)).casefold()}
 docs.append(row)
 put(OUT/'data/docs'/f'{i}.json',{'id':i,'title':src['title'],'text':src['text'],'raw_source':src,'facts':mapped,'entities':entities,'topics':topics,'lines':[{'id':l.id,'who':l.who,'text':l.text,'recorded_date':l.unit_date} for l in store.lines.values() if l.unit==i],'title_entity':titlecode,'title_mismatch':mismatch,'questions':refs[i],'xml':ET.tostring(sessions[i],encoding='unicode')})
meta={'dataset':'Hi-Q · 2Wiki','bank':'full-native-v8','documents':len(docs),'questions':len(qindex),'facts':len(store.facts),'entities':len(vocab.entities),'topics':len(vocab.topics),'aliases':sum(len(e.aliases) for e in vocab.entities.values()),'entity_edges':sum(len(e.rels) for e in vocab.entities.values()),'same_text_extra_rows':duplicates,'title_mismatches':sum(d['title_mismatch'] for d in docs),'unmapped_contexts':unmapped,'model':'gpt-6-luna','reasoning':'none','rounds':1,'bank_sha256':bank_sha,'source_files':{'questions':str(DATA/'2wikimultihopqa.json'),'corpus':str(DATA/'2wikimultihopqa_corpus.json'),'bank':str(BANK)},'scope':'All 1000 questions search the same 6119-document bank. Per-question context has10candidate passages; no gold used for extraction.'}
put(OUT/'data/index.json',{'meta':meta,'documents':docs,'questions':qindex})
assert digest(BANK)==bank_sha
put(OUT/'EXPORT_VERIFICATION.json',{'bank_unchanged':True,'bank_sha256':bank_sha,'documents':len(docs),'facts':len(store.facts),'questions':len(qindex),'unmapped_contexts':unmapped,'API_calls':0})
print(json.dumps({k:v for k,v in meta.items() if k not in ['source_files']},ensure_ascii=False,indent=2))
