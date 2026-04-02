"""
精度统计报告生成器

解析 pytest --json-report 生成的 report.json，
按 category / sub_type 分层输出精度统计表。

用法：
    pytest tests/test_classification_accuracy.py \
        --json-report --json-report-file=reports/layer1.json
    python tests/utils/accuracy_reporter.py reports/layer1.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


# 精度目标（低于此值标注警告）
ACCURACY_TARGETS: dict[str, float] = {
    "query_timesheet":  0.95,
    "save_workhour":    0.95,
    "query_project":    0.90,
    "knowledge_qa":     0.98,
    "general_chat":     0.98,
    "edge_cases":       0.80,
    "_overall":         0.92,
}


def _parse_test_id(node_id: str) -> tuple[str, str]:
    """
    从 pytest node_id 中提取 category 和 sub_type。
    node_id 格式：tests/test_classification_accuracy.py::test_intent_classification[qt_001]
    用例 id 格式：qt_001 / qsw_001 / gc_001 等
    """
    # 取方括号内的用例 id
    if "[" in node_id and "]" in node_id:
        case_id = node_id.split("[")[1].rstrip("]")
    else:
        case_id = node_id

    # 前缀映射到 category（由 tests/data/ 下各 JSON 文件的 id 字段首段决定）
    prefix_map = {
        # edge_cases / general_chat / knowledge_qa / query_project
        "ec":   "edge_cases",
        "gc":   "general_chat",
        "kq":   "knowledge_qa",
        "qp":   "query_project",
        # query_timesheet — self
        "qt":   "query_timesheet",
        "qst":  "query_timesheet",   # query_self_today
        "qsw":  "query_timesheet",   # query_self_this_week
        "qsl":  "query_timesheet",   # query_self_last_week
        "qsm":  "query_timesheet",   # query_self_this_month
        "qspm": "query_timesheet",   # query_self_last_month
        "qsdr": "query_timesheet",   # query_self_date_range
        "qsbp": "query_timesheet",   # query_self_by_project
        # query_timesheet — others
        "qot":  "query_timesheet",   # query_others_today
        "qotw": "query_timesheet",   # query_others_this_week
        "qolm": "query_timesheet",   # query_others_last_month
        "qol":  "query_timesheet",
        "qobm": "query_timesheet",   # query_others_by_member
        "qobp": "query_timesheet",   # query_others_by_project
        # save_workhour — all variants & batches
        "sw":   "save_workhour",
        "swh":  "save_workhour",     # batch2/3/4/5 + remaining
        "swhs": "save_workhour",     # save_workhour_simple
        "swhm": "save_workhour",     # save_workhour_multi_days
        "swhp": "save_workhour",     # save_workhour_with_project
        "swhr": "save_workhour",     # save_workhour_with_remark
    }
    prefix = case_id.split("_")[0].lower()
    category = prefix_map.get(prefix, "unknown")
    return category, case_id


def generate_report(report_path: str | Path) -> None:
    path = Path(report_path)
    if not path.exists():
        print(f"报告文件不存在: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        report = json.load(f)

    tests = report.get("tests", [])
    if not tests:
        print("报告中没有测试结果")
        return

    # ── 统计 ──
    # category → {total, passed}
    cat_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0, "failed_ids": []})
    overall = {"total": 0, "passed": 0}

    for test in tests:
        outcome = test.get("outcome", "")  # passed / failed / error
        node_id = test.get("nodeid", "")
        category, case_id = _parse_test_id(node_id)

        cat_stats[category]["total"] += 1
        overall["total"] += 1

        if outcome == "passed":
            cat_stats[category]["passed"] += 1
            overall["passed"] += 1
        else:
            cat_stats[category]["failed_ids"].append(case_id)

    # ── 输出 ──
    col_w = 36
    print(f"\n{'─' * 62}")
    print(f"  {'类别':<{col_w}} {'总数':>5}  {'通过':>5}  {'精度':>7}")
    print(f"{'─' * 62}")

    for category in sorted(cat_stats.keys()):
        s = cat_stats[category]
        total = s["total"]
        passed = s["passed"]
        acc = passed / total if total else 0
        target = ACCURACY_TARGETS.get(category, 0.90)
        flag = "  <== 低于目标" if acc < target else ""
        print(f"  {category:<{col_w}} {total:>5}  {passed:>5}  {acc:>6.1%}{flag}")

    print(f"{'─' * 62}")
    overall_acc = overall["passed"] / overall["total"] if overall["total"] else 0
    overall_target = ACCURACY_TARGETS["_overall"]
    overall_flag = "  <== 低于目标" if overall_acc < overall_target else ""
    print(f"  {'总体':<{col_w}} {overall['total']:>5}  {overall['passed']:>5}  {overall_acc:>6.1%}{overall_flag}")
    print(f"{'─' * 62}\n")

    # ── 失败模式 Top 10 ──
    all_failed = []
    for s in cat_stats.values():
        all_failed.extend(s["failed_ids"])

    if all_failed:
        print(f"  失败用例（共 {len(all_failed)} 条，前 10）：")
        for fid in all_failed[:10]:
            print(f"    - {fid}")
        if len(all_failed) > 10:
            print(f"    ... 还有 {len(all_failed) - 10} 条，查看完整报告：{path}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python accuracy_reporter.py <report.json>")
        sys.exit(1)
    generate_report(sys.argv[1])
