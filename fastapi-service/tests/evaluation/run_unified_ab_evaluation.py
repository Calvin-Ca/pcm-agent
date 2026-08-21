"""Unified single-inference A/B evaluation for routing and parameters.

Each case invokes the selected production path exactly once.  All routing,
tool-selection, parameter and joint E2E metrics are derived from that same
result, so per-case errors and latency remain attributable to one request.

Examples (run from ``fastapi-service``)::

    python tests/evaluation/run_unified_ab_evaluation.py --variant A --output reports/A_unified.json
    python tests/evaluation/run_unified_ab_evaluation.py --variant B --output reports/B_unified.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVICE_ROOT.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


class _FallbackAuditHandler(logging.Handler):
    """Collect architecture fallback signals without changing app behavior."""

    SIGNALS = (
        "Function Calling 失败",
        "LLM参数提取失败",
        "LLM意图分类失败",
        "使用规则兜底",
    )

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.events: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if any(signal in message for signal in self.SIGNALS):
            self.events.append(message)


def _load_environment() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
        load_dotenv(REPO_ROOT / ".env.local", override=True)
    except ImportError:
        pass


def _initialize_agent() -> Any:
    from app.services.intent_router import IntentRouter
    from app.services.langgraph_agent import initialize_agent
    from app.services.llm_client import LLMClient
    from app.services.permission_validator import PermissionValidator
    from app.services.task_executor import TaskExecutor
    from app.services.tool_registry import ToolRegistry
    import app.tools  # noqa: F401 - register tools

    llm_client = LLMClient(env_prefix="CHAT_LLM")
    tool_registry = ToolRegistry()
    permission_validator = PermissionValidator()
    intent_router = IntentRouter()
    intent_router.set_tool_registry(tool_registry)
    intent_router.set_llm_client(llm_client)
    task_executor = TaskExecutor(
        tool_registry=tool_registry,
        permission_validator=permission_validator,
        llm_client=llm_client,
    )
    intent_router.set_task_executor(task_executor)
    initialize_agent(
        intent_router=intent_router,
        tool_registry=tool_registry,
        task_executor=task_executor,
        llm_client=llm_client,
    )
    return llm_client


def _parameter_check(case: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply the same parameter-value rules as test_param_extraction.py."""
    from tests.utils.date_resolver import resolve_relative_dates, should_skip_date_assertion

    expected = case["expected"]
    sub_type = case.get("sub_type", "")
    fuzzy = set(expected.get("params_fuzzy", [])) | {
        "project_id",
        "member_id",
        "description",
    }
    exists = set(expected.get("params_exists", []))
    expected_params = expected.get("params", {}).copy()
    skip_date = should_skip_date_assertion(sub_type)
    if expected.get("date_relative") and not skip_date:
        expected_params = resolve_relative_dates(expected_params, sub_type=sub_type)

    errors: list[str] = []
    for key, expected_value in expected_params.items():
        if key in fuzzy:
            continue
        if skip_date and key in {"start_date", "end_date", "date"}:
            if not actual.get(key):
                errors.append(f"missing:{key}")
        elif actual.get(key) != expected_value:
            errors.append(f"mismatch:{key}")
    for key in exists:
        if not actual.get(key):
            errors.append(f"missing:{key}")
    return not errors, errors


