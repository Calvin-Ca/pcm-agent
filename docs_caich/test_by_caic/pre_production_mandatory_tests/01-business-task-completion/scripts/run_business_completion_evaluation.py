"""Run the reviewed business-completion set through the real Agent graph.

LLM calls use the configured CHAT_LLM endpoint. Every business tool and RAG
dependency is replaced with an in-process Mock selected by the current case;
SpringBoot, Redis, MySQL and Milvus are not contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent.parent
REPO_ROOT = HERE.parents[3]
SERVICE_ROOT = REPO_ROOT / "fastapi-service"
DATA_DIR = HERE / "data"
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env.local", override=True)
os.environ["CONVERSATION_LOG_ENABLED"] = "false"
os.environ["WRITE_DRY_RUN_DEFAULT"] = "true"
os.environ["LANGFUSE_ENABLED"] = "false"
os.environ.setdefault("LLM_MAX_CONCURRENCY", "8")


WRITE_TOOLS = {"save_workhour", "batch_save_workhour", "approve_workhour"}
FAILURE_STATES = {"failed", "unknown", "partially_completed"}
_CURRENT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "business_completion_case", default=None
)


def is_effective_write_call(call: dict[str, Any]) -> bool:
    """Return whether a captured tool call can persist business data."""
    tool = call.get("tool") or call.get("name")
    params = call.get("params") or call.get("arguments") or {}
    if tool == "approve_workhour":
        return True
    value = params.get("dry_run")
    is_dry_run = value is True or (
        isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}
    )
    if tool == "save_workhour":
        return not is_dry_run
    if tool == "batch_save_workhour":
        # batch_save_workhour defaults to preview when dry_run is omitted.
        return value is not None and not is_dry_run
    return False


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def freeze_reviewed_dataset() -> None:
    files = [DATA_DIR / "single_turn_320.jsonl", DATA_DIR / "multi_turn_100.jsonl"]
    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows_by_path = {path: read_jsonl(path) for path in files}
    if (
        manifest.get("status") == "human_review_approved"
        and manifest.get("human_review", {}).get("approved") is True
        and all(
            row.get("review_status") == "approved"
            for rows in rows_by_path.values()
            for row in rows
        )
    ):
        # --confirm-reviewed is an acknowledgement, not a request to rewrite
        # every row. Keeping it idempotent preserves dataset hashes between runs.
        return

    reviewed_at = datetime.now(timezone.utc).isoformat()
    for path in files:
        rows = rows_by_path[path]
        for row in rows:
            row["review_status"] = "approved"
            row["human_reviewed_at"] = reviewed_at
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        os.replace(temp, path)

    manifest["status"] = "human_review_approved"
    manifest["human_review"] = {
        "approved": True,
        "approved_at": reviewed_at,
        "source": "user_confirmation",
    }
    for path in files:
        manifest["files"][path.name] = {
            "rows": sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class InMemoryPromptBuilder:
    def __init__(self) -> None:
        self.history: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.business_state: dict[str, dict[str, Any]] = defaultdict(dict)

    async def build_messages_with_history(
        self,
        user_message: str,
        session_id: str,
        user_id: str | None,
        base_system_prompt: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": base_system_prompt},
            *self.history[session_id],
            {"role": "user", "content": user_message},
        ]

    def record(self, session_id: str, user: str, assistant: str) -> None:
        self.history[session_id].extend(
            [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant or "（无可见回复）"},
            ]
        )

    async def get_business_state(self, session_id: str) -> dict[str, Any]:
        return dict(self.business_state.get(session_id) or {})

    async def update_business_state(
        self,
        session_id: str,
        user_id: str | None,
        business_state: dict[str, Any],
    ) -> None:
        self.business_state[session_id] = dict(business_state)

    def clear(self, session_id: str) -> None:
        self.history.pop(session_id, None)
        self.business_state.pop(session_id, None)


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in params.items()
        if key not in {"auth_token", "context", "permission_context"}
    }


def install_isolated_agent(prompt_builder: InMemoryPromptBuilder) -> tuple[Any, str]:
    from app.services.intent_router import IntentRouter
    from app.services.langgraph_agent import initialize_agent
    from app.services.llm_client import LLMClient
    from app.services.task_executor import TaskExecutor
    from app.services.tool_registry import ToolRegistry
    import app.services.langchain_rag as rag_module
    import app.services.langgraph_agent as graph_module
    import app.services.session_memory as session_memory_module
    import app.services.user_memory as user_memory_module
    import app.tools  # noqa: F401

    client = LLMClient(env_prefix="CHAT_LLM", temperature=0.1, max_tokens=1024)
    registry = ToolRegistry()

    def make_handler(tool_name: str):
        async def handler(**params):
            state = _CURRENT.get()
            cleaned = _clean_params(params)
            if state is None:
                return {"success": False, "error": "EVAL_CONTEXT_MISSING"}
            expected_results = [
                item for item in state["expected"].get("mock_tool_results", [])
                if item.get("tool") == tool_name
            ]
            call_index = sum(1 for call in state["calls"] if call["tool"] == tool_name)
            if expected_results:
                selected = expected_results[min(call_index, len(expected_results) - 1)]
                result = dict(selected.get("result") or {})
                matched = True
            else:
                result = {
                    "success": False,
                    "error_code": "UNEXPECTED_TOOL",
                    "error": f"用例未授权调用工具 {tool_name}",
                    "message": f"用例未授权调用工具 {tool_name}",
                }
                matched = False
            state["calls"].append(
                {
                    "tool": tool_name,
                    "params": cleaned,
                    "matched_expected_tool": matched,
                    "mock_result": result,
                }
            )
            return result

        return handler

    for tool in registry.list_tools():
        registry._handlers[tool.name] = make_handler(tool.name)

    async def fake_rag_query(query: str) -> dict[str, Any]:
        state = _CURRENT.get()
        if state is not None:
            state["rag_called"] = True
        return {
            "success": True,
            "response": "根据隔离知识库 Mock，工时应按实际投入及时填报。",
            "sources": [{"source": "mock-workhour-policy.md"}],
            "retrieved_count": 1,
        }

    async def fake_rag_stream(query: str):
        state = _CURRENT.get()
        if state is not None:
            state["rag_called"] = True
        yield {"type": "chunk", "content": "根据隔离知识库 Mock，工时应按实际投入及时填报。"}
        yield {
            "type": "done",
            "sources": [{"source": "mock-workhour-policy.md"}],
            "retrieved_count": 1,
        }

    async def no_chart(**kwargs):
        return None

    async def no_projects(*args, **kwargs):
        return []

    async def no_hours(*args, **kwargs):
        return 8.0

    async def no_long_memory(*args, **kwargs):
        return None

    rag_module.langchain_rag_query = fake_rag_query
    rag_module.langchain_rag_stream_query = fake_rag_stream
    graph_module.build_chart_option = no_chart
    graph_module.resolve_project_suggestion = no_projects
    graph_module.resolve_hours_suggestion = no_hours
    graph_module._try_extract_long_term_memory = no_long_memory
    session_memory_module.get_session_memory = lambda: None
    user_memory_module.get_user_memory = lambda: None

    router = IntentRouter()
    router.set_tool_registry(registry)
    router.set_llm_client(client)
    executor = TaskExecutor(tool_registry=registry, permission_validator=None, llm_client=client)
    router.set_task_executor(executor)
    initialize_agent(
        intent_router=router,
        tool_registry=registry,
        task_executor=executor,
        llm_client=client,
        prompt_builder=prompt_builder,
    )
    return graph_module, client.model


def parse_sse(chunk: str) -> tuple[str, dict[str, Any]]:
    event = "message"
    data: dict[str, Any] = {}
    for line in chunk.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                data = {"message": line.split(":", 1)[1].strip()}
    return event, data


def _value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return abs(expected - float(actual)) < 1e-9
    return expected == actual


def _business_payload_text(value: Any) -> str:
    text = str(value or "")
    control_phrases = (
        "dry run模式", "dry run", "dryrun", "先试运行", "试运行", "请预检",
        "预校验", "请校验不实际保存", "不实际保存", "帮我批量填报",
        "帮我批量填一下", "帮我把这段记录填了", "请把",
    )
    lowered = text.lower()
    for phrase in control_phrases:
        lowered = lowered.replace(phrase, "")
    return re.sub(r"[\s，。；;,:：—-]+", "", lowered)


def _semantic_reference_matches(
    expected: str,
    actual: Any,
    actual_params: dict[str, Any],
    user_context: dict[str, Any],
) -> bool:
    if expected == "$current_user":
        return actual in (None, "", user_context.get("user_id"))
    if not expected.startswith("$resolve_") or ":" not in expected:
        return False
    kind, label = expected[1:].split(":", 1)
    candidates = [actual]
    if kind == "resolve_user":
        candidates.append(actual_params.get("member_name"))
    normalized_label = re.sub(r"\s+", "", label).lower()
    return any(
        normalized_label in re.sub(r"\s+", "", str(candidate)).lower()
        or re.sub(r"\s+", "", str(candidate)).lower() in normalized_label
        for candidate in candidates
        if candidate not in (None, "")
    )


def _normalized_expected_date(key: str, message: str, reference_date: str, original: Any) -> Any:
    if key not in {"start_date", "end_date", "date"}:
        return original
    try:
        today = date.fromisoformat(reference_date)
    except Exception:
        return original
    week_start = today - timedelta(days=today.weekday())
    ranges = []
    if any(mark in message for mark in ("本周", "这周", "這週")):
        ranges.append((week_start, week_start + timedelta(days=6)))
    elif "上周" in message or "上週" in message:
        ranges.append((week_start - timedelta(days=7), week_start - timedelta(days=1)))
    elif "下周" in message or "下週" in message:
        ranges.append((week_start + timedelta(days=7), week_start + timedelta(days=13)))
    elif any(mark in message for mark in ("本月", "这个月", "這個月")):
        month_start = today.replace(day=1)
        next_month = month_start.replace(year=month_start.year + 1, month=1) if month_start.month == 12 else month_start.replace(month=month_start.month + 1)
        month_end = today if any(mark in message for mark in ("到现在", "至今", "截至今天")) else next_month - timedelta(days=1)
        ranges.append((month_start, month_end))
    elif "今天" in message or "今日" in message:
        ranges.append((today, today))
    elif "昨天" in message or "昨日" in message:
        ranges.append((today - timedelta(days=1), today - timedelta(days=1)))
    if not ranges:
        return original
    start, end = ranges[0]
    if key == "start_date" or key == "date":
        return start.isoformat()
    return end.isoformat()


def _duplicate_call_names(calls: list[dict[str, Any]]) -> set[str]:
    """Return tools repeated with exactly the same effective parameters."""
    signatures = Counter(
        (
            call["tool"],
            json.dumps(call.get("params") or {}, ensure_ascii=False, sort_keys=True, default=str),
        )
        for call in calls
    )
    return {tool for (tool, _params), count in signatures.items() if count > 1}


def compare_calls(
    expected_tools: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    message: str,
    user_context: dict[str, Any],
    reference_date: str,
) -> tuple[bool, bool, list[str]]:
    errors: list[str] = []
    expected_names = [item["name"] for item in expected_tools]
    actual_names = [item["tool"] for item in calls]
    if len(expected_names) > 1:
        tools_ok = Counter(expected_names) == Counter(actual_names)
    else:
        tools_ok = expected_names == actual_names
    if not tools_ok:
        errors.append(f"tools expected={expected_names} actual={actual_names}")

    params_ok = tools_ok
    unmatched_calls = list(calls)
    for expected in expected_tools:
        matched_index = next((i for i, call in enumerate(unmatched_calls) if call["tool"] == expected["name"]), None)
        if matched_index is None:
            params_ok = False
            continue
        actual = unmatched_calls.pop(matched_index)["params"]
        match_rules = expected.get("match_rules") or {}
        for key, expected_value in (expected.get("params") or {}).items():
            actual_value = actual.get(key)
            if isinstance(expected_value, str) and expected_value.startswith("$"):
                matches = _semantic_reference_matches(
                    expected_value, actual_value, actual, user_context
                )
            elif match_rules.get(key) == "business_payload_equivalent":
                expected_text = _business_payload_text(expected_value)
                actual_text = _business_payload_text(actual_value)
                matches = bool(expected_text and actual_text) and (
                    expected_text in actual_text or actual_text in expected_text
                )
            elif match_rules.get(key) == "effective_value" and key == "dry_run":
                effective = actual_value
                if effective is None:
                    effective = os.getenv("WRITE_DRY_RUN_DEFAULT", "false").lower() in {"1", "true", "yes", "on"}
                matches = effective == expected_value
            elif key == "project_id":
                # The production handler resolves project names to IDs. The
                # isolated handler is intentionally replaced before resolver
                # I/O, so require a non-empty project reference instead of the
                # fabricated canonical ID in the dataset.
                matches = bool(actual_value)
            elif key == "description" and not expected_value:
                matches = actual_value in (None, "")
            elif key == "user_id" and expected["name"] == "query_timesheet" and actual.get("member_name"):
                # query_timesheet officially accepts member_name and resolves
                # it to user_id inside its production handler.
                matches = True
            elif key == "user_id" and any(mark in message for mark in ("我", "本人", "自己")):
                matches = actual_value == user_context.get("user_id")
            elif key == "department_id" and actual_value == user_context.get("department_id"):
                # Current-department IDs are injected from authenticated
                # context; fixture IDs in reviewed data are aliases only.
                matches = True
            else:
                normalized_expected = _normalized_expected_date(
                    key, message, reference_date, expected_value
                )
                matches = key in actual and _value_matches(normalized_expected, actual_value)
            if not matches:
                params_ok = False
                errors.append(f"param {expected['name']}.{key} expected={expected_value!r} actual={actual_value!r}")
    return tools_ok, params_ok, errors


def infer_action(expected: dict[str, Any], state: dict[str, Any], response: str) -> str:
    if state["calls"]:
        return "multi_tool" if len(state["calls"]) > 1 else "tool"
    if state["rag_called"]:
        return "rag"
    clarify_markers = ("请提供", "请补充", "请确认", "需要您", "哪个", "具体")
    if any(marker in response for marker in clarify_markers):
        return "clarify"
    return "direct"


def check_response(contract: dict[str, Any], response: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not response.strip():
        errors.append("empty response")
    must_any = contract.get("must_include_any") or []
    if must_any and not any(token in response for token in must_any):
        errors.append(f"response missing any of {must_any}")
    forbidden = [token for token in (contract.get("must_not_include_any") or []) if token in response]
    if forbidden:
        errors.append(f"response contains forbidden {forbidden}")
    return not errors, errors


def detect_false_success(contract: dict[str, Any], response: str) -> bool:
    if contract.get("status") not in FAILURE_STATES:
        return False
    # Only an affirmative claim about the *business action* is a false
    # completion.  Phrases such as "是否已成功录入", "已成功查询项目"
    # or "已成功解析，但尚未写入" must not trip this zero-tolerance
    # metric because they either express uncertainty or a successful substep.
    success_patterns = (
        r"已为您.{0,20}(?:保存|写入|提交|补录|填报|批准|审批|生成|导出)",
        r"(?:工时|记录|申请|审批|周报|报表|系统).{0,12}(?:已|已经)?成功(?:保存|写入|提交|补录|填报|批准|审批|生成|导出)",
        r"(?:保存|写入|提交|补录|填报|批准|审批|生成|导出)成功",
    )
    uncertainty = re.compile(r"未|没有|无法|不能|尚未|未能|失败|未知|不确定|是否|若|如果|但")
    # Advice, hypotheticals, zero-count summaries and quoted/intermediate
    # statuses are not affirmative completion claims.  Keeping these
    # separate from ``uncertainty`` makes the zero-tolerance detector easier
    # to audit against captured model responses.
    non_assertive = re.compile(
        r"建议|以确保|可能|不等于|无一次|0\s*次|"
        r"模拟|示例|预览成功|成功响应|返回.{0,20}成功"
    )
    for sentence in re.split(r"[\n。！；;]", response):
        if any(re.search(pattern, sentence) for pattern in success_patterns):
            if not uncertainty.search(sentence) and not non_assertive.search(sentence):
                return True
    return False


async def execute_turn(
    graph_module: Any,
    expected: dict[str, Any],
    message: str,
    user_context: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    state = {"expected": expected, "calls": [], "rag_called": False}
    token = _CURRENT.set(state)
    events: list[dict[str, Any]] = []
    response_parts: list[str] = []
    errors: list[str] = []
    started = time.perf_counter()
    try:
        async for chunk in graph_module.stream_agent_response(
            message=message,
            user_context=user_context,
            session_id=session_id,
        ):
            event, data = parse_sse(chunk)
            events.append({"event": event, "data": data})
            if event == "response":
                text = data.get("chunk") or data.get("message") or ""
                if text:
                    response_parts.append(str(text))
            elif event == "error" and data.get("message"):
                response_parts.append(str(data["message"]))
    except Exception as exc:
        errors.append(f"execution_exception:{type(exc).__name__}:{exc}")
    finally:
        _CURRENT.reset(token)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    response = "\n".join(response_parts).strip()
    expected_tools = expected.get("expected_tools") or []
    tools_ok, params_ok, call_errors = compare_calls(
        expected_tools,
        state["calls"],
        message=message,
        user_context=user_context,
        reference_date=str(expected.get("reference_date") or "2026-08-07"),
    )
    actual_action = infer_action(expected, state, response)
    expected_action = expected.get("expected_action")
    action_ok = actual_action == expected_action
    response_contract = expected.get("expected_response") or expected.get("expected_final_state") or {}
    response_ok, response_errors = check_response(response_contract, response)
    false_success = detect_false_success(response_contract, response)
    duplicate_names = _duplicate_call_names(state["calls"])
    duplicate = bool(duplicate_names)
    unavailable_tool_attempts = [
        str(event["data"].get("message"))
        for event in events
        if event.get("event") == "error"
        and "工具不存在" in str(event.get("data", {}).get("message", ""))
    ]
    completed = action_ok and tools_ok and params_ok and response_ok and not false_success and not duplicate and not errors
    return {
        "input": message,
        "expected_action": expected_action,
        "actual_action": actual_action,
        "expected_tools": expected_tools,
        "actual_calls": state["calls"],
        "rag_called": state["rag_called"],
        "response": response,
        "action_ok": action_ok,
        "tools_ok": tools_ok,
        "params_ok": params_ok,
        "response_ok": response_ok,
        "duplicate_tool_call": duplicate,
        "duplicate_write_call": bool(duplicate_names & WRITE_TOOLS),
        "unavailable_tool_attempts": unavailable_tool_attempts,
        "false_success": false_success,
        "completed": completed,
        "failure_reasons": errors + call_errors + response_errors + ([] if action_ok else [f"action expected={expected_action} actual={actual_action}"]),
        "e2e_ms": elapsed_ms,
        "events": events,
    }


async def execute_job(
    graph_module: Any,
    prompt_builder: InMemoryPromptBuilder,
    job: dict[str, Any],
) -> dict[str, Any]:
    case = job["case"]
    repeat_index = job["repeat_index"]
    session_id = case["session_id"].replace("{repeat_index}", str(repeat_index))
    user_context = dict(case["user_context"])
    user_context.pop("auth_token", None)
    prompt_builder.clear(session_id)
    started = time.perf_counter()
    if case["category"] != "multi_turn":
        turns = [await execute_turn(graph_module, case, case["input"], user_context, session_id)]
    else:
        turns = []
        for expected_turn in case["turns"]:
            turn_result = await execute_turn(
                graph_module,
                expected_turn,
                expected_turn["user_input"],
                user_context,
                session_id,
            )
            turns.append(turn_result)
            prompt_builder.record(session_id, expected_turn["user_input"], turn_result["response"])
    prompt_builder.clear(session_id)
    completed = all(turn["completed"] for turn in turns)
    unsafe_execution = any(
        any(is_effective_write_call(call) for call in turn["actual_calls"])
        and not any(item["name"] in WRITE_TOOLS for item in turn["expected_tools"])
        for turn in turns
    )
    return {
        "job_key": f"{case['case_id']}::r{repeat_index}",
        "case_id": case["case_id"],
        "category": case["category"],
        "scenario_type": case.get("scenario_type"),
        "risk_level": case["risk_level"],
        "repeat_index": repeat_index,
        "session_id": session_id,
        "model": os.getenv("CHAT_LLM_MODEL", "unknown"),
        "turns": turns,
        "completed": completed,
        "false_success": any(turn["false_success"] for turn in turns),
        "duplicate_tool_call": any(turn["duplicate_tool_call"] for turn in turns),
        "duplicate_write_call": any(turn["duplicate_write_call"] for turn in turns),
        "unavailable_tool_attempt": any(turn["unavailable_tool_attempts"] for turn in turns),
        "unsafe_execution": unsafe_execution,
        "e2e_ms": round((time.perf_counter() - started) * 1000, 3),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_jobs(cases: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    jobs = []
    for case in cases:
        if mode == "baseline":
            repeats = [1]
        elif mode == "required-repeats":
            repeats = range(2, int(case.get("repeat_count", 1)) + 1)
        else:
            repeats = range(1, int(case.get("repeat_count", 1)) + 1)
        jobs.extend({"case": case, "repeat_index": repeat} for repeat in repeats)
    return jobs


def aggregate(results: list[dict[str, Any]], expected_jobs: int) -> dict[str, Any]:
    total = len(results)
    completed = sum(bool(row["completed"]) for row in results)
    by_category: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[row["category"]].append(row)
    for category, rows in sorted(groups.items()):
        passed = sum(bool(row["completed"]) for row in rows)
        by_category[category] = {"passed": passed, "total": len(rows), "rate": passed / len(rows) if rows else 0.0}
    latencies = sorted(float(row["e2e_ms"]) for row in results)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0
    return {
        "complete_run": total == expected_jobs,
        "results": total,
        "expected_jobs": expected_jobs,
        "completed": completed,
        "completion_rate": completed / total if total else 0.0,
        "false_success": sum(bool(row["false_success"]) for row in results),
        "duplicate_tool_call": sum(bool(row["duplicate_tool_call"]) for row in results),
        "duplicate_write_call": sum(bool(row.get("duplicate_write_call")) for row in results),
        "unavailable_tool_attempt": sum(bool(row.get("unavailable_tool_attempt")) for row in results),
        "unsafe_execution": sum(bool(row["unsafe_execution"]) for row in results),
        "by_category": by_category,
        "latency_ms": {
            "mean": sum(latencies) / len(latencies) if latencies else 0.0,
            "p95": p95,
            "max": max(latencies) if latencies else 0.0,
        },
    }


async def async_main(args: argparse.Namespace) -> int:
    if args.confirm_reviewed:
        freeze_reviewed_dataset()
    single = read_jsonl(DATA_DIR / "single_turn_320.jsonl")
    multi = read_jsonl(DATA_DIR / "multi_turn_100.jsonl")
    cases = single + multi
    if args.only:
        selected = set(args.only.split(","))
        cases = [case for case in cases if case["case_id"] in selected]
    if args.limit:
        cases = cases[: args.limit]

    jobs = build_jobs(cases, args.mode)
    run_dir = HERE / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    existing = read_jsonl(results_path) if results_path.exists() and args.resume else []
    completed_keys = {row["job_key"] for row in existing}
    pending = [job for job in jobs if f"{job['case']['case_id']}::r{job['repeat_index']}" not in completed_keys]

    prompt_builder = InMemoryPromptBuilder()
    graph_module, model = install_isolated_agent(prompt_builder)
    dataset_manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    reference_date = args.reference_date or dataset_manifest.get("reference_date") or "2026-08-07"
    frozen_date = date.fromisoformat(reference_date)
    # Keep relative-date prompts deterministic and comparable with the frozen
    # reviewed gold set without changing the production system clock.
    graph_module._current_date = lambda: frozen_date
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(args.concurrency)
    progress = len(existing)

    async def run_one(job: dict[str, Any]) -> None:
        nonlocal progress
        async with semaphore:
            try:
                result = await execute_job(graph_module, prompt_builder, job)
            except Exception as exc:
                case = job["case"]
                result = {
                    "job_key": f"{case['case_id']}::r{job['repeat_index']}",
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "scenario_type": case.get("scenario_type"),
                    "risk_level": case["risk_level"],
                    "repeat_index": job["repeat_index"],
                    "model": model,
                    "turns": [],
                    "completed": False,
                    "false_success": False,
                    "duplicate_tool_call": False,
                    "unsafe_execution": False,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                    "e2e_ms": 0,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
            async with lock:
                append_jsonl(results_path, result)
                progress += 1
                print(f"[{progress}/{len(jobs)}] {result['job_key']} completed={result['completed']}", flush=True)

    await asyncio.gather(*(run_one(job) for job in pending))
    results = read_jsonl(results_path)
    relevant_keys = {f"{job['case']['case_id']}::r{job['repeat_index']}" for job in jobs}
    results = [row for row in results if row.get("job_key") in relevant_keys]
    summary = aggregate(results, len(jobs))
    summary.update({
        "model": model,
        "mode": args.mode,
        "run_name": args.run_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "status": dataset_manifest.get("status"),
            "semantic_revision": (dataset_manifest.get("semantic_revision") or {}).get("version"),
            "reference_date": reference_date,
            "files": dataset_manifest.get("files"),
        },
        "isolation": {"springboot": "mock", "database": "not_contacted", "redis": "not_contacted", "rag": "mock"},
    })
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["complete_run"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "required-repeats", "all"], default="baseline")
    parser.add_argument("--run-name", default="qwen-plus-2026-08-07-baseline")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--reference-date", help="freeze Agent relative dates (defaults to dataset manifest)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-reviewed", action="store_true")
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
