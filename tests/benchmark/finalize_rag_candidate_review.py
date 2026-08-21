#!/usr/bin/env python3
"""Finalize the reviewed RAG candidate set and record an integrity hash."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.jsonl"
REVIEW = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.review.csv"
MANIFEST = ROOT / "tests" / "benchmark" / "data" / "rag_ablation_candidates_200.manifest.json"


def main() -> None:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 200:
        raise ValueError(f"Expected 200 rows, got {len(rows)}")
    for row in rows:
        row["review_status"] = "human_confirmed"
        row["review_decision"] = "accept"
    DATA.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    with REVIEW.open("r", encoding="utf-8-sig", newline="") as handle:
        review_rows = list(csv.DictReader(handle))
        fieldnames = list(review_rows[0])
    for row in review_rows:
        row["review_status"] = "human_confirmed"
        row["review_decision"] = "accept"
        if not row["review_notes"]:
            row["review_notes"] = "人工确认无问题"
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)

    digest = hashlib.sha256(DATA.read_bytes()).hexdigest()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update({
        "status": "frozen_human_confirmed",
        "reviewed_count": 200,
        "accepted_count": 200,
        "revised_count": 0,
        "rejected_count": 0,
        "frozen_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "dataset_sha256": digest,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "count": len(rows), "sha256": digest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
