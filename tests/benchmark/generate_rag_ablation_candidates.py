#!/usr/bin/env python3
"""Generate a stratified candidate annotation set for RAG ablation.

The LLM writes candidate questions/fact summaries. Source document and chunk
identifiers are assigned by this program and must be confirmed by a reviewer
before the blind evaluation set is frozen.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fastapi-service"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_rag_recall import (  # noqa: E402
    _load_documents_from_dir,
    _split_documents,
)

OUTPUT = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.jsonl"
MANIFEST = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.manifest.json"
LLM_URL = os.getenv("RAG_DATASET_LLM_URL", "http://172.19.3.136:8099/v1/chat/completions")
LLM_MODEL = os.getenv("RAG_DATASET_LLM_MODEL", "qwen3-8b")
SEED = 20260806

CATEGORY_COUNTS = {
    "direct_fact": 30,
    "synonym_rewrite": 30,
    "proper_noun_code": 30,
    "long_tail": 30,
    "multi_constraint": 30,
    "multi_hop": 30,
    "unanswerable": 20,
}

CATEGORY_INSTRUCTIONS = {
    "direct_fact": "提出一个能从材料中直接定位答案的事实问题，措辞清晰简洁。",
    "synonym_rewrite": "提出一个同义改写问题，避免复用材料里的核心原词，但含义必须一致。",
    "proper_noun_code": "围绕材料里的制度名、项目名、编号、接口名、日期或专有名词提问。",
    "long_tail": "使用真实用户可能采用的口语、缩写、轻微错别字或低频说法提问，但仍应可理解。",
    "multi_constraint": "问题必须同时包含至少两个约束，例如角色、时间、条件、流程状态或业务范围。",
    "multi_hop": "问题必须需要联合材料 A 和材料 B 才能完整回答，不得仅靠其中一段。",
    "unanswerable": "提出一个与业务主题相关、看似合理，但给定材料没有可靠答案的问题。不要暗示材料已有答案。",
}


def _norm_source(source: str) -> str:
    path = Path(source)
    try:
        return path.resolve().relative_to((ROOT / "knowledge-base").resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _chunk_id(source: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}\n{index}\n{text}".encode("utf-8")).hexdigest()[:12]
    return f"chunk_{digest}"


def _clean_text(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("LLM response does not contain a JSON array")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, list):
        raise ValueError("LLM response is not a JSON array")
    return value


def _call_llm(category: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    material = []
    for i, task in enumerate(tasks):
        entry = {"index": i, "material_a": task["text_a"]}
        if task.get("text_b"):
            entry["material_b"] = task["text_b"]
        material.append(entry)

    prompt = f"""你正在构造中文 RAG 检索评测候选数据。
类别：{category}
要求：{CATEGORY_INSTRUCTIONS[category]}

只返回 JSON 数组，每项严格包含：
{{"index":整数,"question":"问题","must_answer_facts":["材料明确支持的简短事实"]}}

规则：
1. 不得在问题中泄露答案，不得虚构具体数字、制度或流程。
2. 除 unanswerable 外，每个事实必须由给定材料直接支持。
3. multi_hop 的事实列表必须同时覆盖材料 A 和 B。
4. unanswerable 的 must_answer_facts 必须为空数组。
5. 每个 index 恰好输出一次。

