#!/usr/bin/env python3
"""
Progressive RAG (A-RAG) vs One-shot RAG 对比评测

评测方式:
  - oneshot:    临时移除 kb_* 工具, LLM 只能看见 knowledge_qa
  - progressive: 全量工具可用, LLM 可自主多轮调用 kb_outline / kb_*_search / kb_read_section
  - both:       两个模式都跑, 输出对比 CSV

用法:
  python bench_progressive_rag.py --mode oneshot
  python bench_progressive_rag.py --mode progressive
  python bench_progressive_rag.py --mode both
  python bench_progressive_rag.py --smoke
  python bench_progressive_rag.py --mark-mode --csv results/progressive_rag_2026-05-05.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将 fastapi-service 加入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── 常量 ─────────────────────────────────────────────────────────────────────
DATA_FILE = Path(__file__).with_suffix("").parent / "data" / "progressive_rag_eval_18.jsonl"
RESULTS_DIR = Path(__file__).with_suffix("").parent / "results"

# vLLM 配置 (评测直接调用, 不依赖已启动的 FastAPI 服务)
VLLM_API_BASE = os.getenv("CHAT_LLM_API_BASE", "http://172.19.3.136:8099/v1")
VLLM_API_KEY = os.getenv("CHAT_LLM_API_KEY", "EMPTY")
VLLM_MODEL = os.getenv("CHAT_LLM_MODEL", "qwen3-8b")

# 知识库路径 (相对 fastapi-service)
# 注意: fastapi-service/knowledge-base 是空目录, 真实文件在 ai-service/knowledge-base
KB_PATH = os.getenv("KB_PATH", "../knowledge-base")

# Milvus 配置 (本地评测直连 172, 不走 .env 里的 docker 容器名 'milvus')
MILVUS_HOST = os.getenv("MILVUS_HOST_OVERRIDE", "172.19.3.136")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")

# ─── 环境变量预设 ─────────────────────────────────────────────────────────────
# 强制让 RAG 生成阶段也走本地 vLLM, 不走 DashScope
os.environ.setdefault("CHAT_LLM_API_BASE", VLLM_API_BASE)
os.environ.setdefault("CHAT_LLM_API_KEY", VLLM_API_KEY)
os.environ.setdefault("CHAT_LLM_MODEL", VLLM_MODEL)
# 让 langchain_rag 内 os.getenv("MILVUS_HOST") 拿到本地可达的 IP, 避免静默回退 FAISS
os.environ["MILVUS_HOST"] = MILVUS_HOST
os.environ["MILVUS_PORT"] = MILVUS_PORT


# ─── 初始化 ───────────────────────────────────────────────────────────────────

_rag_initialized = False
_agent_initialized = False
_original_tools: Dict[str, Any] = {}


async def _init_rag():
    """初始化 LangChain RAG 服务 (只做一次)"""
    global _rag_initialized
    if _rag_initialized:
        return
    from app.services.langchain_rag import initialize_langchain_rag

    result = await initialize_langchain_rag(KB_PATH)
    if not result.get("success"):
        logger.error(f"RAG 初始化失败: {result}")
        raise RuntimeError("RAG 初始化失败")
    logger.info(f"RAG 初始化完成: {result['loaded_files']} 文件 / {result['total_chunks']} chunks")
    _rag_initialized = True


def _init_agent_components():
    """初始化 Agent 组件 (只做一次)"""
    global _agent_initialized
    if _agent_initialized:
        return

    from app.services.langgraph_agent import initialize_agent
    from app.services.llm_client import LLMClient
    from app.services.intent_router import IntentRouter
    from app.services.task_executor import TaskExecutor
    from app.services.tool_registry import ToolRegistry

    # 触发工具注册
    from app.tools import (
        approve_workhour,
        batch_save_workhour,
        compute_statistics,
        export_report,
        generate_weekly_report,
        kb_keyword_search,
        kb_outline,
        kb_read_section,
        kb_semantic_search,
        knowledge_qa,
        query_project,
        query_timesheet,
        save_workhour,
        suggest_workhour,
    )

    llm_client = LLMClient(
        api_key=VLLM_API_KEY,
        api_base=VLLM_API_BASE,
        model=VLLM_MODEL,
        env_prefix="CHAT_LLM",
    )

    tool_registry = ToolRegistry()
    intent_router = IntentRouter()
    task_executor = TaskExecutor(tool_registry=tool_registry, llm_client=llm_client)

    initialize_agent(
        intent_router=intent_router,
        tool_registry=tool_registry,
        task_executor=task_executor,
        llm_client=llm_client,
    )

    # 备份原始工具列表 (用于 oneshot / progressive 切换)
    global _original_tools
    _original_tools = {
        "tools": dict(tool_registry._tools),
        "handlers": dict(tool_registry._handlers),
    }

    _agent_initialized = True
    logger.info(f"Agent 初始化完成, 工具数: {len(tool_registry.list_tools())}")


def _set_mode(mode: str):
    """切换 oneshot / progressive 模式"""
    from app.services.tool_registry import ToolRegistry

    tr = ToolRegistry()
    kb_tools = ["kb_outline", "kb_keyword_search", "kb_semantic_search", "kb_read_section"]

    if mode == "oneshot":
        # 移除 kb_* 工具, 只保留 knowledge_qa 等旧工具
        for t in kb_tools:
            if t in tr._tools:
                del tr._tools[t]
            if t in tr._handlers:
                del tr._handlers[t]
    elif mode == "progressive":
        # 恢复全部工具
        tr._tools.clear()
        tr._handlers.clear()
        tr._tools.update(_original_tools["tools"])
        tr._handlers.update(_original_tools["handlers"])
    logger.info(f"模式切换为 {mode}, 当前工具: {[t.name for t in tr.list_tools()]}")


# ─── 单次评测 ─────────────────────────────────────────────────────────────────

async def run_one(query_record: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """跑单条 query, 返回评测指标"""
    from app.services.langgraph_agent import _build_graph

    _set_mode(mode)
    graph = _build_graph()

    # 构建初始 state
    system_prompt = _build_system_prompt(mode)
    conversation_history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query_record["query"]},
    ]

    state = {
        "user_message": query_record["query"],
        "user_context": {"user_id": "benchmark_user", "entity_type": "employee"},
        "session_id": f"bench-{query_record['id']}-{mode}",
        "conversation_history": conversation_history,
        "intent": None,
        "tool_name": None,
        "tool_params": {},
        "query": "",
        "clarify_message": None,
        "tool_result": None,
        "rag_result": None,
        "llm_result": None,
        "error": None,
        "task_plan": None,
        "plan_results": None,
        "agent_iterations": 0,
        "agent_max_iterations": 5,
        "agent_history": [],
    }

    start_ts = time.time()
    try:
        result = await graph.ainvoke(state)
    except Exception as e:
        logger.error(f"[{query_record['id']}] {mode} 执行失败: {e}")
        return {
            "id": query_record["id"],
            "category": query_record["category"],
            "mode": mode,
            "query": query_record["query"],
            "answer": f"[ERROR] {e}",
            "answer_completeness": "",
            "docs_coverage": 0.0,
            "tokens": 0,
            "latency_ms": int((time.time() - start_ts) * 1000),
            "tool_calls": 0,
            "tools_used": "",
            "docs_retrieved": "",
            "error": str(e),
        }
    elapsed_ms = int((time.time() - start_ts) * 1000)

    # 提取答案
    answer = _extract_answer(result)

    # 提取工具调用历史
    history = result.get("agent_history", [])
    tools_used = [h["tool"] for h in history]
    tool_calls = len(history)

    # 提取检索到的文档名
    docs_retrieved = _extract_doc_names(history, result)

    # 计算跨文档覆盖率
    docs_coverage = _calc_docs_coverage(
        docs_retrieved, query_record.get("expected_docs", [])
    )

    # tokens: 从 agent_history 的 observation 长度粗略估算 (字符/3)
    tokens = _estimate_tokens(history, result)

    return {
        "id": query_record["id"],
        "category": query_record["category"],
        "mode": mode,
        "query": query_record["query"],
        "answer": answer,
        "answer_completeness": "",  # 人工打分, 留空
        "docs_coverage": docs_coverage,
        "tokens": tokens,
        "latency_ms": elapsed_ms,
        "tool_calls": tool_calls,
        "tools_used": "|".join(tools_used) if tools_used else ("knowledge_qa" if mode == "oneshot" else ""),
        "docs_retrieved": "|".join(docs_retrieved) if docs_retrieved else "",
        "error": "",
    }


def _build_system_prompt(mode: str) -> str:
    """根据模式构建 system prompt"""
    base = "你是工时管理智能助手，专为企业工时管理系统服务。"
    if mode == "progressive":
        base += """

