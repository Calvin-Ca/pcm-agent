#!/usr/bin/env python3
"""Generate and judge blind-test answers for A_vector and E_hybrid_rerank."""
from __future__ import annotations
import json, re, sys, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]; sys.path.insert(0,str(HERE))
import generate_rag_ablation_candidates as gen
from bench_rag_ablation import DATA,OUT,LLM

DETAIL=OUT/'rag_ablation_blind_test.json'; CHECK=OUT/'rag_answer_quality.checkpoint.jsonl'
FINAL=OUT/'rag_answer_quality.json'; SUMMARY=OUT/'rag_answer_quality_summary.json'
STRATEGIES=('A_vector','E_hybrid_rerank'); lock=threading.Lock()

def post(messages,max_tokens=900,temperature=0.2):
    payload={'model':'qwen3-8b','messages':messages,'temperature':temperature,'max_tokens':max_tokens,'chat_template_kwargs':{'enable_thinking':False}}
    for attempt in range(4):
        try:
            r=requests.post(LLM,json=payload,timeout=90);r.raise_for_status();return r.json()['choices'][0]['message']['content']
        except requests.RequestException:
            if attempt==3: raise
            time.sleep(2**attempt)

def chunk_map():
    raw=gen._load_documents_from_dir(str(ROOT/'knowledge-base')); split=gen._split_documents(raw); idx={}; out={}
    for d in split:
        text=gen._clean_text(d.page_content)
        if len(text)<100:continue
        src=gen._norm_source(str(d.metadata.get('source','')));i=idx.get(src,0);idx[src]=i+1
        out[gen._chunk_id(src,i,text)]=text
    return out

def parse_json(text):
    text=re.sub(r'<think>.*?</think>','',text,flags=re.S);a=text.find('{');b=text.rfind('}')
    return json.loads(text[a:b+1])

def run_task(task,chunks):
    contexts=[]
    for cid in task['chunk_ids'][:5]: contexts.append(f'[{cid}] {chunks[cid]}')
    prompt='仅依据给定知识库片段回答问题。覆盖所有有依据的要点；每个事实后引用对应 [chunk_id]；禁止使用外部知识。\n问题：'+task['question']+'\n片段：\n'+'\n---\n'.join(contexts)
    answers=[post([{'role':'user','content':prompt}],700,.3) for _ in range(3)]
    judge_prompt='''你是严格的RAG答案裁判。根据问题、标准事实点、允许引用的chunk和三个答案评分。
只返回JSON对象：{"scores":[{"fact_correctness":0到1,"fact_coverage":0到1,"unsupported_claim_rate":0到1,"citation_correctness":0到1}]}。
scores必须恰好3项。事实正确率衡量已陈述事实正确性；覆盖率衡量标准事实覆盖；无依据率衡量陈述中无材料支持的比例；引用正确率衡量引用是否支持相邻事实。
''' + json.dumps({'question':task['question'],'must_answer_facts':task['facts'],'allowed_chunks':contexts,'answers':answers},ensure_ascii=False)
    judged=parse_json(post([{'role':'user','content':judge_prompt}],900,0))['scores']
    if len(judged)!=3: raise ValueError('judge scores must contain 3 items')
    return {**task,'answers':[{'repeat':i+1,'answer':answers[i],**judged[i]} for i in range(3)]}

def main():
    cases={x['case_id']:x for x in (json.loads(s) for s in DATA.read_text(encoding='utf-8').splitlines()) if x['split']=='blind_test' and not x['should_refuse']}
    retrieved=json.loads(DETAIL.read_text(encoding='utf-8')); tasks=[]
    for r in retrieved:
        if r['strategy'] in STRATEGIES:
            c=cases[r['case_id']];tasks.append({'case_id':r['case_id'],'category':c['category'],'strategy':r['strategy'],'question':c['question'],'facts':c['must_answer_facts'],'chunk_ids':r['retrieved_chunk_ids']})
    done={}
    if CHECK.exists():
        for line in CHECK.read_text(encoding='utf-8').splitlines():
            if line.strip():
                x=json.loads(line);done[(x['case_id'],x['strategy'])]=x
    pending=[t for t in tasks if (t['case_id'],t['strategy']) not in done]; chunks=chunk_map()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(run_task,t,chunks):t for t in pending}
        for n,f in enumerate(as_completed(futures),1):
            x=f.result();done[(x['case_id'],x['strategy'])]=x
            with lock, CHECK.open('a',encoding='utf-8') as h:h.write(json.dumps(x,ensure_ascii=False)+'\n')
            print(f'{len(done)}/{len(tasks)} {x["case_id"]} {x["strategy"]}',flush=True)
    rows=sorted(done.values(),key=lambda x:(x['case_id'],x['strategy']));FINAL.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={}
    for s in STRATEGIES:
        z=[a for r in rows if r['strategy']==s for a in r['answers']]
        summary[s]={k:sum(float(x[k]) for x in z)/len(z) for k in ('fact_correctness','fact_coverage','unsupported_claim_rate','citation_correctness')};summary[s]['answers']=len(z)
    SUMMARY.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
