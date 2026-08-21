"""Repair semantic inconsistencies in the reviewed business-completion set.

The migration is deterministic and idempotent.  It replaces unobservable
fixture IDs with semantic references, aligns relative dates with production
calendar semantics, derives successful Mock payloads from expected parameters,
and broadens response contracts without changing product-policy decisions such
as whether an incomplete request should clarify or call a tool.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
FILES = ("single_turn_320.jsonl", "multi_turn_100.jsonl")
REVISION = "semantic-consistency-v2"
WRITE_TOOLS = {"save_workhour", "batch_save_workhour", "approve_workhour"}

PERSON_NAMES = (
    "李明", "王芳", "张伟", "陈静", "张三", "李四", "王五", "赵六",
    "陈七", "刘九", "赵强", "李雷", "韩梅梅",
)
PROJECT_NAMES = (
    "ERP升级v1", "CRM系统升级", "xinyun-platform", "星云平台", "星雲平台",
    "智慧园区", "ERP升级", "ERP升級", "AI平台", "数据中台", "智能客服",
)
PROJECT_ID_HINTS = {
    "P101": "星云平台", "p_001": "星云平台", "P2026001": "星云平台",
    "proj_starcloud": "星云平台", "P2001": "星云平台",
    "P202": "智慧园区", "p_002": "智慧园区", "proj_smartpark": "智慧园区",
    "P2002": "智慧园区", "P2026003": "智慧园区",
    "P303": "ERP升级", "p_003": "ERP升级", "P2026002": "ERP升级",
    "proj_erp_v2": "ERP升级", "proj_erp_v1": "ERP升级v1",
}
SELF_MARKERS = ("我", "本人", "自己", "我们")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_in_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def names_in(text: str) -> list[str]:
    return unique_in_order([name for name in PERSON_NAMES if name in text])


def projects_in(text: str) -> list[str]:
    values = [name for name in PROJECT_NAMES if name in text]
    # Prefer the longest label when one name contains another (ERP升级v1).
    return unique_in_order(sorted(values, key=lambda item: text.find(item)))


def departments_in(text: str) -> list[str]:
    matches = re.findall(r"(?:研发一部|研发二部|研发部|市场部|运营部|技术一部|技术二部|财务部)", text)
    return unique_in_order(matches)


def symbolic_value(kind: str, label: str) -> str:
    return f"$resolve_{kind}:{label}"


def is_grounded(value: Any, history: str, context: dict[str, Any]) -> bool:
    text = str(value)
    return (
        text.startswith("$")
        or text in history
        or text == str(context.get("user_id", ""))
        or text == str(context.get("department_id", ""))
    )


def choose_user_label(text: str, history: str, position: int) -> str | None:
    current_names = names_in(text)
    if current_names:
        return current_names[min(position, len(current_names) - 1)]
    historical_names = names_in(history)
    if historical_names and any(mark in text for mark in ("他", "她", "这个人", "该员工")):
        return historical_names[-1]
    return None


def choose_project_label(text: str, history: str, value: str, position: int) -> str | None:
    if value in PROJECT_ID_HINTS:
        return PROJECT_ID_HINTS[value]
    current = projects_in(text)
    if current:
        return current[min(position, len(current) - 1)]
    historical = projects_in(history)
    if historical and any(mark in text for mark in ("这个项目", "该项目", "之前的项目", "它")):
        return historical[-1]
    return None


def normalize_identity_params(
    tools: list[dict[str, Any]],
    *,
    text: str,
    history: str,
    context: dict[str, Any],
) -> int:
    changes = 0
    user_position = project_position = department_position = 0
    for tool in tools:
        params = tool.get("params") or {}
        for key in ("user_id", "project_id", "department_id", "org_id"):
            value = params.get(key)
            if value in (None, "") or is_grounded(value, history + " " + text, context):
                continue
            old_value = str(value)
            replacement: str | None = None
            if key == "user_id":
                label = choose_user_label(text, history, user_position)
                if label:
                    replacement = symbolic_value("user", label)
                    user_position += 1
                elif any(mark in text for mark in SELF_MARKERS) or not names_in(history + " " + text):
                    replacement = "$current_user"
                else:
                    replacement = symbolic_value("user", old_value)
            elif key == "project_id":
                label = choose_project_label(text, history, old_value, project_position)
                replacement = symbolic_value("project", label or old_value)
                project_position += 1
            else:
                labels = departments_in(text) or departments_in(history)
                label = labels[min(department_position, len(labels) - 1)] if labels else old_value
                replacement = symbolic_value("department", label)
                department_position += 1
            if replacement != value:
                params[key] = replacement
                changes += 1
        tool["params"] = params
        rules = dict(tool.get("match_rules") or {})
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                rules[key] = "semantic_reference"
        if tool["name"] == "batch_save_workhour":
            rules["text"] = "business_payload_equivalent"
        if tool["name"] in WRITE_TOOLS and params.get("dry_run") is True:
            rules["dry_run"] = "effective_value"
        if rules:
            tool["match_rules"] = rules
    return changes


def relative_range(text: str, reference_date: str) -> tuple[str, str] | None:
    try:
        today = date.fromisoformat(reference_date)
    except ValueError:
        return None
    monday = today - timedelta(days=today.weekday())
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    match = re.search(r"(上周|上週|上个周|上個週)([一二三四五六日天])", text)
    if match:
        target = monday - timedelta(days=7) + timedelta(days=weekday_map[match.group(2)])
        return target.isoformat(), target.isoformat()
    match = re.search(r"(本周|这周|這週)([一二三四五六日天])", text)
    if match:
        target = monday + timedelta(days=weekday_map[match.group(2)])
        return target.isoformat(), target.isoformat()
    if "最近两周" in text or "最近兩週" in text:
        return (monday - timedelta(days=7)).isoformat(), (monday + timedelta(days=6)).isoformat()
    if any(mark in text for mark in ("本周", "这周", "這週")):
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if any(mark in text for mark in ("上周", "上週", "上个周", "上個週")):
        return (monday - timedelta(days=7)).isoformat(), (monday - timedelta(days=1)).isoformat()
    if any(mark in text for mark in ("下周", "下週")):
        return (monday + timedelta(days=7)).isoformat(), (monday + timedelta(days=13)).isoformat()
    if any(mark in text for mark in ("本月", "这个月", "這個月")):
        start = today.replace(day=1)
        if any(mark in text for mark in ("至今", "到现在", "到現在", "截至今天")):
            end = today
        else:
            next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
            end = next_month - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    return None


def normalize_dates(tools: list[dict[str, Any]], text: str, reference_date: str) -> int:
    date_range = relative_range(text, reference_date)
    if not date_range:
        return 0
    start, end = date_range
    changes = 0
    for tool in tools:
        params = tool.get("params") or {}
        if "start_date" in params and params["start_date"] != start:
            params["start_date"] = start
            changes += 1
        if "end_date" in params and params["end_date"] != end:
            params["end_date"] = end
            changes += 1
    return changes


def display_label(value: Any, fallback: str) -> str:
    text = str(value or "")
    if ":" in text and text.startswith("$"):
        return text.split(":", 1)[1]
    return text or fallback


def success_mock(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "query_timesheet":
        project = display_label(params.get("project_id"), "测试项目")
        record_date = str(params.get("start_date") or params.get("end_date") or "2026-08-07")
        return {
            "success": True,
            "total_hours": 8.0,
            "record_count": 1,
            "records": [{"date": record_date, "project_name": project, "duration": 8.0}],
        }
    if tool_name == "compute_statistics":
        subject = display_label(
            params.get("project_id") or params.get("department_id") or params.get("user_id"),
            "当前用户",
        )
        return {"success": True, "total_hours": 40.0, "items": [{"name": subject, "total_hours": 40.0}]}
    if tool_name == "query_project":
        project_id = params.get("project_id") or "test_project_001"
        return {
            "success": True,
            "projects": [{
                "project_id": project_id,
                "project_name": display_label(project_id, "测试项目"),
                "status": "active",
            }],
        }
    if tool_name == "generate_weekly_report":
        return {
            "success": True,
            "week": params.get("week") or "thisWeek",
            "total_hours": 40.0,
            "report": "本周完成需求分析、开发与测试工作。",
        }
    if tool_name == "save_workhour":
        return {
            "success": True,
            "dry_run": bool(params.get("dry_run", True)),
            "preview": {
                "project_name": display_label(params.get("project_id"), "测试项目"),
                "date": params.get("date"),
                "duration": params.get("duration"),
                "description": params.get("description", ""),
            },
            "message": "预览成功，尚未写入",
        }
    if tool_name == "batch_save_workhour":
        return {"success": True, "dry_run": True, "preview_text": "批量工时解析完成，尚未写入"}
    if tool_name == "approve_workhour":
        ids = params.get("workhour_ids") or []
        ids = [ids] if isinstance(ids, str) else list(ids)
        return {"success": True, "approved_count": len(ids), "workhour_ids": ids, "message": "Mock审核成功"}
    if tool_name == "export_report":
        return {"success": True, "file_name": "workhour_mock.xlsx", "size_bytes": 2048, "message": "Mock报表已生成"}
    if tool_name == "suggest_workhour":
        return {"success": True, "suggested_projects": [{"project_id": "test_project_001", "project_name": "测试项目"}], "suggested_hours": 8.0}
    if tool_name == "knowledge_qa":
        return {"success": True, "answer": "根据知识库，工时应按实际投入及时填报。", "sources": ["workhour-policy.md"]}
    if tool_name == "kb_outline":
        return {"success": True, "documents": [{"file": "workhour-policy.md", "sections": ["核心规则", "补填要求"]}]}
    if tool_name in {"kb_keyword_search", "kb_semantic_search"}:
        return {"success": True, "results": [{"file": "workhour-policy.md", "section": "核心规则", "content": "工时应按实际投入填报。"}]}
    if tool_name == "kb_read_section":
        return {
            "success": True,
            "file": params.get("file") or "workhour-policy.md",
            "section": params.get("section") or "核心规则",
            "content": "工时应按实际投入及时填报。",
        }
    return {"success": True}


def failure_mock(outcome: str) -> dict[str, Any] | None:
    if outcome == "permission_denied":
        return {"success": False, "error_code": "PERMISSION_DENIED", "message": "无权执行该操作"}
    if outcome in {"timeout", "timeout_unknown"}:
        return {"success": False, "error_code": "TIMEOUT", "message": "请求超时，结果未知"}
    if outcome == "connection_lost_unknown":
        return {"success": False, "error_code": "CONNECTION_LOST", "message": "连接中断，提交结果未知，请查询确认"}
    if outcome in {"tool_error", "validation_error"}:
        return {
            "success": False,
            "error_code": "VALIDATION_ERROR" if outcome == "validation_error" else "UPSTREAM_ERROR",
            "message": "工具执行失败",
        }
    if outcome == "empty_result":
        return {"success": True, "empty": True, "items": [], "message": "未查询到符合条件的数据"}
    return None


def refresh_mocks(unit: dict[str, Any]) -> int:
    tools = unit.get("expected_tools") or []
    outcome = unit.get("tool_outcome", "success")
    old = unit.get("mock_tool_results") or []
    mocks = []
    for index, tool in enumerate(tools):
        selected_outcome = "partial_failure" if outcome == "partial_failure" and index == len(tools) - 1 else outcome
        result = failure_mock(selected_outcome)
        if selected_outcome == "partial_failure":
            result = {"success": False, "partial": True, "message": "该子任务执行失败"}
        if result is None:
            result = success_mock(tool["name"], tool.get("params") or {})
        mocks.append({"tool": tool["name"], "result": result})
    unit["mock_tool_results"] = mocks
    return int(old != mocks)


def relaxed_contract(action: str, outcome: str) -> dict[str, Any]:
    if action == "clarify":
        return {
            "status": "clarification_required",
            "must_include_any": ["请提供", "请补充", "请确认", "需要", "无法确定", "具体"],
            "must_not_include_any": ["系统已成功保存", "已完成填报", "写入成功", "提交成功"],
        }
    if action == "direct":
        return {"status": "completed", "must_include_any": [], "must_not_include_any": ["系统已成功保存", "已写入"]}
    if action == "rag":
        return {"status": "completed", "must_include_any": [], "must_not_include_any": ["根据个人工时数据", "系统已成功保存"]}
    if outcome in {"timeout_unknown", "connection_lost_unknown"}:
        return {
            "status": "unknown",
            "must_include_any": ["结果未知", "结果暂不确定", "无法确认", "查询确认", "确认是否", "核实"],
            "must_not_include_any": ["系统已成功保存", "写入成功", "提交成功", "已完成填报"],
        }
    if outcome in {"permission_denied", "tool_error", "validation_error", "timeout"}:
        return {
            "status": "failed",
            "must_include_any": ["失败", "无权", "超时", "未完成", "未成功", "无法", "异常", "不能"],
            "must_not_include_any": ["系统已成功保存", "写入成功", "提交成功", "已完成填报"],
        }
    if outcome == "partial_failure":
        return {
            "status": "partially_completed",
            "must_include_any": ["部分", "失败", "未完成", "未成功"],
            "must_not_include_any": ["全部完成", "全部写入成功"],
        }
    if outcome == "empty_result":
        return {
            "status": "completed_empty",
            "must_include_any": ["未查询到", "暂无", "没有符合", "无数据", "没有数据", "为空"],
            "must_not_include_any": ["系统已成功保存"],
        }
    if outcome == "dry_run_success":
        return {
            "status": "preview_ready",
            "must_include_any": ["预览", "确认", "预检", "试运行", "尚未写入", "未实际保存"],
            "must_not_include_any": ["系统已成功保存", "写入成功", "提交成功", "已完成填报"],
        }
    return {"status": "completed", "must_include_any": [], "must_not_include_any": ["执行失败"]}


def repair_unit(
    unit: dict[str, Any],
    *,
    text: str,
    history: str,
    context: dict[str, Any],
    reference_date: str,
) -> Counter:
    counts: Counter = Counter()
    tools = unit.get("expected_tools") or []
    counts["identity_references"] += normalize_identity_params(
        tools, text=text, history=history, context=context
    )
    counts["relative_dates"] += normalize_dates(tools, text, reference_date)
    unit["expected_tools"] = tools
    unit["expected_params"] = [tool.get("params") or {} for tool in tools]
    counts["mock_payloads"] += refresh_mocks(unit)
    contract_key = "expected_final_state" if "input" in unit else "expected_response"
    new_contract = relaxed_contract(unit.get("expected_action", "direct"), unit.get("tool_outcome", "not_applicable"))
    if unit.get(contract_key) != new_contract:
        unit[contract_key] = new_contract
        counts["response_contracts"] += 1
    return counts


def repair_cases(single: list[dict[str, Any]], multi: list[dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    for case in single:
        counts.update(repair_unit(
            case,
            text=case["input"],
            history="",
            context=case["user_context"],
            reference_date=case.get("reference_date", "2026-08-07"),
        ))
        prior_review = case.pop("human_reviewed_at", None)
        if prior_review:
            case["previous_human_reviewed_at"] = prior_review
        case["review_status"] = "pending_human_review"
        case["semantic_revision"] = REVISION
    for case in multi:
        history = ""
        for turn in case.get("turns", []):
            text = turn.get("user_input", "")
            counts.update(repair_unit(
                turn,
                text=text,
                history=history,
                context=case["user_context"],
                reference_date=case.get("reference_date", "2026-08-07"),
            ))
            history += " " + text
        if case.get("turns"):
            case["expected_final_state"] = case["turns"][-1]["expected_response"]
        prior_review = case.pop("human_reviewed_at", None)
        if prior_review:
            case["previous_human_reviewed_at"] = prior_review
        case["review_status"] = "pending_human_review"
        case["semantic_revision"] = REVISION
    return counts


def audit_changes(
    original_single: list[dict[str, Any]],
    original_multi: list[dict[str, Any]],
    repaired_single: list[dict[str, Any]],
    repaired_multi: list[dict[str, Any]],
) -> Counter:
    """Count material differences against the immutable pre-fix archive."""
    counts: Counter = Counter()

    def compare_units(before: dict[str, Any], after: dict[str, Any]) -> None:
        before_tools = before.get("expected_tools") or []
        after_tools = after.get("expected_tools") or []
        for old_tool, new_tool in zip(before_tools, after_tools):
            old_params = old_tool.get("params") or {}
            new_params = new_tool.get("params") or {}
            for key in ("user_id", "project_id", "department_id", "org_id"):
                if old_params.get(key) != new_params.get(key):
                    counts["identity_references"] += 1
            for key in ("start_date", "end_date", "date"):
                if old_params.get(key) != new_params.get(key):
                    counts["relative_dates"] += 1
        if before.get("mock_tool_results") != after.get("mock_tool_results"):
            counts["mock_payloads"] += 1
        old_contract = before.get("expected_final_state") or before.get("expected_response") or {}
        new_contract = after.get("expected_final_state") or after.get("expected_response") or {}
        if old_contract != new_contract:
            counts["response_contracts"] += 1

    for before, after in zip(original_single, repaired_single):
        compare_units(before, after)
    for before_case, after_case in zip(original_multi, repaired_multi):
        for before_turn, after_turn in zip(before_case.get("turns", []), after_case.get("turns", [])):
            compare_units(before_turn, after_turn)
    return counts


def main() -> int:
    single_path = DATA_DIR / FILES[0]
    multi_path = DATA_DIR / FILES[1]
    single = read_jsonl(single_path)
    multi = read_jsonl(multi_path)
    archive = DATA_DIR / "archive" / "pre-semantic-consistency-v2"
    archive.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        destination = archive / name
        if not destination.exists():
            shutil.copy2(DATA_DIR / name, destination)
    before = {name: sha256(archive / name) for name in FILES}

    repair_cases(single, multi)
    write_jsonl(single_path, single)
    write_jsonl(multi_path, multi)
    counts = audit_changes(
        read_jsonl(archive / FILES[0]),
        read_jsonl(archive / FILES[1]),
        single,
        multi,
    )
    after = {name: sha256(DATA_DIR / name) for name in FILES}
    repaired_at = datetime.now(timezone.utc).isoformat()

    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "semantic_fix_pending_human_review"
    manifest["semantic_revision"] = {
        "version": REVISION,
        "repaired_at": repaired_at,
        "changes": dict(counts),
        "archive": str(archive.relative_to(DATA_DIR)).replace("\\", "/"),
        "scope_excluded": ["clarify-versus-default product policy"],
    }
    manifest["human_review"] = {
        "approved": False,
        "reason": "semantic revision changed gold expectations; re-review required",
    }
    for name, rows in ((FILES[0], single), (FILES[1], multi)):
        manifest["files"][name] = {"rows": len(rows), "sha256": after[name]}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "revision": REVISION,
        "repaired_at": repaired_at,
        "files": {name: {"before_sha256": before[name], "after_sha256": after[name]} for name in FILES},
        "changes": dict(counts),
        "rows_pending_human_review": len(single) + len(multi),
        "archive": str(archive.relative_to(DATA_DIR)).replace("\\", "/"),
    }
    (DATA_DIR / "semantic_repair_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
