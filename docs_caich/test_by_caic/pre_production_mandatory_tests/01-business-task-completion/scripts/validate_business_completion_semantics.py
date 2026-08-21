"""Validate semantic consistency beyond JSON Schema correctness."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repair_business_completion_dataset_semantics import (
    DATA_DIR,
    FILES,
    display_label,
    is_grounded,
    read_jsonl,
    relative_range,
)


def add_error(errors: list[dict[str, Any]], case_id: str, turn: int | None, code: str, detail: Any) -> None:
    errors.append({"case_id": case_id, "turn": turn, "code": code, "detail": detail})


def validate_unit(
    case: dict[str, Any],
    unit: dict[str, Any],
    *,
    history: str,
    text: str,
    errors: list[dict[str, Any]],
    counts: Counter,
) -> None:
    case_id = case["case_id"]
    turn_no = unit.get("turn")
    tools = unit.get("expected_tools") or []
    if unit.get("expected_params") != [tool.get("params") or {} for tool in tools]:
        add_error(errors, case_id, turn_no, "expected_params_out_of_sync", unit.get("expected_params"))

    date_range = relative_range(text, case.get("reference_date", "2026-08-07"))
    for tool in tools:
        params = tool.get("params") or {}
        for key in ("user_id", "project_id", "department_id", "org_id"):
            value = params.get(key)
            if value not in (None, "") and not is_grounded(value, history + " " + text, case["user_context"]):
                add_error(errors, case_id, turn_no, "ungrounded_identity", {"tool": tool["name"], "key": key, "value": value})
        if date_range:
            start, end = date_range
            if "start_date" in params and params["start_date"] != start:
                add_error(errors, case_id, turn_no, "relative_start_date_mismatch", {"actual": params["start_date"], "expected": start})
            if "end_date" in params and params["end_date"] != end:
                add_error(errors, case_id, turn_no, "relative_end_date_mismatch", {"actual": params["end_date"], "expected": end})

    mocks = unit.get("mock_tool_results") or []
    if [mock.get("tool") for mock in mocks] != [tool.get("name") for tool in tools]:
        add_error(errors, case_id, turn_no, "mock_tool_order_mismatch", [mock.get("tool") for mock in mocks])
    for tool, mock in zip(tools, mocks):
        params = tool.get("params") or {}
        result = mock.get("result") or {}
        if not result.get("success") or result.get("empty"):
            continue
        name = tool["name"]
        if name == "save_workhour":
            preview = result.get("preview") or {}
            for key in ("date", "duration", "description"):
                expected = params.get(key, "" if key == "description" else None)
                if preview.get(key) != expected:
                    add_error(errors, case_id, turn_no, "save_preview_mismatch", {"key": key, "expected": expected, "actual": preview.get(key)})
            expected_project = display_label(params.get("project_id"), "测试项目")
            if preview.get("project_name") != expected_project:
                add_error(errors, case_id, turn_no, "save_project_mismatch", {"expected": expected_project, "actual": preview.get("project_name")})
        elif name == "approve_workhour":
            ids = params.get("workhour_ids") or []
            ids = [ids] if isinstance(ids, str) else list(ids)
            if result.get("workhour_ids") != ids or result.get("approved_count") != len(ids):
                add_error(errors, case_id, turn_no, "approval_result_mismatch", result)
        elif name == "query_project":
            projects = result.get("projects") or []
            if projects and projects[0].get("project_id") != (params.get("project_id") or "test_project_001"):
                add_error(errors, case_id, turn_no, "query_project_result_mismatch", projects[0])
        elif name == "generate_weekly_report":
            if result.get("week") != (params.get("week") or "thisWeek"):
                add_error(errors, case_id, turn_no, "weekly_report_week_mismatch", result.get("week"))
        elif name == "query_timesheet":
            start = params.get("start_date")
            end = params.get("end_date")
            records = result.get("records") or []
            if start and end and any(not (start <= str(record.get("date")) <= end) for record in records):
                add_error(errors, case_id, turn_no, "timesheet_record_out_of_range", records)
            duration_sum = sum(float(record.get("duration", 0)) for record in records)
            if float(result.get("total_hours", 0)) != duration_sum or int(result.get("record_count", 0)) != len(records):
                add_error(errors, case_id, turn_no, "timesheet_aggregate_mismatch", result)
        elif name == "batch_save_workhour" and "record_count" in result:
            add_error(errors, case_id, turn_no, "unverifiable_batch_record_count", result.get("record_count"))

    contract = unit.get("expected_final_state") or unit.get("expected_response") or {}
    broad_forbidden = {"已成功", "已完成"} & set(contract.get("must_not_include_any") or [])
    if broad_forbidden:
        add_error(errors, case_id, turn_no, "overbroad_forbidden_phrase", sorted(broad_forbidden))
    counts["validated_turns"] += 1
    counts["validated_tools"] += len(tools)


def main() -> int:
    single = read_jsonl(DATA_DIR / FILES[0])
    multi = read_jsonl(DATA_DIR / FILES[1])
    errors: list[dict[str, Any]] = []
    counts: Counter = Counter()
    for case in single:
        validate_unit(case, case, history="", text=case["input"], errors=errors, counts=counts)
    for case in multi:
        history = ""
        for turn in case.get("turns", []):
            text = turn.get("user_input", "")
            validate_unit(case, turn, history=history, text=text, errors=errors, counts=counts)
            history += " " + text

    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "counts": dict(counts),
        "review_status": Counter(case.get("review_status") for case in single + multi),
    }
    report["review_status"] = dict(report["review_status"])
    report_path = DATA_DIR / "semantic_validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["semantic_validation"] = {
        "valid": report["valid"],
        "error_count": len(errors),
        "report": report_path.name,
    }
    if errors:
        manifest["status"] = "invalid"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
