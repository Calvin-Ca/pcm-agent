#!/usr/bin/env python3
import json,random
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'fastapi-service/reports';N=10000
answers=json.loads((R/'rag_answer_quality.json').read_text(encoding='utf-8')); by={(x['case_id'],x['strategy']):x for x in answers}
metrics=('fact_correctness','fact_coverage','unsupported_claim_rate','citation_correctness');boot={}
for m in metrics:
 d=[]
 for case in sorted({x['case_id'] for x in answers}):
  a=by[(case,'A_vector')]['answers'];e=by[(case,'E_hybrid_rerank')]['answers']
  d.extend(float(y[m])-float(x[m]) for x,y in zip(a,e))
 rng=random.Random(20260807);s=sorted(sum(d[rng.randrange(len(d))] for _ in d)/len(d) for _ in range(N));boot[m]={'delta':sum(d)/len(d),'ci95':[s[250],s[9749]]}
(R/'rag_answer_quality_bootstrap.json').write_text(json.dumps(boot,ensure_ascii=False,indent=2),encoding='utf-8')
retr=json.loads((R/'rag_ablation_analysis.json').read_text(encoding='utf-8'));ref=json.loads((R/'rag_refusal_blind_test.json').read_text(encoding='utf-8'));summ=json.loads((R/'rag_answer_quality_summary.json').read_text(encoding='utf-8'))
rate=sum(x['correct_refusal'] for x in ref)/len(ref)
lines=['# RAG 混合检索实验最终报告','','## 最终结论','','- 冻结推荐方案：E（向量 + BM25 RRF + bge-reranker-large）。','- 检索排序增益显著，但 Recall@5 +8pp 验收线未达到。',f'- 无答案正确拒答率：{rate:.2%}，未达到 95% 验收线。','- 答案事实、覆盖、引用均改善，无依据陈述下降。','','## 盲测检索（E 相对 A）','']
for m,x in retr['comparisons']['E_hybrid_rerank'].items():lines.append(f"- {m}: {x['delta']:+.4f}, 95% CI [{x['ci95_low']:.4f}, {x['ci95_high']:.4f}]")
lines += ['','## 盲测答案质量（324 vs 324）','']
for s,v in summ.items():lines.append(f"- {s}: 正确率 {v['fact_correctness']:.2%}，覆盖率 {v['fact_coverage']:.2%}，无依据率 {v['unsupported_claim_rate']:.2%}，引用正确率 {v['citation_correctness']:.2%}")
lines += ['','### E 相对 A 配对 Bootstrap','']
for m,x in boot.items():lines.append(f"- {m}: {x['delta']:+.4f}, 95% CI [{x['ci95'][0]:.4f}, {x['ci95'][1]:.4f}]")
lines += ['','## 验收判定','','- 检索 Recall@5 提升 ≥8pp：未通过（+6.48pp）。','- MRR/nDCG 显著提升：通过。','- 答案要点覆盖率提升 ≥8pp：未通过（+3.23pp）。','- 无依据陈述率不得上升：通过（下降约3.32pp）。','- 无答案正确拒答率 ≥95%：未通过（33.33%）。','- 延迟分阶段记录：通过。','']
(R/'rag_ablation_final_report.md').write_text('\n'.join(lines),encoding='utf-8');print(json.dumps(boot,ensure_ascii=False,indent=2))
