#!/usr/bin/env python3
"""
LLM-judge 统一重判 completeness（盲评）

用法:
  python judge_completeness.py

输入: 三份 CSV（8B基准 / 14B新 / 3.5-plus基准）
输出: results/judge_2026-05-16.csv
"""

import csv
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Dict, List

import httpx

# ─── 配置 ─────────────────────────────────────────────────────────────────────
RESULTS_DIR = Path(__file__).with_suffix("").parent / "results"
JUDGE_CSV = RESULTS_DIR / "judge_2026-05-16.csv"

# key 来源：项目根 .env 的 PLANNER_LLM_API_KEY / PLANNER_LLM_API_BASE
ENV_ROOT = Path(__file__).resolve().parents[3] / ".env"
PLANNER_LLM_API_KEY = ""
PLANNER_LLM_API_BASE = ""

if ENV_ROOT.exists():
    with open(ENV_ROOT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("PLANNER_LLM_API_KEY="):
                PLANNER_LLM_API_KEY = line.split("=", 1)[1]
            elif line.startswith("PLANNER_LLM_API_BASE="):
                PLANNER_LLM_API_BASE = line.split("=", 1)[1]
else:
    print(f"[FATAL] 项目根 .env 不存在: {ENV_ROOT}")
    sys.exit(1)

JUDGE_API_BASE = PLANNER_LLM_API_BASE or "https://dashscope.aliyuncs.com/compatible-mode/v1"
JUDGE_MODEL = "qwen3.6-plus"

# 三份输入 CSV
CSV_SOURCES = {
    "8B": RESULTS_DIR / "progressive_rag_2026-05-05_qwen3-8b.csv",
    "14B": RESULTS_DIR / "progressive_rag_2026-05-16_qwen3-14b-awq-marlin.csv",
    "3.5-plus": RESULTS_DIR / "progressive_rag_2026-05-05_qwen3.5-plus.csv",
}

# 数据集（含 expected_points）
DATASET_FILE = Path(__file__).with_suffix("").parent / "data" / "progressive_rag_eval_18.jsonl"

# ─── 加载数据 ─────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_judge_items() -> List[Dict]:
    """构建盲评条目: 每条是一个 (id, mode, query) 下的三方 answer"""
    sources_data = {k: load_csv(v) for k, v in CSV_SOURCES.items()}

    # 从数据集加载 expected_points
    expected_map = {}
    with open(DATASET_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                expected_map[d["id"]] = d.get("expected_points", [])

    # 以 8B 为基准，收集所有 (id, mode)
    items = []
    for row in sources_data["8B"]:
        # 收集三方的 answer
        answers = {}
        for src, rows in sources_data.items():
            match = next((r for r in rows if r["id"] == row["id"] and r["mode"] == row["mode"]), None)
            if match:
                answers[src] = match["answer"]

        if len(answers) == 3:
            pts = expected_map.get(row["id"], [])
            items.append({
                "id": row["id"],
                "category": row["category"],
                "mode": row["mode"],
                "query": row["query"],
                "expected_points": " | ".join(pts),
                "answers": answers,
            })
    return items


# ─── 裁判调用 ─────────────────────────────────────────────────────────────────

JUDGE_PROMPT = """你是公正的评测裁判。请根据 expected_points 对给定的 answer 打分。

评分标准（0-10 分）：
- 每命中一个 expected_points 中的要点，按比例给分
- 答非所问或有明显幻觉：≤3 分
- 完全正确且无冗余：9-10 分
- 部分正确但有遗漏：4-8 分

IMPORTANT: 只输出一个 0-10 的整数分数，不要输出任何其他文字、解释或格式。
Example good output: 7
Example bad output: "我认为这个答案是7分" 或 JSON 或 markdown

Query: {query}
Expected points: {expected_points}
Answer: {answer}

Score (0-10 only):"""


def _call_api(api_base: str, model: str, prompt: str, api_key: str = "") -> str:
    """调用 OpenAI-compatible API，返回 content"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 256,
    }
    resp = httpx.post(
        f"{api_base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_judge_response(content: str) -> Dict:
    """从裁判响应中提取数字分数（0-10），处理 think 标签和各种格式"""
    import re

    cleaned = content.strip()

    # 1. 去除 <think> 区块
    if "<think>" in cleaned:
        if "</think>" in cleaned:
            cleaned = cleaned.split("</think>", 1)[1].strip()
        else:
            lines = cleaned.split("\n")
            after_think = []
            in_think = False
            for line in lines:
                if "<think>" in line:
                    in_think = True
                    continue
                if "</think>" in line:
                    in_think = False
                    continue
                if not in_think:
                    after_think.append(line)
            cleaned = "\n".join(after_think).strip()

    # 2. 去除 markdown 代码块
    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1].strip()

    # 3. 尝试直接解析为数字
    cleaned_nums = re.sub(r'[^\d\-\.]', '', cleaned)
    if cleaned_nums:
        try:
            score = int(float(cleaned_nums))
            if 0 <= score <= 10:
                return {"score": score, "hit_points": "", "reason": "direct_parse"}
        except ValueError:
            pass

    # 4. 找最后一行独立的数字
    lines = [l.strip() for l in cleaned.split("\n") if l.strip()]
    for line in reversed(lines):
        m = re.match(r'^(\d{1,2})(\s*$|\\.\s*$)', line)
        if m:
            score = int(m.group(1))
            if 0 <= score <= 10:
                return {"score": score, "hit_points": "", "reason": "line_match"}

    # 5. 找文本中第一个 0-10 的数字
    nums = re.findall(r'\b(\d{1,2})\b', cleaned)
    for n in nums:
        n_int = int(n)
        if 0 <= n_int <= 10:
            return {"score": n_int, "hit_points": "", "reason": "number_match"}

    raise ValueError(f"无法提取分数: {content[:200]}")


def judge_one(query: str, expected_points: str, answer: str) -> Dict:
    """调用裁判模型对单条 answer 打分。失败即抛异常，不 fallback。"""
    prompt = JUDGE_PROMPT.format(
        query=query,
        expected_points=expected_points,
        answer=answer[:4000],
    )

    for attempt in range(3):
        try:
            content = _call_api(JUDGE_API_BASE, JUDGE_MODEL, prompt, PLANNER_LLM_API_KEY)
            return _parse_judge_response(content)
        except Exception as e:
            print(f"  裁判失败 (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(1)
            else:
                raise

    # 理论上不会到这里（上面已 raise）
    raise RuntimeError("judge_one 全部重试失败")


# ─── 启动探针硬门 ─────────────────────────────────────────────────────────────

def probe_judge_api() -> None:
    """启动前探针：验证裁判 API key 和模型可用性。非 200 直接 exit。"""
    if not PLANNER_LLM_API_KEY:
        print("[FATAL] PLANNER_LLM_API_KEY 为空，无法启动裁判。请检查项目根 .env")
        sys.exit(1)

    print(f"[PROBE] 探针裁判 API: {JUDGE_API_BASE}  model={JUDGE_MODEL}")
    prompt = "请回复数字 5。"
    try:
        content = _call_api(JUDGE_API_BASE, JUDGE_MODEL, prompt, PLANNER_LLM_API_KEY)
        print(f"[PROBE] 响应: {content.strip()[:50]}")
        # 简单验证响应中包含 5
        if "5" in content:
            print("[PROBE] PASS 探针通过，继续评测")
            return
        else:
            print(f"[FATAL] 探针响应异常（未含预期数字 5）: {content[:200]}")
            sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"[FATAL] 探针 HTTP 错误: {e.response.status_code}")
        try:
            body = e.response.text[:500]
            print(f"[FATAL] 响应体: {body}")
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] 探针异常: {type(e).__name__}: {e}")
        sys.exit(1)


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    print(f"裁判模型: {JUDGE_MODEL}")
    print(f"API Base: {JUDGE_API_BASE}")
    print(f"Key 来源: {ENV_ROOT}")
    print(f"Key 前缀: {PLANNER_LLM_API_KEY[:10]}...")

    # 探针硬门
    probe_judge_api()

    print(f"\n加载三份 CSV...")
    items = build_judge_items()
    print(f"共 {len(items)} 条评测条目")

    # 盲评：为每条 (id, mode) 打乱三方 answer 顺序送评
    results = []
    for idx, item in enumerate(items, 1):
        print(f"\n[{idx}/{len(items)}] {item['id']} {item['mode']}: {item['query'][:40]}...")

        # 打乱顺序
        sources = list(item["answers"].keys())
        random.shuffle(sources)

        for src in sources:
            answer = item["answers"][src]
            print(f"  判 {src}...", end="", flush=True)

            try:
                result = judge_one(
                    query=item["query"],
                    expected_points=item["expected_points"],
                    answer=answer,
                )
            except Exception as e:
                print(f" [FATAL] 裁判调用失败: {e}")
                sys.exit(1)

            result["source"] = src  # 判分后才回填来源
            result["id"] = item["id"]
            result["category"] = item["category"]
            result["mode"] = item["mode"]
            result["query"] = item["query"]
            results.append(result)
            print(f" score={result['score']} ({result['reason']})")

    # 保存结果
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(JUDGE_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "category", "mode", "source", "score", "hit_points", "reason", "query"],
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[OK] Judge 完成: {JUDGE_CSV}")

    # 汇总统计
    print("\n=== 分数汇总 ===")
    for mode in ["oneshot", "progressive"]:
        print(f"\n{mode}:")
        for src in ["8B", "14B", "3.5-plus"]:
            scores = [
                r["score"] for r in results
                if r["mode"] == mode and r["source"] == src
            ]
            if scores:
                avg = sum(scores) / len(scores)
                print(f"  {src}: avg={avg:.2f} (n={len(scores)})")


if __name__ == "__main__":
    main()