材料：
{json.dumps(material, ensure_ascii=False)}"""
    response = requests.post(
        LLM_URL,
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": "你是严谨的 RAG 评测数据标注助手。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1800,
        },
        timeout=120,
    )
    response.raise_for_status()
    return _extract_json_array(response.json()["choices"][0]["message"]["content"])


def _build_tasks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        domain = chunk["source"].split("/", 1)[0]
        by_domain.setdefault(domain, []).append(chunk)

    tasks: list[dict[str, Any]] = []
    single_categories = [c for c in CATEGORY_COUNTS if c not in {"multi_hop", "unanswerable"}]
    for category in single_categories:
        selected = rng.sample(chunks, CATEGORY_COUNTS[category])
        for chunk in selected:
            negatives = [x for x in by_domain[chunk["source"].split("/", 1)[0]] if x["source"] != chunk["source"]]
            negative = rng.choice(negatives or [x for x in chunks if x["source"] != chunk["source"]])
            tasks.append({"category": category, "a": chunk, "negative": negative, "text_a": chunk["text"]})

    for _ in range(CATEGORY_COUNTS["multi_hop"]):
        domain = rng.choice([d for d, values in by_domain.items() if len({x["source"] for x in values}) >= 2])
        a = rng.choice(by_domain[domain])
        candidates = [x for x in by_domain[domain] if x["source"] != a["source"]]
        b = rng.choice(candidates)
        tasks.append({"category": "multi_hop", "a": a, "b": b, "negative": None, "text_a": a["text"], "text_b": b["text"]})

    selected = rng.sample(chunks, CATEGORY_COUNTS["unanswerable"])
    for chunk in selected:
        tasks.append({"category": "unanswerable", "a": chunk, "negative": None, "text_a": chunk["text"]})
    return tasks


def _generate_batch(category: str, batch: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            generated = _call_llm(category, batch)
            by_index = {int(item["index"]): item for item in generated}
            if set(by_index) != set(range(len(batch))):
                raise ValueError(f"Unexpected indexes for {category}: {sorted(by_index)}")
            return [(task, by_index[i]) for i, task in enumerate(batch)]
        except Exception as exc:
            last_error = exc
            time.sleep(attempt + 1)

    if len(batch) > 1:
        rows = []
        for task in batch:
            rows.extend(_generate_batch(category, [task]))
        return rows

    task = batch[0]
    topic = task["a"]["source"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    question = f"关于{topic}，现有材料能否说明这一问题？"
    if category == "unanswerable":
        question = f"{topic}在境外分支机构执行时有哪些特殊例外？"
    return [(task, {
        "question": question,
        "must_answer_facts": [] if category == "unanswerable" else [task["a"]["text"][:120]],
        "fallback": True,
        "error": str(last_error),
    })]


def main() -> None:
    raw_docs = _load_documents_from_dir(str(ROOT / "knowledge-base"))
    split = _split_documents(raw_docs)
    per_source_index: dict[str, int] = {}
    chunks: list[dict[str, Any]] = []
    for doc in split:
        text = _clean_text(doc.page_content)
        if len(text) < 100:
            continue
        source = _norm_source(str(doc.metadata.get("source", "")))
        index = per_source_index.get(source, 0)
        per_source_index[source] = index + 1
        chunks.append({"source": source, "chunk_id": _chunk_id(source, index, text), "text": text})

    tasks = _build_tasks(chunks)
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for category in CATEGORY_COUNTS:
        category_tasks = [task for task in tasks if task["category"] == category]
        batches.extend((category, category_tasks[i : i + 5]) for i in range(0, len(category_tasks), 5))

    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_generate_batch, category, batch): (category, len(batch)) for category, batch in batches}
        for future in as_completed(futures):
            category, size = futures[future]
            rows = future.result()
            completed.extend(rows)
            print(f"generated category={category} batch={size} total={len(completed)}/{len(tasks)}", flush=True)

    category_order = {name: i for i, name in enumerate(CATEGORY_COUNTS)}
    completed.sort(key=lambda pair: (category_order[pair[0]["category"]], pair[0]["a"]["chunk_id"], pair[1]["question"]))
    counters = {category: 0 for category in CATEGORY_COUNTS}
    records = []
    for task, generated in completed:
        category = task["category"]
        counters[category] += 1
        a, b = task["a"], task.get("b")
        relevant_docs = [] if category == "unanswerable" else [a["source"]] + ([b["source"]] if b else [])
        relevant_chunks = [] if category == "unanswerable" else [a["chunk_id"]] + ([b["chunk_id"]] if b else [])
        records.append({
            "case_id": f"{category}_{counters[category]:03d}",
            "category": category,
            "question": str(generated["question"]).strip(),
            "relevant_doc_ids": relevant_docs,
            "relevant_chunk_ids": relevant_chunks,
            "must_answer_facts": [] if category == "unanswerable" else generated.get("must_answer_facts", []),
            "hard_negative_doc_ids": [task["negative"]["source"]] if task.get("negative") else [],
            "is_multi_hop": category == "multi_hop",
            "should_refuse": category == "unanswerable",
            "split": "unassigned",
            "review_status": "pending_human_review",
            "generator": {
                "model": LLM_MODEL,
                "seed": SEED,
                "fallback": bool(generated.get("fallback", False)),
            },
        })

    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    manifest = {
        "status": "candidate_pending_human_review",
        "count": len(records),
        "category_counts": counters,
        "knowledge_base_markdown_files": len(list((ROOT / "knowledge-base").rglob("*.md"))),
        "eligible_chunks": len(chunks),
        "chunking": {"chunk_size": 512, "chunk_overlap": 50},
        "llm_url": LLM_URL,
        "llm_model": LLM_MODEL,
        "seed": SEED,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
