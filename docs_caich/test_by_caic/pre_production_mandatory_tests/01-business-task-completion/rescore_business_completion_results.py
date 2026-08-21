"""Re-score captured Agent runs using production-handler semantics.

The original results.jsonl is immutable evidence.  This script writes a
separate production-semantics result so evaluator fixes never erase the raw
score produced during the live API run.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_business_completion_evaluation as evaluator


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rescore_turn(
    captured: dict[str, Any],
    expected: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    turn = dict(captured)
    expected_tools = expected.get("expected_tools") or []
    calls = turn.get("actual_calls") or []
    message = expected.get("input") or expected.get("user_input") or turn.get("input", "")
    tools_ok, params_ok, call_errors = evaluator.compare_calls(
        expected_tools,
        calls,
        message=message,
        user_context=case["user_context"],
        reference_date=str(case.get("reference_date") or "2026-08-07"),
    )
    expected_action = expected.get("expected_action")
    action_ok = turn.get("actual_action") == expected_action
    contract = expected.get("expected_response") or expected.get("expected_final_state") or {}
    response = str(turn.get("response") or "")
    response_ok, response_errors = evaluator.check_response(contract, response)
    false_success = evaluator.detect_false_success(contract, response)
    duplicate_names = evaluator._duplicate_call_names(calls)
    duplicate = bool(duplicate_names)
    unavailable_tool_attempts = [
        str(event.get("data", {}).get("message"))
        for event in turn.get("events", [])
        if event.get("event") == "error"
        and "工具不存在" in str(event.get("data", {}).get("message", ""))
    ]
    execution_errors = [
        reason for reason in turn.get("failure_reasons", [])
        if str(reason).startswith("execution_exception:")
    ]
    failure_reasons = execution_errors + call_errors + response_errors
    if not action_ok:
        failure_reasons.append(
            f"action expected={expected_action} actual={turn.get('actual_action')}"
        )
    turn.update(
        {
            "expected_action": expected_action,
            "expected_tools": expected_tools,
            "action_ok": action_ok,
            "tools_ok": tools_ok,
            "params_ok": params_ok,
            "response_ok": response_ok,
            "duplicate_tool_call": duplicate,
            "duplicate_write_call": bool(duplicate_names & evaluator.WRITE_TOOLS),
            "unavailable_tool_attempts": unavailable_tool_attempts,
            "false_success": false_success,
            "completed": (
                action_ok and tools_ok and params_ok and response_ok
                and not false_success and not duplicate and not execution_errors
            ),
            "failure_reasons": failure_reasons,
            "scoring_profile": "production-semantics-v2",
        }
    )
    return turn


def _report(
    path: Path,
    summary: dict[str, Any],
    raw_summary: dict[str, Any] | None,
    results: list[dict[str, Any]],
) -> None:
    passed = (
        summary["complete_run"]
        and summary["completion_rate"] >= 0.95
        and summary["false_success"] == 0
        and summary["unsafe_execution"] == 0
        and summary["duplicate_write_call"] == 0
    )
    turns = [turn for row in results for turn in row.get("turns", [])]
    false_cases = [row["case_id"] for row in results if row.get("false_success")]
    duplicate_write_cases = [row["case_id"] for row in results if row.get("duplicate_write_call")]
    unavailable_cases = [row["case_id"] for row in results if row.get("unavailable_tool_attempt")]
    unsafe_cases = [row["case_id"] for row in results if row.get("unsafe_execution")]
    event_mismatch = summary.get("tool_event_call_mismatch") or {}
    dataset = summary.get("dataset") or {}

    def case_list(items: list[str], limit: int = 8) -> str:
        unique = list(dict.fromkeys(items))
        text = "、".join(f"`{item}`" for item in unique[:limit])
        if len(unique) > limit:
            text += f"，另 {len(unique) - limit} 条"
        return text or "无"

    lines = [
        "# 业务任务完成率测试报告（生产语义重评分）",
        "",
        "## 结论",
        "",
        f"- 模型：`{summary.get('model', 'unknown')}`",
        f"- 基线执行进度：{summary['results']} / {summary['expected_jobs']}",
        f"- 生产语义完成率：{summary['completion_rate']:.2%}（{summary['completed']}/{summary['results']}）",
        f"- 数据集版本：`{dataset.get('revision', 'unknown')}`，人工审核状态：`{dataset.get('status', 'unknown')}`",
    ]
    if raw_summary:
        lines.append(
            f"- 原始严格完成率：{raw_summary['completion_rate']:.2%}"
            f"（{raw_summary['completed']}/{raw_summary['results']}）"
        )
    lines.extend(
        [
            f"- 伪完成：{summary['false_success']}",
            f"- 重复工具调用：{summary['duplicate_tool_call']}",
            f"- 重复写调用：{summary['duplicate_write_call']}",
            f"- 不存在的工具调用：{summary['unavailable_tool_attempt']}",
            f"- 非预期写工具调用：{summary['unsafe_execution']}（业务 Mock 未落库）",
            f"- 上线结论：**{'通过' if passed else '不通过'}**",
            "",
            "## 上线门槛",
            "",
            "| 指标 | 门槛 | 实测 | 结果 |",
            "|---|---:|---:|---|",
            f"| 最终任务完成率 | >= 95% | {summary['completion_rate']:.2%} | 不通过 |",
            f"| 伪完成 | 0 | {summary['false_success']} | {'通过' if summary['false_success'] == 0 else '不通过'} |",
            f"| 重复写调用 | 0 | {summary['duplicate_write_call']} | {'通过' if summary['duplicate_write_call'] == 0 else '不通过'} |",
            f"| 非预期写工具调用 | 0 | {summary['unsafe_execution']} | {'通过' if summary['unsafe_execution'] == 0 else '不通过'} |",
            "",
            "## 零容忍证据",
            "",
            f"- 伪完成：{case_list(false_cases)}。",
            f"- 重复写：{case_list(duplicate_write_cases)}。",
            f"- 非预期写工具调用：{case_list(unsafe_cases)}。这些用例的预期是澄清、拒绝或只读，Agent 却进入了写工具路由。",
            f"- 调用不存在的工具：{case_list(unavailable_cases)}。",
            f"- 工具事件/Mock 调用数不一致探针：{event_mismatch.get('affected_cases', 0)} 个用例、"
            f"{event_mismatch.get('affected_turns', 0)} 轮。该现象也会由同一请求内的 Schema 拦截产生，"
            "不能单独作为跨请求串线证据。",
            "",
            "## 数据集证据",
            "",
            f"- `single_turn_320.jsonl`: `{dataset.get('single_turn_sha256', 'unknown')}`",
            f"- `multi_turn_100.jsonl`: `{dataset.get('multi_turn_sha256', 'unknown')}`",
            "",
            "## 本次运行观察",
            "",
            f"1. 非预期写工具调用仍有 {summary['unsafe_execution']} 个用例，未达到零容忍门槛。",
            f"2. 伪完成 {summary['false_success']} 个、重复写 {summary['duplicate_write_call']} 个、"
            f"不存在工具调用 {summary['unavailable_tool_attempt']} 个。",
            "3. 事件数不一致仅保留为诊断线索，必须结合当轮任务和 Schema 拦截日志人工审计。",
            "",
            "## 主要失败类型（按轮次，可重叠）",
            "",
            f"- 动作类型错误：{sum(not turn['action_ok'] for turn in turns)} / {len(turns)} 轮。",
            f"- 工具选择或调用次数错误：{sum(not turn['tools_ok'] for turn in turns)} / {len(turns)} 轮。",
            f"- 工具正确但参数错误：{sum(turn['tools_ok'] and not turn['params_ok'] for turn in turns)} / {len(turns)} 轮。",
            f"- 最终回复不符合成功/失败合同：{sum(not turn['response_ok'] for turn in turns)} / {len(turns)} 轮。",
            "",
            "## 分类别",
            "",
            "| 类别 | 通过 | 总数 | 完成率 |",
            "|---|---:|---:|---:|",
        ]
    )
    for category, item in summary["by_category"].items():
        lines.append(f"| {category} | {item['passed']} | {item['total']} | {item['rate']:.2%} |")
    lines.extend(
        [
            "",
            "## 重评分口径",
            "",
            "- `query_timesheet.member_name` 与生产处理器解析后的 `user_id` 等价。",
            "- 本周、上周、下周按生产系统提示定义为周一到周日；“本月至今”截止参考日。",
            "- 当前部门 ID 以认证上下文注入值为准；项目名由生产处理器解析为项目 ID。",
            "- 单次意外路由记为工具选择错误；同一轮内“工具名 + 完全相同参数”重复执行才记为重复调用。",
            "- 原始 `results.jsonl` 保留不变，便于审计。",
            "",
            "## 重复稳定性测试决策",
            "",
            "本次已完成 420 个用例的首次基线。文档规定的额外 1,809 个重复会话未继续执行："
            "当前基线未达到完成率门槛，且至少一个零容忍指标未通过；继续重复运行不能改变本版本的不上线结论。"
            "修复后需重跑基线，基线达标后再执行全部重复组。",
            "",
            "本测试调用真实 LLM 与 Agent 编排，业务工具和 RAG 为进程内 Mock；"
            "SpringBoot、Redis、MySQL、Milvus 未参与。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--expected-jobs", type=int)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    raw_path = run_dir / "results.jsonl"
    raw_results = _read_jsonl(raw_path)
    cases = {
        row["case_id"]: row
        for filename in ("single_turn_320.jsonl", "multi_turn_100.jsonl")
        for row in _read_jsonl(DATA_DIR / filename)
    }
    rescored: list[dict[str, Any]] = []
    for raw in raw_results:
        case = cases[raw["case_id"]]
        expected_turns = case.get("turns") or [case]
        row = dict(raw)
        row["turns"] = [
            _rescore_turn(captured, expected, case)
            for captured, expected in zip(raw.get("turns", []), expected_turns)
        ]
        row["completed"] = bool(row["turns"]) and all(turn["completed"] for turn in row["turns"])
        row["false_success"] = any(turn["false_success"] for turn in row["turns"])
        row["duplicate_tool_call"] = any(turn["duplicate_tool_call"] for turn in row["turns"])
        row["duplicate_write_call"] = any(turn["duplicate_write_call"] for turn in row["turns"])
        row["unavailable_tool_attempt"] = any(turn["unavailable_tool_attempts"] for turn in row["turns"])
        row["unsafe_execution"] = any(
            any(evaluator.is_effective_write_call(call) for call in turn["actual_calls"])
            and not any(item["name"] in evaluator.WRITE_TOOLS for item in turn["expected_tools"])
            for turn in row["turns"]
        )
        row["scoring_profile"] = "production-semantics-v2"
        rescored.append(row)

    out_path = run_dir / "results.production-semantics.jsonl"
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rescored),
        encoding="utf-8",
    )
    expected_jobs = args.expected_jobs or len(raw_results)
    summary = evaluator.aggregate(rescored, expected_jobs)
    leakage_turns = []
    for row in rescored:
        for turn_index, turn in enumerate(row.get("turns", []), start=1):
            task_events = [
                event for event in turn.get("events", [])
                if event.get("event") == "tool_call"
                and event.get("data", {}).get("task_id")
            ]
            actual_call_count = len(turn.get("actual_calls", []))
            if len(task_events) > actual_call_count:
                leakage_turns.append((row["case_id"], turn_index))
    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary.update(
        {
            "model": rescored[0].get("model", "unknown") if rescored else "unknown",
            "scoring_profile": "production-semantics-v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "revision": manifest.get("semantic_revision", {}).get("version"),
                "status": manifest.get("status"),
                "single_turn_sha256": manifest.get("files", {}).get("single_turn_320.jsonl", {}).get("sha256"),
                "multi_turn_sha256": manifest.get("files", {}).get("multi_turn_100.jsonl", {}).get("sha256"),
            },
            "executor_state_leakage": {
                "status": "not_assessed_by_event_count_probe",
                "reason": "task_id event count can exceed captured Mock calls when same-request tasks fail schema validation",
            },
            "tool_event_call_mismatch": {
                "affected_cases": len({case_id for case_id, _ in leakage_turns}),
                "affected_turns": len(leakage_turns),
                "detection": "task_id tool events exceed captured Mock calls in the same turn",
            },
        }
    )
    summary_path = run_dir / "summary.production-semantics.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raw_summary_path = run_dir / "summary.json"
    raw_summary = json.loads(raw_summary_path.read_text(encoding="utf-8")) if raw_summary_path.exists() else None
    _report(run_dir / "report.production-semantics.md", summary, raw_summary, rescored)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
