"""
测试数据验证脚本

检查 tests/data/ 下所有 JSON 文件是否符合 docs/testing-plan.md 定义的 schema，
并输出每个文件的质量报告。

用法：
    python tests/validate_test_data.py              # 验证全部
    python tests/validate_test_data.py --fix        # 验证 + 自动修复可修复的问题
    python tests/validate_test_data.py --file tests/data/query_timesheet/query_self_today.json
"""

import json
import sys
import argparse
from pathlib import Path
from collections import Counter
from typing import Any

# ─── 合法值定义 ───────────────────────────────────────────────────────────────

VALID_INTENTS = {"tool_execution", "knowledge_qa", "general_chat", "clarify"}
VALID_TOOLS = {"query_timesheet", "save_workhour", "query_project", None}
VALID_ENTITY_TYPES = {"employee", "deptAdmin", "deptSubAdmin", "companyAdmin", "superAdmin"}
VALID_CATEGORIES = {
    "query_timesheet", "save_workhour", "query_project",
    "knowledge_qa", "general_chat", "edge_cases",
}

# sub_type → params_exists 中必须包含的字段
REQUIRED_PARAMS_EXISTS: dict[str, list[str]] = {
    "query_self_today": ["user_id"],
    "query_self_this_week": ["user_id"],
    "query_self_last_week": ["user_id"],
    "query_self_this_month": ["user_id"],
    "query_self_last_month": ["user_id"],
    "query_self_date_range": ["user_id"],
    "query_self_by_project": ["user_id"],
    "query_others_this_week": ["member_name"],
    "query_others_this_month": ["member_name"],
    "query_others_last_month": ["member_name"],
    "query_others_date_range": ["member_name"],
    "query_others_by_name": ["member_name"],
}

# sub_type → params 中必须包含的字段（精确匹配检查）
REQUIRED_PARAMS_KEYS: dict[str, list[str]] = {
    "fill_all_params": ["project_id", "date", "duration"],
    "fill_with_description": ["project_id", "date", "duration"],
}

# 各 sub_type 的期望最小条数（低于此值警告）
MIN_COUNTS: dict[str, int] = {
    "query_self_today": 50,
    "query_self_this_week": 80,
    "query_self_last_week": 60,
    "query_self_this_month": 80,
    "query_self_last_month": 80,
    "query_self_date_range": 60,
    "query_self_by_project": 50,
    "query_others_this_week": 50,
    "query_others_this_month": 50,
    "query_others_last_month": 60,
    "query_others_date_range": 40,
    "fill_all_params": 150,
    "fill_with_description": 100,
    "fill_missing_project": 70,
    "fill_missing_duration": 70,
    "fill_missing_date": 60,
}

# ─── 单条用例验证 ─────────────────────────────────────────────────────────────

def validate_case(case: dict[str, Any], index: int) -> list[str]:
    """
    验证单条测试用例，返回错误信息列表（空列表表示通过）。
    """
    errors: list[str] = []
    case_id = case.get("id", f"#index_{index}")

    def err(msg: str):
        errors.append(f"  [{case_id}] {msg}")

    # ── 顶层必填字段 ──
    for field in ["id", "category", "sub_type", "description", "input",
                  "user_context", "expected", "notes"]:
        if field not in case:
            err(f"缺少顶层字段: {field!r}")

    if not case.get("input", "").strip():
        err("input 为空字符串")

    # ── category ──
    cat = case.get("category", "")
    if cat not in VALID_CATEGORIES:
        err(f"category 值非法: {cat!r}，合法值: {VALID_CATEGORIES}")

    # ── user_context ──
    uctx = case.get("user_context", {})
    for field in ["entity_type", "user_id", "user_name", "department_id"]:
        if field not in uctx:
            err(f"user_context 缺少字段: {field!r}")
    if uctx.get("entity_type") not in VALID_ENTITY_TYPES:
        err(f"user_context.entity_type 非法: {uctx.get('entity_type')!r}")

    # ── expected ──
    exp = case.get("expected", {})
    for field in ["intent", "tool_name", "params", "params_fuzzy",
                  "params_exists", "date_relative"]:
        if field not in exp:
            err(f"expected 缺少字段: {field!r}")

    intent = exp.get("intent")
    if intent not in VALID_INTENTS:
        err(f"expected.intent 非法: {intent!r}")

    tool_name = exp.get("tool_name")
    if tool_name not in VALID_TOOLS:
        err(f"expected.tool_name 非法: {tool_name!r}")

    if not isinstance(exp.get("params", {}), dict):
        err("expected.params 必须是对象")

    if not isinstance(exp.get("params_fuzzy", []), list):
        err("expected.params_fuzzy 必须是数组")

    if not isinstance(exp.get("params_exists", []), list):
        err("expected.params_exists 必须是数组")

    # ── intent 与 tool_name 一致性 ──
    if intent == "tool_execution" and not tool_name:
        err("intent=tool_execution 时 tool_name 不能为 null")
    if intent in ("general_chat", "knowledge_qa") and tool_name:
        err(f"intent={intent} 时 tool_name 应为 null，实际: {tool_name!r}")

    # ── sub_type 业务规则 ──
    sub_type = case.get("sub_type", "")
    params_exists = set(exp.get("params_exists", []))
    params = exp.get("params", {})

    required_exists = REQUIRED_PARAMS_EXISTS.get(sub_type, [])
    for field in required_exists:
        if field not in params_exists and field not in params:
            err(f"sub_type={sub_type!r} 要求 params_exists 包含 {field!r}，"
                f"但 params_exists={list(params_exists)}，params.keys={list(params.keys())}")

    required_param_keys = REQUIRED_PARAMS_KEYS.get(sub_type, [])
    for field in required_param_keys:
        if field not in params:
            err(f"sub_type={sub_type!r} 的 params 缺少必填字段: {field!r}")

    # ── 日期逻辑 ──
    start = params.get("start_date", "")
    end = params.get("end_date", "")
    if start and end and start > end:
        err(f"日期区间错误: start_date={start!r} > end_date={end!r}")

    return errors


