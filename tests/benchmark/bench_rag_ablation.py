#!/usr/bin/env python3
"""A-F retrieval ablation on the frozen 200-case RAG dataset."""
from __future__ import annotations
import argparse, asyncio, csv, json, math, os, sys, time
from collections import defaultdict
from pathlib import Path
import requests

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
sys.path[:0]=[str(HERE),str(ROOT/'fastapi-service')]
import generate_rag_ablation_candidates as gen
from bench_rag_recall import _init_embeddings, _init_vector_store
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
import jieba

DATA=ROOT/'tests/benchmark/data/rag_ablation_candidates_200.jsonl'
OUT=ROOT/'fastapi-service/reports'; RERANK='http://172.19.3.136:8095/rerank'
LLM='http://172.19.3.136:8099/v1/chat/completions'

def docs():
    raw=gen._load_documents_from_dir(str(ROOT/'knowledge-base')); split=gen._split_documents(raw)
    idx={}; out=[]
    for d in split:
        text=gen._clean_text(d.page_content)
        if len(text)<100: continue
        src=gen._norm_source(str(d.metadata.get('source',''))); i=idx.get(src,0); idx[src]=i+1
        d.metadata.update(source=src,chunk_id=gen._chunk_id(src,i,text)); d.page_content=text; out.append(d)
    return out

def rewrite(q):
    p='将问题改写为3个用于知识库检索的中文查询，保留全部约束。只返回JSON字符串数组。问题：'+q
    payload={'model':'qwen3-8b','messages':[{'role':'user','content':p}],'temperature':0,'max_tokens':300,'chat_template_kwargs':{'enable_thinking':False}}
    for attempt in range(3):
        try:
            response=requests.post(LLM,json=payload,timeout=60); response.raise_for_status(); x=response.json(); break
        except requests.RequestException:
            if attempt==2: return [q]
            time.sleep(2**attempt)
    s=x['choices'][0]['message']['content']; a=s.find('['); b=s.rfind(']')
    try:
        values=json.loads(s[a:b+1])[:3]
        values=[x.get('query','') if isinstance(x,dict) else str(x) for x in values]
        return [q]+[x for x in values if x]
    except Exception: return [q]

def rrf(groups,k=60):
    score=defaultdict(float); by={}
    for group in groups:
        for rank,d in enumerate(group,1):
            cid=d.metadata['chunk_id']; score[cid]+=1/(k+rank); by[cid]=d
    return [by[c] for c in sorted(score,key=score.get,reverse=True)],score

def rerank(q,ds):
    if not ds:return [],[]
    payload={'query':q,'passages':[d.page_content for d in ds[:20]],'normalize':True}
    for attempt in range(3):
        try:
            response=requests.post(RERANK,json=payload,timeout=120); response.raise_for_status(); x=response.json()['scores']; break
        except requests.RequestException:
            if attempt==2: return ds[:20],[0.0]*min(20,len(ds))
            time.sleep(2**attempt)
    pairs=sorted(zip(ds[:20],x),key=lambda z:z[1],reverse=True)
    return [p[0] for p in pairs],[p[1] for p in pairs]

def metrics(ds,gold):
    ids=[d.metadata['chunk_id'] for d in ds]; rel=[1 if x in gold else 0 for x in ids]
    recall=lambda k: len(set(ids[:k])&gold)/len(gold) if gold else 0
    mrr=next((1/i for i,x in enumerate(ids[:10],1) if x in gold),0)
    dcg=sum(v/math.log2(i+2) for i,v in enumerate(rel[:10])); ideal=sum(1/math.log2(i+2) for i in range(min(len(gold),10)))
    return {'recall5':recall(5),'recall10':recall(10),'precision5':sum(rel[:5])/5,'mrr10':mrr,'ndcg10':dcg/ideal if ideal else 0}

async def main(split_name):
    cases=[json.loads(x) for x in DATA.read_text(encoding='utf-8').splitlines() if x.strip()]
    cases=[x for x in cases if x['split']==split_name and not x['should_refuse']]
    chunks=docs(); emb=_init_embeddings(); vs=_init_vector_store(chunks,emb); vr=vs.as_retriever(search_kwargs={'k':20})
    bm=BM25Retriever.from_documents(chunks,k=20,preprocess_func=lambda x:list(jieba.cut(x)))
    rows=[]
    for n,c in enumerate(cases,1):
        q=c['question']; gold=set(c['relevant_chunk_ids']); timing={}; t=time.perf_counter()
        v=await asyncio.to_thread(vr.invoke,q); timing['vector_ms']=(time.perf_counter()-t)*1000
        t=time.perf_counter(); b=await asyncio.to_thread(bm.invoke,q); timing['bm25_ms']=(time.perf_counter()-t)*1000
        hybrid,hs=rrf([v,b]); t=time.perf_counter(); qs=await asyncio.to_thread(rewrite,q); timing['rewrite_ms']=(time.perf_counter()-t)*1000
        groups=[]
        for rq in qs:
            groups += [await asyncio.to_thread(vr.invoke,rq),await asyncio.to_thread(bm.invoke,rq)]
        rewritten,rs=rrf(groups); t=time.perf_counter(); er,es=await asyncio.to_thread(rerank,q,hybrid); timing['rerank_e_ms']=(time.perf_counter()-t)*1000
        t=time.perf_counter(); fr,fs=await asyncio.to_thread(rerank,q,rewritten); timing['rerank_f_ms']=(time.perf_counter()-t)*1000
        variants={'A_vector':v,'B_bm25':b,'C_hybrid':hybrid,'D_hybrid_rewrite':rewritten,'E_hybrid_rerank':er,'F_full':fr}
        for name,result in variants.items():
            rows.append({'case_id':c['case_id'],'category':c['category'],'strategy':name,**metrics(result,gold),'retrieved_chunk_ids':[d.metadata['chunk_id'] for d in result[:20]],'rewritten_queries':qs,'timing_ms':timing})
        print(f'{n}/{len(cases)} {c["case_id"]}',flush=True)
    OUT.mkdir(parents=True,exist_ok=True); detail=OUT/f'rag_ablation_{split_name}.json'; detail.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=[]
    for s in sorted({r['strategy'] for r in rows}):
        z=[r for r in rows if r['strategy']==s]; summary.append({'strategy':s,**{k:sum(r[k] for r in z)/len(z) for k in ('recall5','recall10','precision5','mrr10','ndcg10')}})
    with (OUT/f'rag_ablation_{split_name}.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=summary[0]);w.writeheader();w.writerows(summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',default='development',choices=['development','validation','blind_test']);a=p.parse_args();asyncio.run(main(a.split))