## 知识库检索策略（渐进式披露 / A-RAG）

1. 简单单文档问题（如"加班算工时吗"）：调 knowledge_qa 工具。
2. 复杂或跨文档问题（涉及多个制度、需要对比、多跳推理）：必须用渐进式检索工具 —
   - 先 kb_outline 看大纲
   - 再 kb_keyword_search 或 kb_semantic_search 找相关章节
   - 最后 kb_read_section 精读关键章节
   - 信息不够时继续 search/read，够了再生成回答

## 判断标准

问题出现以下信号时，走渐进式检索（kb_ 工具），不走 knowledge_qa：
- "和"、"同时"、"对比"、"区别"、"涉及哪些"等多文档关联词
- 问题涉及多个主题域（如加班+审批、产假+奖金、调休+离职）
- 需要枚举或列出（如"有哪些"、"几种"）
- 问题模糊，不确定该查哪篇文档"""
    return base


def _extract_answer(result: Dict[str, Any]) -> str:
    """从 graph 结果中提取最终答案文本"""
    # 优先取 rag_result (knowledge_qa 路径)
    rag = result.get("rag_result")
    if isinstance(rag, dict) and rag.get("success") and rag.get("response"):
        return rag["response"]

    # 取 llm_result (general_chat / summarize 路径)
    llm = result.get("llm_result")
    if llm:
        return llm

    # 取 tool_result 的摘要
    tool = result.get("tool_result")
    if isinstance(tool, dict):
        inner = tool.get("result", tool)
        if isinstance(inner, dict):
            return inner.get("message", inner.get("preview_text", str(inner)))
        return str(inner)

    return ""


def _extract_doc_names(history: List[Dict], result: Dict) -> List[str]:
    """从 agent_history 的 observation 里提取文档名

    处理两种情况:
    1. 原始 observation (kb_search / kb_outline / kb_read_section)
    2. 被 _truncate_observation 截断后的 observation ({_truncated: True, _preview: "..."})
       — 从 _preview 文本中用正则提取文件名
    """
    docs: set = set()
    for h in history:
        obs = h.get("observation")
        if not isinstance(obs, dict):
            continue

        # 情况 2: 截断的 observation, 从 _preview 中提取
        if obs.get("_truncated") and obs.get("_preview"):
            preview = obs["_preview"]
            # 匹配 "file": "..." 或 file: ... 模式
            import re
            for m in re.finditer(r'"file"\s*:\s*"([^"]+)"', preview):
                f = m.group(1)
                docs.add(Path(f).name.replace(".md", "").replace(".docx", "").replace(".pdf", ""))
            # 也匹配 kb_outline 的 documents 列表中的 title
            for m in re.finditer(r'"title"\s*:\s*"([^"]+)"', preview):
                t = m.group(1)
                docs.add(t.replace(".md", "").replace(".docx", "").replace(".pdf", ""))
            continue

        # 情况 1: 原始 observation
        # kb_* search 结果
        results = obs.get("results", obs.get("documents", []))
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict):
                    f = r.get("file") or r.get("title", "")
                    if f:
                        docs.add(Path(f).name.replace(".md", "").replace(".docx", "").replace(".pdf", ""))
        # kb_read_section 结果
        f = obs.get("file")
        if f:
            docs.add(Path(f).name.replace(".md", "").replace(".docx", "").replace(".pdf", ""))

    # oneshot / knowledge_qa 路径: 从 rag_result.sources 提取文档名
    rag = result.get("rag_result")
    if isinstance(rag, dict):
        for src in rag.get("sources", []):
            if isinstance(src, dict):
                s = src.get("source", "")
                if s:
                    docs.add(Path(s).name.replace(".md", "").replace(".docx", "").replace(".pdf", ""))

    return sorted(docs)


def _calc_docs_coverage(actual: List[str], expected: List[str]) -> float:
    """计算跨文档覆盖率: len(intersection) / len(expected)

    匹配策略: expected 文档名中任意 2 字子串出现在 actual 文档名中即算匹配。
    例如 expected="加班补偿政策" 匹配 actual="加班常见问题"(因"加班"命中)。
    这反映了"是否检索到了相关主题域的文档", 而非精确文件名匹配。
    """
    if not expected:
        return 1.0
    matched = 0
    for exp in expected:
        exp_norm = exp.replace(".md", "").replace(".docx", "").replace(".pdf", "")
        found = False
        for act in actual:
            act_norm = act.replace(".md", "").replace(".docx", "").replace(".pdf", "")
            # 检查 exp 中任意 2 字子串是否在 act 中
            for i in range(len(exp_norm) - 1):
                if exp_norm[i:i + 2] in act_norm:
                    found = True
                    break
            if found:
                break
        if found:
            matched += 1
    return matched / len(expected)


def _estimate_tokens(history: List[Dict], result: Dict) -> int:
    """粗略估算 tokens: 所有 observation 字符数 / 3 + answer 字符数 / 3"""
    total_chars = 0
    for h in history:
        obs = h.get("observation")
        try:
            s = json.dumps(obs, ensure_ascii=False, default=str)
        except Exception:
            s = str(obs)
        total_chars += len(s)

    # 加上 answer
    answer = _extract_answer(result)
    total_chars += len(answer)

    # 加上 conversation_history (system + user)
    total_chars += 500  # 粗略估计 prompt 开销

    return int(total_chars / 3)


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def _load_existing_results(csv_path: Path) -> List[Dict[str, Any]]:
    """加载已有 CSV 结果(用于断点续跑)"""
    if not csv_path.exists():
        return []
    rows = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 转换数字字段
                for k in ["docs_coverage", "tokens", "latency_ms", "tool_calls"]:
                    if row.get(k):
                        try:
                            row[k] = float(row[k]) if k == "docs_coverage" else int(row[k])
                        except ValueError:
                            pass
                rows.append(dict(row))
    except Exception as e:
        logger.warning(f"加载已有 CSV 失败: {e}, 将重新跑")
    return rows


async def run_benchmark(mode: str, smoke: bool = False, filter_ids: list = None):
    """运行评测(支持断点续跑)"""
    if not DATA_FILE.exists():
        logger.error(f"测试集不存在: {DATA_FILE}")
        sys.exit(1)

    # 加载测试集
    queries = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    if filter_ids:
        queries = [q for q in queries if q["id"] in filter_ids]
        logger.info(f"=== Filter 模式: {len(queries)} 条 query ===")
    elif smoke:
        # Smoke: S01 + M01 各跑 2 个模式 = 4 次
        queries = [q for q in queries if q["id"] in ("S01", "M01")]
        logger.info(f"=== Smoke 模式: {len(queries)} 条 query ===")
    else:
        logger.info(f"=== 全量测试: {len(queries)} 条 query ===")

    # 初始化
    await _init_rag()
    _init_agent_components()

    modes = ["oneshot", "progressive"] if mode == "both" else [mode]

    # 断点续跑: 加载已有结果
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "smoke" if smoke else "full"
    csv_path = RESULTS_DIR / f"progressive_rag_{date.today().isoformat()}_{suffix}.csv"
    existing = _load_existing_results(csv_path)
    done_keys = {(r.get("id"), r.get("mode")) for r in existing if not r.get("error")}
    if existing:
        logger.info(f"断点续跑: 已加载 {len(existing)} 条已有结果, 跳过 {len(done_keys)} 条成功记录")

    results: List[Dict[str, Any]] = list(existing)

    total = len(queries) * len(modes)
    idx = 0
    skipped = 0
    for query in queries:
        for m in modes:
            idx += 1
            if (query["id"], m) in done_keys:
                skipped += 1
                continue
            logger.info(f"[{idx}/{total}] id={query['id']} mode={m} query=\"{query['query']}\"")
            try:
                r = await run_one(query, m)
                results.append(r)
                logger.info(
                    f"  -> intent={r.get('tools_used', 'N/A')} "
                    f"tool_calls={r['tool_calls']} "
                    f"latency={r['latency_ms']}ms "
                    f"coverage={r['docs_coverage']:.1%} "
                    f"tokens={r['tokens']}"
                )
            except Exception as e:
                logger.error(f"  -> FAILED: {e}")
                results.append({
                    "id": query["id"],
                    "category": query["category"],
                    "mode": m,
                    "query": query["query"],
                    "answer": f"[FAILED] {e}",
                    "answer_completeness": "",
                    "docs_coverage": 0.0,
                    "tokens": 0,
                    "latency_ms": 0,
                    "tool_calls": 0,
                    "tools_used": "",
                    "docs_retrieved": "",
                    "error": str(e),
                })
            # 每条之间短暂休息, 避免 vLLM 过载
            if idx < total:
                await asyncio.sleep(1.0)

    if skipped:
        logger.info(f"跳过 {skipped} 条已有成功记录")

    fieldnames = [
        "id", "category", "mode", "query", "answer_completeness",
        "docs_coverage", "tokens", "latency_ms", "tool_calls",
        "tools_used", "docs_retrieved", "answer", "error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    logger.info(f"\n结果已保存: {csv_path}")

    # 打印汇总
    _print_summary(results)
    return results


def _print_summary(results: List[Dict[str, Any]]):
    """打印汇总统计"""
    print("\n" + "=" * 70)
    print("汇总统计")
    print("=" * 70)

    for mode in ["oneshot", "progressive"]:
        mode_results = [r for r in results if r["mode"] == mode]
        if not mode_results:
            continue

        ok_results = [r for r in mode_results if not r.get("error")]
        fail_count = len(mode_results) - len(ok_results)

        avg_latency = sum(r["latency_ms"] for r in ok_results) / len(ok_results) if ok_results else 0
        avg_tokens = sum(r["tokens"] for r in ok_results) / len(ok_results) if ok_results else 0
        avg_tool_calls = sum(r["tool_calls"] for r in ok_results) / len(ok_results) if ok_results else 0
        avg_coverage = sum(r["docs_coverage"] for r in ok_results) / len(ok_results) if ok_results else 0

        print(f"\n[{mode}] 样本: {len(mode_results)} 成功: {len(ok_results)} 失败: {fail_count}")
        print(f"  平均延迟: {avg_latency:.0f}ms")
        print(f"  平均 tokens: {avg_tokens:.0f}")
        print(f"  平均 tool_calls: {avg_tool_calls:.1f}")
        print(f"  平均 docs_coverage: {avg_coverage:.1%}")

    # 按类别拆分
    print("\n--- 按类别拆分 ---")
    categories = sorted(set(r["category"] for r in results))
    for cat in categories:
        for mode in ["oneshot", "progressive"]:
            cat_results = [r for r in results if r["category"] == cat and r["mode"] == mode and not r.get("error")]
            if cat_results:
                avg_cov = sum(r["docs_coverage"] for r in cat_results) / len(cat_results)
                avg_lat = sum(r["latency_ms"] for r in cat_results) / len(cat_results)
                avg_tc = sum(r["tool_calls"] for r in cat_results) / len(cat_results)
                print(f"  {cat:12s} {mode:11s}: coverage={avg_cov:.1%} latency={avg_lat:.0f}ms tool_calls={avg_tc:.1f}")

    print("=" * 70)


# ─── 人工打分模式 ─────────────────────────────────────────────────────────────

def run_mark_mode(csv_path: str):
    """逐条显示 query + answer + expected_points, 提示用户输入 0-10 分"""
    import shutil

    path = Path(csv_path)
    if not path.exists():
        logger.error(f"CSV 不存在: {csv_path}")
        sys.exit(1)

    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))

    # 加载 expected_points
    expected_map = {}
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    expected_map[rec["id"]] = rec.get("expected_points", [])

    # 逐条打分
    terminal_width = shutil.get_terminal_size().columns
    updated = 0
    for i, row in enumerate(rows):
        if row.get("answer_completeness"):
            continue  # 已有分数, 跳过

        qid = row["id"]
        print("\n" + "=" * min(70, terminal_width))
        print(f"[{i+1}/{len(rows)}] {qid} | mode={row['mode']} | category={row['category']}")
        print(f"Query: {row['query']}")
        print("-" * min(70, terminal_width))
        print(f"Answer:\n{row['answer'][:800]}{'...' if len(row['answer']) > 800 else ''}")
        print("-" * min(70, terminal_width))
        points = expected_map.get(qid, [])
        print(f"Expected points: {points}")
        print("-" * min(70, terminal_width))

        while True:
            score = input("请输入完整度评分 (0-10, 或 s 跳过, q 退出): ").strip()
            if score.lower() == "q":
                break
            if score.lower() == "s":
                break
            try:
                score_val = int(score)
                if 0 <= score_val <= 10:
                    row["answer_completeness"] = str(score_val)
                    updated += 1
                    break
                else:
                    print("评分必须在 0-10 之间")
            except ValueError:
                print("请输入数字或 s/q")

        if score.lower() == "q":
            break

    if updated > 0:
        # 写回 CSV
        fieldnames = list(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n已更新 {updated} 条评分, 保存到: {path}")
    else:
        print("\n没有新评分")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Progressive RAG 对比评测")
    parser.add_argument("--mode", choices=["oneshot", "progressive", "both"], default="both",
                        help="评测模式")
    parser.add_argument("--smoke", action="store_true", help="Smoke 模式, 只跑前 2 条")
    parser.add_argument("--filter-ids", type=str, help="只跑指定 ID, 逗号分隔, 如 S01,M01,L01")
    parser.add_argument("--mark-mode", action="store_true", help="人工打分模式")
    parser.add_argument("--csv", type=str, help="人工打分模式对应的 CSV 文件")
    args = parser.parse_args()

    if args.mark_mode:
        if not args.csv:
            # 自动找最新的 CSV
            csv_files = sorted(RESULTS_DIR.glob("progressive_rag_*.csv"))
            if csv_files:
                args.csv = str(csv_files[-1])
            else:
                logger.error("未找到 CSV 文件, 请用 --csv 指定")
                sys.exit(1)
        run_mark_mode(args.csv)
    else:
        filter_ids = args.filter_ids.split(",") if args.filter_ids else None
        asyncio.run(run_benchmark(mode=args.mode, smoke=args.smoke, filter_ids=filter_ids))


if __name__ == "__main__":
    main()