# ─── 文件级验证 ───────────────────────────────────────────────────────────────

def validate_file(path: Path, fix: bool = False) -> dict[str, Any]:
    """
    验证单个 JSON 文件，返回报告字典。
    fix=True 时自动修复可修复问题（去除重复 input，补全缺失的 params_fuzzy）。
    """
    report: dict[str, Any] = {
        "file": str(path.relative_to(Path(__file__).parent)),
        "count": 0,
        "errors": [],
        "warnings": [],
        "fixed": [],
    }

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        report["errors"].append(f"  JSON 解析失败: {e}")
        return report
    except Exception as e:
        report["errors"].append(f"  文件读取失败: {e}")
        return report

    if not isinstance(data, list):
        report["errors"].append("  文件根节点必须是 JSON 数组")
        return report

    report["count"] = len(data)

    # ── 重复 input 检测 ──
    input_counter = Counter(case.get("input", "") for case in data)
    dup_inputs = {inp: cnt for inp, cnt in input_counter.items() if cnt > 1}
    if dup_inputs:
        for inp, cnt in dup_inputs.items():
            report["warnings"].append(
                f"  重复 input（出现 {cnt} 次）: {inp!r}"
            )
        if fix:
            seen: set[str] = set()
            new_data = []
            for case in data:
                inp = case.get("input", "")
                if inp not in seen:
                    seen.add(inp)
                    new_data.append(case)
            removed = len(data) - len(new_data)
            data = new_data
            report["count"] = len(data)
            report["fixed"].append(f"  已去除 {removed} 条重复 input")

    # ── ID 重复检测 ──
    id_counter = Counter(case.get("id", "") for case in data)
    dup_ids = {id_: cnt for id_, cnt in id_counter.items() if cnt > 1}
    if dup_ids:
        for id_, cnt in dup_ids.items():
            report["errors"].append(f"  重复 id（出现 {cnt} 次）: {id_!r}")

    # ── 条数检查 ──
    sub_types = {case.get("sub_type") for case in data}
    for sub_type in sub_types:
        min_count = MIN_COUNTS.get(sub_type)
        if min_count and report["count"] < min_count:
            report["warnings"].append(
                f"  sub_type={sub_type!r} 当前 {report['count']} 条，"
                f"目标 {min_count} 条，还需补充 {min_count - report['count']} 条"
            )

    # ── 逐条验证 ──
    for i, case in enumerate(data):
        # 自动补全缺失的 params_fuzzy（fix 模式）
        if fix and "expected" in case and "params_fuzzy" not in case["expected"]:
            case["expected"]["params_fuzzy"] = []
            report["fixed"].append(f"  [{case.get('id', i)}] 补全 expected.params_fuzzy=[]")

        errors = validate_case(case, i)
        report["errors"].extend(errors)

    # ── fix 模式写回 ──
    if fix and report["fixed"]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return report


# ─── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="验证 AI 助手测试数据质量")
    parser.add_argument("--fix", action="store_true", help="自动修复可修复的问题")
    parser.add_argument("--file", type=str, help="只验证指定文件")
    args = parser.parse_args()

    data_dir = Path(__file__).parent / "data"

    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(data_dir.rglob("*.json"))

    if not files:
        print("未找到任何 JSON 文件")
        sys.exit(0)

    total_cases = 0
    total_errors = 0
    total_warnings = 0
    all_ok = True

    print(f"\n{'=' * 60}")
    print(f"  测试数据验证报告{'（修复模式）' if args.fix else ''}")
    print(f"{'=' * 60}\n")

    for path in files:
        report = validate_file(path, fix=args.fix)
        total_cases += report["count"]
        total_errors += len(report["errors"])
        total_warnings += len(report["warnings"])

        status = "[OK]" if not report["errors"] else "[FAIL]"
        warn_tag = f" [{len(report['warnings'])} warnings]" if report["warnings"] else ""
        print(f"{status} {report['file']}  ({report['count']} 条){warn_tag}")

        for w in report["warnings"]:
            print(f"\033[33m{w}\033[0m")  # 黄色

        for e in report["errors"]:
            print(f"\033[31m{e}\033[0m")  # 红色
            all_ok = False

        for f in report["fixed"]:
            print(f"\033[32m{f}\033[0m")  # 绿色

        if report["errors"] or report["warnings"] or report["fixed"]:
            print()

    print(f"{'─' * 60}")
    print(f"  总计: {total_cases} 条用例  |  "
          f"错误: {total_errors}  |  警告: {total_warnings}")
    print(f"{'─' * 60}\n")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
