#!/usr/bin/env python3
import asyncio,csv,json,os,sys,time
from pathlib import Path
import requests,jieba
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];sys.path[:0]=[str(HERE),str(ROOT/'fastapi-service')]
from bench_rag_ablation import DATA,OUT,docs,rrf,LLM
from bench_rag_recall import _init_embeddings,_init_vector_store
from langchain_community.retrievers import BM25Retriever

REFUSALS=('无法从知识库确认','知识库中没有','现有资料无法','没有足够信息','未提供相关信息','无法确定')
def answer(q,contexts):
    p='仅依据以下知识库片段回答。若片段不能可靠回答，必须明确回复“无法从知识库确认”，不得使用外部知识。\n问题：'+q+'\n片段：\n'+'\n---\n'.join(contexts)
    payload={'model':'qwen3-8b','messages':[{'role':'user','content':p}],'temperature':0.3,'max_tokens':500,'chat_template_kwargs':{'enable_thinking':False}}
    for i in range(3):
        try:
            x=requests.post(LLM,json=payload,timeout=60);x.raise_for_status();return x.json()['choices'][0]['message']['content']
        except requests.RequestException:
            if i==2:raise
            time.sleep(2**i)
async def main():
    cases=[json.loads(x) for x in DATA.read_text(encoding='utf-8').splitlines() if x.strip()]
    cases=[x for x in cases if x['split']=='blind_test' and x['should_refuse']]
    chunks=docs();emb=_init_embeddings();vs=_init_vector_store(chunks,emb);vr=vs.as_retriever(search_kwargs={'k':20});bm=BM25Retriever.from_documents(chunks,k=20,preprocess_func=lambda x:list(jieba.cut(x)))
    rows=[]
    for n,c in enumerate(cases,1):
        v=await asyncio.to_thread(vr.invoke,c['question']);b=await asyncio.to_thread(bm.invoke,c['question']);hybrid,_=rrf([v,b])
        from bench_rag_ablation import rerank
        ranked,_=await asyncio.to_thread(rerank,c['question'],hybrid)
        for rep in range(1,4):
            text=await asyncio.to_thread(answer,c['question'],[d.page_content for d in ranked[:5]])
            rows.append({'case_id':c['case_id'],'repeat':rep,'question':c['question'],'answer':text,'correct_refusal':any(x in text for x in REFUSALS),'context_chunk_ids':[d.metadata['chunk_id'] for d in ranked[:5]]})
        print(f'{n}/{len(cases)} {c["case_id"]}',flush=True)
    rate=sum(x['correct_refusal'] for x in rows)/len(rows);OUT.mkdir(parents=True,exist_ok=True);(OUT/'rag_refusal_blind_test.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'rag_refusal_blind_test.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_id','repeat','question','answer','correct_refusal']);w.writeheader();w.writerows([{k:r[k] for k in w.fieldnames} for r in rows])
    print(json.dumps({'cases':len(cases),'runs':len(rows),'correct_refusal_rate':rate},ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