def _score(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    expected_intent = expected["intent"]
    expected_tool = expected.get("tool_name")
    actual_intent = result.get("intent")
    actual_tool = result.get("tool_name")

    intent_ok = actual_intent == expected_intent
    tool_applicable = bool(expected_tool)
    tool_ok = actual_tool == expected_tool if tool_applicable else actual_tool is None
    routing_ok = intent_ok and tool_ok

    param_applicable = expected_intent == "tool_execution" and tool_applicable
    route_to_expected_tool = (
        actual_intent == "tool_execution" and actual_tool == expected_tool
    )
    param_ok = False
    param_errors: list[str] = []
    if param_applicable and route_to_expected_tool:
        param_ok, param_errors = _parameter_check(case, result.get("tool_params") or {})
    elif param_applicable:
        param_errors = ["routing_failed"]

    return {
        "intent_ok": intent_ok,
        "tool_applicable": tool_applicable,
        "tool_ok": tool_ok,
        "routing_ok": routing_ok,
        "param_applicable": param_applicable,
        "route_to_expected_tool": route_to_expected_tool,
        "param_ok": param_ok,
        "joint_e2e_ok": routing_ok and (not param_applicable or param_ok),
        "non_tool_false_call": not tool_applicable and actual_tool is not None,
        "clarify_applicable": expected_intent == "clarify",
        "clarify_ok": expected_intent == "clarify" and actual_intent == "clarify",
        "param_errors": param_errors,
    }


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(result)
    params = dict(cleaned.get("tool_params") or {})
    params.pop("auth_token", None)
    cleaned["tool_params"] = params
    return cleaned


async def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    from app.services.langgraph_agent import node_llm_with_tools
    from tests.test_classification_accuracy import build_state

    started = time.perf_counter()
    try:
        result = await node_llm_with_tools(build_state(case))
        error = None
    except Exception as exc:  # retain failed requests in the denominator
        result = {}
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000

    return {
        "id": case["id"],
        "input": case["input"],
        "category": case.get("category"),
        "sub_type": case.get("sub_type"),
        "expected": case["expected"],
        "actual": _safe_result(result),
        "score": _score(case, result),
        "latency_ms": round(latency_ms, 3),
        "error": error,
    }


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "passed": numerator,
        "total": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _summarize(
    records: list[dict[str, Any]], wall_seconds: float, fallback_events: list[str]
) -> dict[str, Any]:
    scores = [record["score"] for record in records]
    count = lambda key: sum(bool(score[key]) for score in scores)
    applicable = lambda key: sum(bool(score[key]) for score in scores)
    tool_total = applicable("tool_applicable")
    param_total = applicable("param_applicable")
    routed_param_total = applicable("route_to_expected_tool")
    non_tool_total = len(records) - tool_total
    clarify_total = applicable("clarify_applicable")
    latencies = sorted(record["latency_ms"] for record in records)

    def percentile(fraction: float) -> float | None:
        if not latencies:
            return None
        index = min(round((len(latencies) - 1) * fraction), len(latencies) - 1)
        return round(latencies[index], 3)

    return {
        "requests": len(records),
        "errors": sum(record["error"] is not None for record in records),
        "fallback_audit": {
            "count": len(fallback_events),
            "by_message": dict(Counter(fallback_events)),
        },
        "metrics": {
            "intent_accuracy": _ratio(count("intent_ok"), len(records)),
            "routing_joint_accuracy": _ratio(count("routing_ok"), len(records)),
            "tool_e2e_accuracy": _ratio(
                sum(s["intent_ok"] and s["tool_ok"] for s in scores if s["tool_applicable"]),
                tool_total,
            ),
            "tool_conditional_accuracy": _ratio(
                sum(s["tool_ok"] for s in scores if s["tool_applicable"] and s["intent_ok"]),
                sum(s["tool_applicable"] and s["intent_ok"] for s in scores),
            ),
            "parameter_conditional_accuracy": _ratio(
                sum(s["param_ok"] for s in scores if s["route_to_expected_tool"]),
                routed_param_total,
            ),
            "parameter_e2e_accuracy": _ratio(count("param_ok"), param_total),
            "joint_e2e_accuracy": _ratio(count("joint_e2e_ok"), len(records)),
            "non_tool_false_call_rate": _ratio(count("non_tool_false_call"), non_tool_total),
            "clarify_accuracy": _ratio(count("clarify_ok"), clarify_total),
        },
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "max": max(latencies) if latencies else None,
            "wall_seconds": round(wall_seconds, 3),
        },
        "by_actual_intent": dict(Counter(r["actual"].get("intent") for r in records)),
    }


