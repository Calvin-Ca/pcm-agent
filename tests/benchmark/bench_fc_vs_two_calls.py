#!/usr/bin/env python3
"""
指标 2：Function Calling vs 两次 LLM 调用的延迟对比基准测试（公平对比版）

A 模式（对照组）：完整 LangGraph 流程 + node_classify_intent（两次 LLM 调用）
B 模式（实验组）：完整 LangGraph 流程 + node_llm_with_tools（单次 Function Calling）

关键修正（2026-04-24）：
- 旧版 A 模式直接裸调 LLM API，未走 LangGraph，对比不公平
- 新版 A/B 均走 /api/ai/chat/stream，仅 LLM 调用次数不同，其余环节完全一致

对比维度：TTFT（首 token 延迟）、E2E（端到端延迟）
"""

import asyncio
import json
import os
import sys
import time
import csv
from datetime import date
from typing import Dict, Any, List

import aiohttp

# ─── 常量 ─────────────────────────────────────────────────────────────────────
API_BASE_URL = os.environ.get("BENCH_API_BASE", "http://localhost:8000")
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "latency_eval_50.jsonl")
RESULTS_DIR = os.environ.get("BENCH_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results"))
SMOKE_COUNT = 5

TODAY = date.today().strftime("%Y-%m-%d")


# ─── 通用 SSE 调用 ────────────────────────────────────────────────────────────

async def call_chat_stream(
    query: str,
    force_fallback: bool = False,
) -> Dict[str, Any]:
    """
    调用 /api/ai/chat/stream，解析 SSE，返回计时指标。

    Args:
        query: 用户消息
        force_fallback: True 时触发 A 模式（两次 LLM 调用）

    Returns:
        {"ttft_ms": int, "e2e_ms": int, "call_count": int, "error": str|None}
    """
    url = f"{API_BASE_URL}/api/ai/chat/stream"
    user_context = {
        "user_id": "benchmark_user",
        "user_name": "基准测试用户",
        "entity_type": "employee",
        "department_id": "1",
    }
    if force_fallback:
        user_context["_benchmark_force_fallback"] = True

    payload = {
        "message": query,
        "user_context": user_context,
        "session_id": f"bench-{int(time.time()*1000)}-{ 'a' if force_fallback else 'b' }",
    }
    headers = {"Content-Type": "application/json"}

    ttft_ms = None
    req_start = time.monotonic()
    current_event_type = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    raise ValueError(f"请求失败 ({resp.status}): {err}")

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line:
                        current_event_type = None
                        continue

                    if line.startswith("event: "):
                        current_event_type = line[7:].strip()
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            continue

                        # TTFT：第一个非 start/thinking 的 SSE 事件
                        if ttft_ms is None and current_event_type not in ("start", "thinking", None):
                            ttft_ms = int((time.monotonic() - req_start) * 1000)

    except Exception as e:
        return {
            "ttft_ms": None,
            "e2e_ms": None,
            "call_count": 0,
            "error": str(e),
        }

    e2e_ms = int((time.monotonic() - req_start) * 1000)

    return {
        "ttft_ms": ttft_ms if ttft_ms is not None else e2e_ms,
        "e2e_ms": e2e_ms,
        "call_count": 2 if force_fallback else 1,
        "error": None,
    }


# ─── 主流程 ───────────────────────────────────────────────────────────────────

