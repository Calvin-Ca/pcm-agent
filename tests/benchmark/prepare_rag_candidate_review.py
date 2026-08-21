#!/usr/bin/env python3
"""Prepare the generated RAG candidates for human review and stratified split."""

from __future__ import annotations

import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import generate_rag_ablation_candidates as generator  # noqa: E402

DATA = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.jsonl"
MANIFEST = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.manifest.json"
REVIEW = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.review.csv"
SEED = 20260806


def build_chunk_map() -> dict[str, str]:
    raw = generator._load_documents_from_dir(str(ROOT / "knowledge-base"))
    split = generator._split_documents(raw)
    per_source: dict[str, int] = {}
    result: dict[str, str] = {}
    for doc in split:
        text = generator._clean_text(doc.page_content)
        if len(text) < 100:
            continue
        source = generator._norm_source(str(doc.metadata.get("source", "")))
        index = per_source.get(source, 0)
        per_source[source] = index + 1
        result[generator._chunk_id(source, index, text)] = text
    return result


def assign_splits(rows: list[dict]) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)
    rng = random.Random(SEED)
    for category, values in grouped.items():
        rng.shuffle(values)
        count = len(values)
        dev_end = round(count * 0.2)
        validation_end = dev_end + round(count * 0.2)
        for index, row in enumerate(values):
            row["split"] = "development" if index < dev_end else "validation" if index < validation_end else "blind_test"


def main() -> None:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    assign_splits(rows)
    rows.sort(key=lambda row: row["case_id"])
    DATA.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    chunks = build_chunk_map()
    fields = [
        "case_id", "split", "category", "question", "relevant_doc_ids",
        "relevant_chunk_ids", "source_excerpt", "must_answer_facts",
        "hard_negative_doc_ids", "is_multi_hop", "should_refuse",
        "review_status", "review_decision", "review_notes",
    ]
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            excerpts = [chunks.get(chunk_id, "[missing chunk]") for chunk_id in row["relevant_chunk_ids"]]
            writer.writerow({
                "case_id": row["case_id"],
                "split": row["split"],
                "category": row["category"],
                "question": row["question"],
                "relevant_doc_ids": json.dumps(row["relevant_doc_ids"], ensure_ascii=False),
                "relevant_chunk_ids": json.dumps(row["relevant_chunk_ids"], ensure_ascii=False),
                "source_excerpt": "\n---\n".join(excerpts),
                "must_answer_facts": json.dumps(row["must_answer_facts"], ensure_ascii=False),
                "hard_negative_doc_ids": json.dumps(row["hard_negative_doc_ids"], ensure_ascii=False),
                "is_multi_hop": row["is_multi_hop"],
                "should_refuse": row["should_refuse"],
                "review_status": row["review_status"],
                "review_decision": "",
                "review_notes": "",
            })

    split_counts = Counter(row["split"] for row in rows)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["split_seed"] = SEED
    manifest["split_counts"] = dict(split_counts)
    manifest["review_file"] = str(REVIEW.relative_to(ROOT).as_posix())
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(rows), "split_counts": dict(split_counts), "review_file": str(REVIEW)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
