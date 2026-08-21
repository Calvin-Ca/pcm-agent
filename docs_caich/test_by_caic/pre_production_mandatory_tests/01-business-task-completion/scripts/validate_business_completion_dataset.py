"""Validate business-completion data against the production ToolRegistry."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent
REPO_ROOT = HERE.parents[3]
SERVICE_ROOT = REPO_ROOT / "fastapi-service"
DATA_DIR = HERE / "data"
sys.path.insert(0, str(SERVICE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)

import app.tools  # noqa: E402,F401
from app.services.tool_registry import EXPECTED_TOOL_NAMES, tool_registry  # noqa: E402
from generate_business_completion_dataset import (  # noqa: E402
    read_jsonl,
    validate_dataset,
)


def main() -> int:
    single = read_jsonl(DATA_DIR / "single_turn_320.jsonl")
    multi = read_jsonl(DATA_DIR / "multi_turn_100.jsonl")
    structural = validate_dataset(single, multi)
    schema_errors: list[dict] = []
    checked_calls = 0

    for file_name, cases in (("single_turn_320.jsonl", single), ("multi_turn_100.jsonl", multi)):
        for case in cases:
            turn_sets = [case] if file_name.startswith("single") else case.get("turns", [])
            for turn in turn_sets:
                for tool in turn.get("expected_tools", []):
                    checked_calls += 1
                    valid, error = tool_registry.validate_params(tool["name"], tool.get("params") or {})
                    if not valid:
                        schema_errors.append({
                            "case_id": case["case_id"],
                            "turn": turn.get("turn"),
                            "tool": tool["name"],
                            "error": error,
                            "params": tool.get("params") or {},
                        })

    actual_tools = set(structural["counts"]["tool_coverage"])
    missing_tools = sorted(set(EXPECTED_TOOL_NAMES) - actual_tools)
    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "valid": structural["valid"] and not schema_errors and not missing_tools,
        "structural_errors": structural["errors"],
        "schema_checked_tool_calls": checked_calls,
        "schema_errors": schema_errors,
        "registered_tool_count": len(EXPECTED_TOOL_NAMES),
        "missing_registered_tools": missing_tools,
        "counts": structural["counts"],
        "warnings": structural["warnings"],
    }
    report_path = DATA_DIR / "registry_validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Keep the manifest's structural-validation snapshot aligned with the
    # currently frozen files (including their human-review status).
    manifest["validation"] = {
        "valid": structural["valid"],
        "errors": structural["errors"],
        "warnings": structural["warnings"],
        "counts": structural["counts"],
    }
    manifest["registry_validation"] = {
        "valid": report["valid"],
        "schema_checked_tool_calls": checked_calls,
        "schema_error_count": len(schema_errors),
        "missing_registered_tools": missing_tools,
        "report": report_path.name,
    }
    if not report["valid"]:
        manifest["status"] = "invalid"
    elif manifest.get("human_review", {}).get("approved"):
        manifest["status"] = "human_review_approved"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