async def _run(cases: list[dict[str, Any]], concurrency: int) -> tuple[list[dict[str, Any]], float]:
    queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
    for index, case in enumerate(cases):
        queue.put_nowait((index, case))
    records: list[dict[str, Any] | None] = [None] * len(cases)

    async def worker() -> None:
        while True:
            try:
                index, case = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                records[index] = await _evaluate_case(case)
            finally:
                queue.task_done()

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(min(concurrency, len(cases)))))
    return [record for record in records if record is not None], time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("A", "B"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Smoke-test only; omit for the full dataset")
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--api-base", default="http://172.19.3.136:8099/v1")
    parser.add_argument("--model", default="qwen3-8b")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be at least 1")

    _load_environment()
    # Explicit CLI values win over inherited shell variables and dotenv files.
    # This prevents a benchmark from silently switching to DashScope/defaults.
    os.environ["CHAT_LLM_API_BASE"] = args.api_base
    os.environ["CHAT_LLM_MODEL"] = args.model
    os.environ.setdefault("CHAT_LLM_API_KEY", "EMPTY")
    os.environ["INTENT_LLM_API_BASE"] = args.api_base
    os.environ["INTENT_LLM_MODEL"] = args.model
    os.environ.setdefault("INTENT_LLM_API_KEY", "EMPTY")
    if args.variant == "A":
        os.environ["BENCHMARK_FORCE_FALLBACK"] = "1"
        architecture = "two_stage_intent_then_parameters"
    else:
        os.environ.pop("BENCHMARK_FORCE_FALLBACK", None)
        architecture = "single_function_calling"

    llm_client = _initialize_agent()
    if llm_client.api_base.rstrip("/") != args.api_base.rstrip("/") or llm_client.model != args.model:
        raise RuntimeError("LLM client configuration does not match the requested benchmark target")
    from tests.utils.test_data_loader import load_all_cases

    cases = load_all_cases()
    if args.limit is not None:
        cases = cases[: args.limit]
    records: list[dict[str, Any]] = []
    previous_wall_seconds = 0.0
    previous_fallback_events: list[str] = []
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("variant") != args.variant or previous.get("llm", {}).get("model") != args.model:
            raise RuntimeError("Existing checkpoint does not match variant/model")
        records = previous.get("samples", [])
        previous_wall_seconds = previous.get("summary", {}).get("latency_ms", {}).get("wall_seconds", 0.0)
        previous_fallback_events = previous.get("fallback_events", [])
    completed_ids = {record["id"] for record in records}
    pending_cases = [case for case in cases if case["id"] not in completed_ids]

    audit_handler = _FallbackAuditHandler()
    checkpoint: dict[str, Any] = {
        "schema_version": 1,
        "complete": len(records) == len(cases),
        "created_at_epoch": time.time(),
        "variant": args.variant,
        "architecture": architecture,
        "llm": {"api_base": llm_client.api_base, "model": llm_client.model},
        "evaluation_rule": "one complete architecture invocation per case",
        "concurrency": args.concurrency,
        "summary": _summarize(records, previous_wall_seconds, previous_fallback_events),
        "fallback_events": previous_fallback_events,
        "samples": records,
    }
    logging.getLogger().addHandler(audit_handler)
    try:
        wall_seconds = previous_wall_seconds
        for offset in range(0, len(pending_cases), args.checkpoint_every):
            batch = pending_cases[offset : offset + args.checkpoint_every]
            batch_records, batch_seconds = asyncio.run(_run(batch, args.concurrency))
            records.extend(batch_records)
            wall_seconds += batch_seconds
            fallback_events = previous_fallback_events + audit_handler.events
            checkpoint = {
                "schema_version": 1,
                "complete": len(records) == len(cases),
                "created_at_epoch": time.time(),
                "variant": args.variant,
                "architecture": architecture,
                "llm": {"api_base": llm_client.api_base, "model": llm_client.model},
                "evaluation_rule": "one complete architecture invocation per case",
                "concurrency": args.concurrency,
                "summary": _summarize(records, wall_seconds, fallback_events),
                "fallback_events": fallback_events,
                "samples": records,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Checkpoint: {len(records)}/{len(cases)} -> {args.output.resolve()}")
    finally:
        logging.getLogger().removeHandler(audit_handler)
    payload = checkpoint
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