async def run_benchmark(smoke: bool = False, smoke_count: int = SMOKE_COUNT):
    """运行基准测试"""
    if not os.path.exists(DATA_FILE):
        print(f"错误：测试集不存在: {DATA_FILE}")
        sys.exit(1)

    queries = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    if smoke:
        queries = queries[:smoke_count]
        print(f"=== 烟测模式：取前 {smoke_count} 条 ===")
    else:
        print(f"=== 全量测试：共 {len(queries)} 条 ===")

    print(f"API 地址: {API_BASE_URL}/api/ai/chat/stream")
    print(f"A 模式: 完整 LangGraph + 2 次 LLM (intent_classify -> param_extract)")
    print(f"B 模式: 完整 LangGraph + 1 次 LLM (Function Calling)")
    print()

    results = []

    for i, item in enumerate(queries, 1):
        qid = item["id"]
        category = item["category"]
        query = item["query"]

        print(f"[{i}/{len(queries)}] id={qid} category={category} query=\"{query}\"")

        # ── A 模式 ──────────────────────────────────────────────────────────
        try:
            result_a = await call_chat_stream(query, force_fallback=True)
            print(
                f"  A 模式: calls={result_a['call_count']} "
                f"ttft={result_a['ttft_ms']}ms e2e={result_a['e2e_ms']}ms"
                + (f" [错误: {result_a['error']}]" if result_a['error'] else "")
            )
        except Exception as e:
            print(f"  A 模式异常: {e}")
            result_a = {"ttft_ms": None, "e2e_ms": None, "call_count": 0, "error": str(e)}

        await asyncio.sleep(1.5)

        # ── B 模式 ──────────────────────────────────────────────────────────
        try:
            result_b = await call_chat_stream(query, force_fallback=False)
            print(
                f"  B 模式: calls={result_b['call_count']} "
                f"ttft={result_b['ttft_ms']}ms e2e={result_b['e2e_ms']}ms"
                + (f" [错误: {result_b['error']}]" if result_b['error'] else "")
            )
        except Exception as e:
            print(f"  B 模式异常: {e}")
            result_b = {"ttft_ms": None, "e2e_ms": None, "call_count": 0, "error": str(e)}

        results.append({
            "id": qid,
            "category": category,
            "query": query,
            "a_ttft_ms": result_a["ttft_ms"],
            "a_e2e_ms": result_a["e2e_ms"],
            "a_call_count": result_a["call_count"],
            "a_error": result_a["error"],
            "b_ttft_ms": result_b["ttft_ms"],
            "b_e2e_ms": result_b["e2e_ms"],
            "b_call_count": result_b["call_count"],
            "b_error": result_b["error"],
        })

        if i < len(queries):
            await asyncio.sleep(1.5)
        print()

    # ── 保存结果 ──────────────────────────────────────────────────────────────
    os.makedirs(RESULTS_DIR, exist_ok=True)
    suffix = "smoke" if smoke else "full"
    csv_path = os.path.join(RESULTS_DIR, f"latency_{suffix}_{date.today().strftime('%Y%m%d')}.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        if results:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    print(f"原始结果已保存: {csv_path}")

    # ── 统计汇总 ──────────────────────────────────────────────────────────────
    def _pct(values, p):
        s = sorted(values)
        idx = int(len(s) * p / 100)
        return s[max(0, min(idx, len(s) - 1))]

    def _calc(col_a, col_b, unit=""):
        vals_a = [r[col_a] for r in results if r[col_a] is not None and isinstance(r[col_a], (int, float))]
        vals_b = [r[col_b] for r in results if r[col_b] is not None and isinstance(r[col_b], (int, float))]
        if not vals_a or not vals_b:
            return "N/A"
        p50_a, p95_a = _pct(vals_a, 50), _pct(vals_a, 95)
        p50_b, p95_b = _pct(vals_b, 50), _pct(vals_b, 95)
        drop_p50 = (p50_a - p50_b) / p50_a * 100 if p50_a else 0
        drop_p95 = (p95_a - p95_b) / p95_a * 100 if p95_a else 0
        return (f"A: P50={p50_a:.0f}{unit} P95={p95_a:.0f}{unit}  |  "
                f"B: P50={p50_b:.0f}{unit} P95={p95_b:.0f}{unit}  |  "
                f"降幅: P50={drop_p50:.1f}% P95={drop_p95:.1f}%")

    print("=" * 70)
    print("统计汇总")
    print("=" * 70)
    print(f"样本数: {len(results)}")
    print(f"TTFT   | {_calc('a_ttft_ms', 'b_ttft_ms', 'ms')}")
    print(f"E2E    | {_calc('a_e2e_ms', 'b_e2e_ms', 'ms')}")

    # 按类别统计 E2E
    categories = sorted(set(r["category"] for r in results))
    print()
    print("按类别 E2E P50:")
    for cat in categories:
        cat_a = [r["a_e2e_ms"] for r in results if r["category"] == cat and r["a_e2e_ms"] is not None]
        cat_b = [r["b_e2e_ms"] for r in results if r["category"] == cat and r["b_e2e_ms"] is not None]
        if cat_a and cat_b:
            p50_a, p50_b = _pct(cat_a, 50), _pct(cat_b, 50)
            drop = (p50_a - p50_b) / p50_a * 100 if p50_a else 0
            print(f"  {cat:12s} A={p50_a:.0f}ms B={p50_b:.0f}ms 降幅={drop:.1f}%")

    print("=" * 70)

    # ── 回填区（复制到 docs/benchmarks/tasks-2026-04.md）───────────────────────
    print("\n--- 结果回填区（复制到 docs/benchmarks/tasks-2026-04.md）---")
    vals_ttft_a = [r["a_ttft_ms"] for r in results if r["a_ttft_ms"] is not None]
    vals_ttft_b = [r["b_ttft_ms"] for r in results if r["b_ttft_ms"] is not None]
    vals_e2e_a = [r["a_e2e_ms"] for r in results if r["a_e2e_ms"] is not None]
    vals_e2e_b = [r["b_e2e_ms"] for r in results if r["b_e2e_ms"] is not None]

    def _cell(vals, p):
        if not vals:
            return "___"
        return f"{_pct(vals, p):.0f} ms"

    def _drop_cell(vals_a, vals_b, p):
        if not vals_a or not vals_b:
            return "___%"
        va, vb = _pct(vals_a, p), _pct(vals_b, p)
        return f"{((va - vb) / va * 100):.1f}%"

    print(f"| 指标 | TTFT P50 | TTFT P95 | E2E P50 | E2E P95 |")
    print(f"|------|----------|----------|---------|---------|")
    print(f"| A（两次 LLM）| {_cell(vals_ttft_a, 50)} | {_cell(vals_ttft_a, 95)} | {_cell(vals_e2e_a, 50)} | {_cell(vals_e2e_a, 95)} |")
    print(f"| B（Function Calling）| {_cell(vals_ttft_b, 50)} | {_cell(vals_ttft_b, 95)} | {_cell(vals_e2e_b, 50)} | {_cell(vals_e2e_b, 95)} |")
    print(f"| 降幅 | {_drop_cell(vals_ttft_a, vals_ttft_b, 50)} | {_drop_cell(vals_ttft_a, vals_ttft_b, 95)} | {_drop_cell(vals_e2e_a, vals_e2e_b, 50)} | {_drop_cell(vals_e2e_a, vals_e2e_b, 95)} |")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Function Calling 延迟对比基准测试（公平对比版）")
    parser.add_argument("--smoke", action="store_true", help="烟测模式，只跑前 5 条")
    parser.add_argument("--smoke-count", type=int, default=SMOKE_COUNT, help=f"烟测条数，默认 {SMOKE_COUNT}")
    parser.add_argument("--api-base", default=API_BASE_URL, help=f"API 基础地址，默认 {API_BASE_URL}")
    args = parser.parse_args()

    if args.api_base:
        API_BASE_URL = args.api_base

    asyncio.run(run_benchmark(smoke=args.smoke, smoke_count=args.smoke_count))
