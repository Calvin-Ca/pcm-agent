#!/usr/bin/env python3
"""Analyze blind RAG ablation results with paired bootstrap intervals."""
import json, random, statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; REPORT=ROOT/'fastapi-service/reports'
ROWS=json.loads((REPORT/'rag_ablation_blind_test.json').read_text(encoding='utf-8'))
METRICS=('recall5','recall10','precision5','mrr10','ndcg10'); N=10000; SEED=20260806

def mean(x): return sum(x)/len(x)
def ci(a,b):
    diffs=[y-x for x,y in zip(a,b)]; rng=random.Random(SEED); n=len(diffs)
    samples=sorted(sum(diffs[rng.randrange(n)] for _ in range(n))/n for _ in range(N))
    return {'delta':mean(diffs),'ci95_low':samples[int(N*.025)],'ci95_high':samples[int(N*.975)]}

by=defaultdict(dict)
for r in ROWS: by[r['case_id']][r['strategy']]=r
comparisons={}
for target in ('C_hybrid','D_hybrid_rewrite','E_hybrid_rerank','F_full'):
    comparisons[target]={}
    for m in METRICS:
        comparisons[target][m]=ci([x['A_vector'][m] for x in by.values()],[x[target][m] for x in by.values()])

category={}
for cat in sorted({r['category'] for r in ROWS}):
    category[cat]={}
    for strategy in sorted({r['strategy'] for r in ROWS}):
        z=[r for r in ROWS if r['category']==cat and r['strategy']==strategy]
        category[cat][strategy]={m:mean([x[m] for x in z]) for m in METRICS}

cases=[next(iter(x.values())) for x in by.values()]; timings={}
for k in cases[0]['timing_ms']:
    vals=sorted(x['timing_ms'][k] for x in cases)
    timings[k]={'mean_ms':mean(vals),'p50_ms':statistics.median(vals),'p95_ms':vals[int(len(vals)*.95)-1]}

out={'bootstrap_iterations':N,'seed':SEED,'case_count':len(by),'paired_vs':'A_vector','comparisons':comparisons,'category_metrics':category,'timing':timings}
(REPORT/'rag_ablation_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# RAG 盲测检索消融统计分析','','配对 Bootstrap：10,000 次，95% 置信区间；正值表示优于 A_vector。','']
for target,metrics in comparisons.items():
    lines += [f'## {target}','', '| 指标 | 差值 | 95% CI | 显著 |','|---|---:|---:|:---:|']
    for m,x in metrics.items(): lines.append(f"| {m} | {x['delta']:.4f} | [{x['ci95_low']:.4f}, {x['ci95_high']:.4f}] | {'是' if x['ci95_low']>0 or x['ci95_high']<0 else '否'} |")
    lines.append('')
lines += ['## 阶段耗时','', '| 阶段 | Mean ms | P50 ms | P95 ms |','|---|---:|---:|---:|']
for k,x in timings.items(): lines.append(f"| {k} | {x['mean_ms']:.1f} | {x['p50_ms']:.1f} | {x['p95_ms']:.1f} |")
(REPORT/'rag_ablation_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'comparisons':comparisons,'timing':timings},ensure_ascii=False,indent=2))
