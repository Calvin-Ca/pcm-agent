"""
Embedding 模型对比测试

对比两个 embedding 模型在 knowledge_qa 场景下的召回率与延迟：
  1. bge-large-zh (vLLM,  http://172.19.3.136:8098/v1)
  2. qwen3-embedding:8b (Ollama, http://172.19.3.136:11434/v1)

评估维度：
  - 冷启动耗时（首次 embed_query）
  - 知识库批量编码总耗时（chunks 编码）
  - 单 query 平均延迟（eval 集平均）
  - Recall@3 / @5 / @10（top-k 命中 expected_doc）
  - 向量维度

使用方法（在 fastapi-service/ 目录下）:
    python -m tests.evaluation.test_embedding_compare

评估集路径：tests/evaluation/eval_set_knowledge_qa.json
  reviewer_confirmed=false 的条目也会参与评估（全量评估）；
  如果只想跑已确认的条目，加 --confirmed-only 开关。
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# 让本脚本能从 fastapi-service/ 目录启动时导入 app.*
_FASTAPI_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_FASTAPI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_FASTAPI_SERVICE_ROOT))

from langchain_openai import OpenAIEmbeddings  # noqa: E402

from app.services.langchain_rag import (  # noqa: E402
    _load_documents_from_dir,
    _split_documents,
)


# ── 配置 ───────────────────────────────────────────────
KB_PATH = _FASTAPI_SERVICE_ROOT.parent / "knowledge-base"
EVAL_SET_PATH = Path(__file__).parent / "eval_set_knowledge_qa.json"
REPORT_PATH = (
    _FASTAPI_SERVICE_ROOT.parent
    / "docs"
    / "test-reports"
    / "embedding-compare-2026-04-10.md"
)

MODELS: Dict[str, Dict[str, Any]] = {
    "bge-large-zh-v1.5 (vLLM, port 8097)": {
        "model": "/model",
        "api_key": "EMPTY",
        "api_base": "http://172.19.3.136:8097/v1",
        "chunk_size": 32,
    },
    "bge-base-zh-v1.5 (vLLM, port 8098)": {
        "model": "/model",
        "api_key": "EMPTY",
        "api_base": "http://172.19.3.136:8098/v1",
        "chunk_size": 32,
    },
    "qwen3-embedding:8b (Ollama)": {
        "model": "qwen3-embedding:8b",
        "api_key": "ollama",
        "api_base": "http://172.19.3.136:11434/v1",
        "chunk_size": 32,
    },
}


# ── 工具函数 ─────────────────────────────────────────────
def cosine_similarity_one_to_many(q: np.ndarray, docs: np.ndarray) -> np.ndarray:
    """单 query 对批量 doc 的余弦相似度（已假设非零向量）。"""
    q_norm = q / (np.linalg.norm(q) + 1e-12)
    doc_norms = np.linalg.norm(docs, axis=1, keepdims=True) + 1e-12
    docs_norm = docs / doc_norms
    return docs_norm @ q_norm


def build_embeddings_client(cfg: Dict[str, Any]) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=cfg["model"],
        openai_api_key=cfg["api_key"],
        openai_api_base=cfg["api_base"],
        chunk_size=cfg.get("chunk_size", 32),
        check_embedding_ctx_length=False,
    )


def hit_topk(top_docs: List[str], expected_doc: str, k: int) -> bool:
    """top-k 中是否存在 source 包含 expected_doc 文件名的 chunk。"""
    return any(expected_doc in (d or "") for d in top_docs[:k])


# ── 核心评估 ─────────────────────────────────────────────
def evaluate_model(
    name: str,
    cfg: Dict[str, Any],
    chunks: List[Any],
    eval_cases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    print(f"\n{'=' * 70}")
    print(f"  评估模型: {name}")
    print(f"{'=' * 70}")

    emb = build_embeddings_client(cfg)

    # 1. 冷启动
    t0 = time.time()
    _probe_vec = emb.embed_query("你好")
    cold_start_s = time.time() - t0
    dim = len(_probe_vec)
    print(f"  冷启动耗时: {cold_start_s:.2f}s | 向量维度: {dim}")

    # 2. 批量编码 chunks
    chunk_texts = [c.page_content for c in chunks]
    t0 = time.time()
    chunk_vecs_list = emb.embed_documents(chunk_texts)
    chunks_encode_s = time.time() - t0
    chunk_vecs = np.asarray(chunk_vecs_list, dtype=np.float32)
    print(
        f"  Chunks 编码: {len(chunks)} 块, 耗时 {chunks_encode_s:.2f}s "
        f"(平均 {chunks_encode_s / max(1, len(chunks)) * 1000:.1f} ms/块)"
    )

    # 3. 逐条 query 检索并判命中
    query_times: List[float] = []
    hit_at = {3: 0, 5: 0, 10: 0}
    details: List[Dict[str, Any]] = []

    for case in eval_cases:
        query = case["query"]
        expected_doc = case["expected_doc"]

        t0 = time.time()
        q_vec = np.asarray(emb.embed_query(query), dtype=np.float32)
        query_times.append(time.time() - t0)

        sims = cosine_similarity_one_to_many(q_vec, chunk_vecs)
        top_idx = np.argsort(-sims)[:10]
        top_docs = [
            str(chunks[i].metadata.get("source", ""))
            for i in top_idx
        ]

        case_hits = {k: hit_topk(top_docs, expected_doc, k) for k in (3, 5, 10)}
        for k, h in case_hits.items():
            if h:
                hit_at[k] += 1

        details.append(
            {
                "id": case["id"],
                "sub_type": case.get("sub_type", ""),
                "query": query,
                "expected_doc": expected_doc,
                "top5_sources": [Path(d).name for d in top_docs[:5]],
                "top5_scores": [float(sims[i]) for i in top_idx[:5]],
                "hit@3": case_hits[3],
                "hit@5": case_hits[5],
                "hit@10": case_hits[10],
            }
        )

    total = len(eval_cases)
    result = {
        "model": name,
        "dim": dim,
        "cold_start_s": round(cold_start_s, 3),
        "chunks_encode_s": round(chunks_encode_s, 3),
        "chunks_encode_ms_per_chunk": round(
            chunks_encode_s / max(1, len(chunks)) * 1000, 1
        ),
        "avg_query_ms": round(sum(query_times) / len(query_times) * 1000, 1),
        "total": total,
        "recall@3": round(hit_at[3] / total * 100, 1),
        "recall@5": round(hit_at[5] / total * 100, 1),
        "recall@10": round(hit_at[10] / total * 100, 1),
        "hit@3": hit_at[3],
        "hit@5": hit_at[5],
        "hit@10": hit_at[10],
        "details": details,
    }
    print(
        f"  结果:  Recall@3 {result['recall@3']}%  |  "
        f"Recall@5 {result['recall@5']}%  |  "
        f"Recall@10 {result['recall@10']}%  |  "
        f"avg_query {result['avg_query_ms']} ms"
    )
    return result


# ── 报告生成 ─────────────────────────────────────────────
def generate_report(
    all_results: List[Dict[str, Any]],
    eval_meta: Dict[str, Any],
    n_docs: int,
    n_chunks: int,
) -> str:
    lines: List[str] = []
    lines.append("# Embedding 模型对比报告（2026-04-10）\n")
    lines.append(f"**知识库**：{n_docs} 篇文档 → {n_chunks} 个 chunks\n")
    lines.append(f"**评估集**：{all_results[0]['total']} 条 query（来自 knowledge_qa.json）\n")
    lines.append("**判命中规则**：top-k 中出现至少一个 chunk 来自 expected_doc 即算命中\n")
    lines.append("")

    # 一、核心指标对比
    lines.append("## 一、核心指标对比\n")
    lines.append("| 指标 | " + " | ".join(r["model"] for r in all_results) + " |")
    lines.append("|------|" + "|".join(["------"] * len(all_results)) + "|")

    rows = [
        ("向量维度", "dim", ""),
        ("冷启动 (s)", "cold_start_s", ""),
        ("Chunks 编码总耗时 (s)", "chunks_encode_s", ""),
        ("Chunks 编码平均 (ms/块)", "chunks_encode_ms_per_chunk", ""),
        ("单 query 平均延迟 (ms)", "avg_query_ms", ""),
        ("Recall@3 (%)", "recall@3", ""),
        ("Recall@5 (%)", "recall@5", ""),
        ("Recall@10 (%)", "recall@10", ""),
    ]
    for label, key, _ in rows:
        row = f"| {label} |"
        for r in all_results:
            row += f" {r.get(key, '-')} |"
        lines.append(row)
    lines.append("")

    # 二、按 sub_type 分解
    lines.append("## 二、按 sub_type 分解 Recall@5\n")
    sub_types: List[str] = []
    for r in all_results:
        for d in r["details"]:
            st = d.get("sub_type", "")
            if st and st not in sub_types:
                sub_types.append(st)

    lines.append(
        "| sub_type | " + " | ".join(r["model"] for r in all_results) + " |"
    )
    lines.append(
        "|----------|" + "|".join(["------"] * len(all_results)) + "|"
    )
    for st in sub_types:
        row = f"| {st} |"
        for r in all_results:
            total_st = sum(1 for d in r["details"] if d.get("sub_type") == st)
            hit_st = sum(
                1
                for d in r["details"]
                if d.get("sub_type") == st and d.get("hit@5")
            )
            rate = (hit_st / total_st * 100) if total_st else 0.0
            row += f" {hit_st}/{total_st} ({rate:.0f}%) |"
        lines.append(row)
    lines.append("")

    # 三、失败用例（只列第一个模型失败的，供分析）
    lines.append("## 三、Recall@5 失败用例（按模型列出）\n")
    for r in all_results:
        lines.append(f"### {r['model']}")
        failed = [d for d in r["details"] if not d.get("hit@5")]
        if not failed:
            lines.append("- 无失败用例 ✅")
        else:
            for d in failed:
                lines.append(
                    f"- `{d['id']}` [{d['sub_type']}] {d['query']}"
                )
                lines.append(
                    f"  - expected: `{d['expected_doc']}`"
                )
                lines.append(
                    f"  - top5: {d['top5_sources']}"
                )
        lines.append("")

    # 四、结论建议
    lines.append("## 四、观察与建议\n")
    lines.append("> 自动生成的摘要，结合实际业务场景人工判断后决策。\n")
    lines.append("")
    # 简单结论：谁 recall@5 高 & 延迟更低
    best_recall = max(all_results, key=lambda r: r["recall@5"])
    best_latency = min(all_results, key=lambda r: r["avg_query_ms"])
    lines.append(f"- **Recall@5 最高**：{best_recall['model']} ({best_recall['recall@5']}%)")
    lines.append(
        f"- **单次 query 最快**：{best_latency['model']} ({best_latency['avg_query_ms']} ms)"
    )
    lines.append(
        f"- **冷启动最快**：{min(all_results, key=lambda r: r['cold_start_s'])['model']}"
    )
    lines.append("")

    return "\n".join(lines)


# ── 入口 ─────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirmed-only",
        action="store_true",
        help="只评估 reviewer_confirmed=true 的条目",
    )
    parser.add_argument(
        "--model",
        action="append",
        choices=list(MODELS.keys()),
        help="只评估指定模型（可多次），默认评估全部",
    )
    args = parser.parse_args()

    # 1. 加载知识库 & 切片
    print(f"加载知识库: {KB_PATH}")
    docs = _load_documents_from_dir(str(KB_PATH))
    chunks = _split_documents(docs)
    print(f"  {len(docs)} 篇文档 → {len(chunks)} 个 chunks")

    # 2. 加载评估集
    print(f"加载评估集: {EVAL_SET_PATH}")
    with open(EVAL_SET_PATH, encoding="utf-8") as f:
        eval_data = json.load(f)

    cases = eval_data["cases"]
    if args.confirmed_only:
        cases = [c for c in cases if c.get("reviewer_confirmed", False)]
        print(f"  仅评估 reviewer_confirmed=true 条目：{len(cases)}")
    else:
        print(f"  评估集条目：{len(cases)}")

    if not cases:
        print("[ERROR] 评估集为空")
        sys.exit(1)

    # 3. 执行各模型评估
    target_models = args.model or list(MODELS.keys())
    all_results: List[Dict[str, Any]] = []
    for name in target_models:
        try:
            r = evaluate_model(name, MODELS[name], chunks, cases)
            all_results.append(r)
        except Exception as e:
            print(f"[ERROR] 模型 {name} 评估失败: {e}")

    if not all_results:
        print("[ERROR] 所有模型评估均失败")
        sys.exit(2)

    # 4. 生成对比报告
    report = generate_report(all_results, eval_data, len(docs), len(chunks))
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n报告已写入：{REPORT_PATH}")

    # 5. 同时保存一份 JSON 明细，便于后续分析
    json_path = REPORT_PATH.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "knowledge_base": {
                    "docs": len(docs),
                    "chunks": len(chunks),
                },
                "eval_set": {
                    "total_cases": len(cases),
                    "confirmed_only": args.confirmed_only,
                },
                "results": all_results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"明细 JSON：{json_path}")


if __name__ == "__main__":
    main()
