"""P0 regression tests for Agent write safety and request isolation."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.services.langgraph_agent as langgraph_agent
from app.models.task_plan import TaskNode, TaskPlan, TaskStatus, TaskType
from app.models.tool import ToolCategory
from app.services.langgraph_agent import (
    _UNVERIFIED_COMPLETION_RE,
    _agent_loop_should_continue,
    _deduplicate_tool_calls,
    _enforce_write_confirmation,
    _forced_tool_calls_for_request,
    _missing_save_workhour_fields,
    _normalize_business_tool_calls,
    _should_verify_project_before_save,
    _tool_call_clarification,
    _write_call_clarification,
)
from app.services.task_executor import TaskExecutor
from app.services.tool_registry import ToolRegistry
from app.tools.approve_workhour import approve_workhour_handler
from app.tools.batch_save_workhour import _save_single_workhour
from app.tools.compute_statistics import COMPUTE_STATISTICS_SCHEMA
from app.tools.generate_weekly_report import GENERATE_WEEKLY_REPORT_SCHEMA
from app.tools.query_timesheet import QUERY_TIMESHEET_SCHEMA
from app.tools.save_workhour import SAVE_WORKHOUR_SCHEMA


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_registry():
    ToolRegistry._instance = None
    yield
    ToolRegistry._instance = None


def _register(registry, name, handler, *, is_write=False, properties=None):
    registry.register_tool(
        name=name,
        description="test tool",
        json_schema={
            "type": "object",
            "properties": properties or {},
            "additionalProperties": False,
        },
        handler=handler,
        category=ToolCategory.WORKHOUR if is_write else ToolCategory.DATA_QUERY,
        is_write=is_write,
    )


async def test_concurrent_plans_do_not_share_task_results():
    registry = ToolRegistry()

    async def echo(value):
        await asyncio.sleep(0.01 if value == "first" else 0)
        return {"success": True, "value": value}

    _register(registry, "echo", echo, properties={"value": {"type": "string"}})
    executor = TaskExecutor(registry)

    def make_plan(value):
        plan = TaskPlan(name=value)
        plan.add_task(TaskNode(
            task_id="t1",
            task_type=TaskType.TOOL_CALL,
            tool_name="echo",
            parameters={"value": value},
        ))
        return plan

    first, second = await asyncio.gather(
        executor.execute_plan(make_plan("first")),
        executor.execute_plan(make_plan("second")),
    )

    assert first["task_results"]["t1"]["result"]["value"] == "first"
    assert second["task_results"]["t1"]["result"]["value"] == "second"
    assert set(first["task_results"]) == {"t1"}
    assert set(second["task_results"]) == {"t1"}


async def test_business_failure_marks_plan_failed_and_remains_in_summary():
    registry = ToolRegistry()

    async def rejected():
        return {"success": False, "error": "validation failed"}

    _register(registry, "reject", rejected)
    executor = TaskExecutor(registry)
    plan = TaskPlan(name="failure")
    plan.add_task(TaskNode(
        task_id="t1",
        task_type=TaskType.TOOL_CALL,
        tool_name="reject",
    ))

    summary = await executor.execute_plan(plan)

    assert summary["success"] is False
    assert summary["status"] == TaskStatus.FAILED
    assert plan.tasks["t1"].status == TaskStatus.FAILED
    assert summary["task_results"]["t1"]["success"] is False


async def test_write_connection_loss_is_not_retried_and_returns_unknown():
    registry = ToolRegistry()
    calls = 0

    async def disconnected():
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection lost")

    _register(registry, "write_once", disconnected, is_write=True)
    executor = TaskExecutor(registry)
    task = TaskNode(
        task_id="w1",
        task_type=TaskType.TOOL_CALL,
        tool_name="write_once",
    )

    result = await executor.execute_single_task(task)

    assert calls == 1
    assert result["success"] is False
    assert result["status"] == "unknown"
    assert result["error_code"] == "WRITE_RESULT_UNKNOWN"
    assert result["message"] == "提交结果未知，请查询确认"


async def test_caught_write_connection_loss_is_promoted_to_unknown():
    registry = ToolRegistry()

    async def caught_connection_loss():
        return {
            "success": False,
            "status": "unknown",
            "error_code": "WRITE_RESULT_UNKNOWN",
            "error": "network details must not be presented as a definite failure",
        }

    _register(registry, "write_once", caught_connection_loss, is_write=True)
    executor = TaskExecutor(registry)
    result = await executor.execute_single_task(TaskNode(
        task_id="w1",
        task_type=TaskType.TOOL_CALL,
        tool_name="write_once",
    ))

    assert result == {
        "success": False,
        "status": "unknown",
        "error_code": "WRITE_RESULT_UNKNOWN",
        "error": "提交结果未知，请查询确认",
        "message": "提交结果未知，请查询确认",
    }


async def test_outer_write_timeout_returns_unknown_without_retry():
    registry = ToolRegistry()
    calls = 0

    async def slow_write():
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return {"success": True}

    _register(registry, "write_once", slow_write, is_write=True)
    executor = TaskExecutor(registry)
    task = TaskNode(
        task_id="w1",
        task_type=TaskType.TOOL_CALL,
        tool_name="write_once",
        timeout=5,
    )

    result = await executor.execute_single_task(task, timeout=0.01)

    assert calls == 1
    assert result["success"] is False
    assert result["status"] == "unknown"
    assert result["error_code"] == "WRITE_RESULT_UNKNOWN"
    assert result["message"] == "提交结果未知，请查询确认"


async def test_definite_write_validation_failure_stays_failed_not_unknown():
    registry = ToolRegistry()

    async def validation_failure():
        return {"success": False, "error": "工时不能超过每日上限"}

    _register(registry, "write_once", validation_failure, is_write=True)
    executor = TaskExecutor(registry)
    result = await executor.execute_single_task(TaskNode(
        task_id="w1",
        task_type=TaskType.TOOL_CALL,
        tool_name="write_once",
    ))

    assert result["success"] is False
    assert result.get("status") != "unknown"
    assert result["result"]["error"] == "工时不能超过每日上限"


async def test_approve_handler_network_loss_returns_unknown():
    with patch("app.tools.approve_workhour.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=httpx.ConnectError("connection lost"))
        client_cls.return_value = client

        result = await approve_workhour_handler(
            workhour_ids=["wh-1"],
            action="approve",
        )

    assert result["success"] is False
    assert result["status"] == "unknown"
    assert result["error_code"] == "WRITE_RESULT_UNKNOWN"
    assert result["message"] == "提交结果未知，请查询确认"


async def test_batch_single_write_network_loss_returns_unknown():
    record = {
        "date": "2026-08-08",
        "project_id": "p-1",
        "project_name": "项目一",
        "hours": 4.0,
        "content": "开发",
    }
    with patch(
        "app.tools.batch_save_workhour._get_workhour_type_for_date",
        new=AsyncMock(return_value="正常工时"),
    ), patch(
        "app.tools.batch_save_workhour.resolve_work_type",
        new=AsyncMock(return_value="研发工作"),
    ), patch("app.tools.batch_save_workhour.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=httpx.ConnectError("connection lost"))
        client_cls.return_value = client

        result = await _save_single_workhour(
            record,
            "user-1",
            "token",
            "http://springboot",
        )

    assert result["success"] is False
    assert result["status"] == "unknown"
    assert result["error_code"] == "WRITE_RESULT_UNKNOWN"
    assert result["error_message"] == "提交结果未知，请查询确认"


async def test_duplicate_write_plan_is_blocked_before_handler():
    registry = ToolRegistry()
    calls = 0

    async def write(value):
        nonlocal calls
        calls += 1
        return {"success": True, "value": value}

    _register(
        registry,
        "write_once",
        write,
        is_write=True,
        properties={"value": {"type": "string"}},
    )
    executor = TaskExecutor(registry)
    plan = TaskPlan(name="duplicate")
    for task_id in ("t1", "t2"):
        plan.add_task(TaskNode(
            task_id=task_id,
            task_type=TaskType.TOOL_CALL,
            tool_name="write_once",
            parameters={"value": "same"},
        ))

    summary = await executor.execute_plan(plan)

    assert calls == 0
    assert summary["success"] is False
    assert "重复写操作" in summary["error"]


async def test_incomplete_save_is_blocked_before_handler():
    registry = ToolRegistry()
    calls = 0

    async def save_workhour(**kwargs):
        nonlocal calls
        calls += 1
        return {"success": True}

    _register(
        registry,
        "save_workhour",
        save_workhour,
        is_write=True,
        properties={
            "project_id": {"type": "string"},
            "date": {"type": "string"},
            "duration": {"type": "number"},
        },
    )
    executor = TaskExecutor(registry)
    task = TaskNode(
        task_id="w1",
        task_type=TaskType.TOOL_CALL,
        tool_name="save_workhour",
        parameters={"project_id": "P100"},
    )

    result = await executor.execute_single_task(task)

    assert calls == 0
    assert result["success"] is False
    assert "必须先向用户澄清" in result["message"]


async def test_trusted_injected_user_id_does_not_break_tool_schema():
    registry = ToolRegistry()

    async def query_project(**kwargs):
        return {"success": True, "received_user": kwargs.get("user_id")}

    _register(
        registry,
        "query_project",
        query_project,
        properties={"project_id": {"type": "string"}},
    )
    executor = TaskExecutor(registry)
    task = TaskNode(
        task_id="q1",
        task_type=TaskType.TOOL_CALL,
        tool_name="query_project",
        parameters={"project_id": "P100", "user_id": "trusted-user"},
    )

    result = await executor.execute_single_task(task)

    assert result["success"] is True
    assert result["result"]["received_user"] == "trusted-user"


async def test_exact_duplicate_model_calls_are_collapsed():
    calls = [
        {"name": "save_workhour", "arguments": {"date": "2026-08-08", "duration": 4}},
        {"name": "save_workhour", "arguments": {"duration": 4, "date": "2026-08-08"}},
        {"name": "save_workhour", "arguments": {"date": "2026-08-09", "duration": 4}},
    ]

    assert len(_deduplicate_tool_calls(calls)) == 2


async def test_batch_write_requires_explicit_confirmation_for_real_write():
    first_turn = [{
        "name": "batch_save_workhour",
        "arguments": {"text": "two records", "dry_run": False},
    }]
    confirmed = [{
        "name": "batch_save_workhour",
        "arguments": {"text": "two records", "dry_run": False},
    }]

    assert _enforce_write_confirmation(first_turn, "帮我批量填报")[0]["arguments"]["dry_run"] is True
    assert _enforce_write_confirmation(confirmed, "确认提交")[0]["arguments"]["dry_run"] is False


async def test_first_turn_model_cannot_invent_today_for_missing_date():
    state = {
        "user_message": "我在星云平台干了2.5小时，dry run提交",
        "conversation_history": [
            {"role": "system", "content": "today=2026-08-08"},
            {"role": "user", "content": "我在星云平台干了2.5小时，dry run提交"},
        ],
    }
    args = {"project_id": "星云平台", "duration": 2.5, "date": "2026-08-08"}

    missing = _missing_save_workhour_fields(args, state)

    assert any("日期" in item for item in missing)


async def test_first_turn_model_cannot_invent_default_duration():
    state = {
        "user_message": "2026-08-03 在智慧园区项目工作，dry run 保存",
        "conversation_history": [
            {"role": "user", "content": "2026-08-03 在智慧园区项目工作，dry run 保存"},
        ],
    }
    args = {"project_id": "智慧园区", "date": "2026-08-03", "duration": 8, "dry_run": True}

    missing = _missing_save_workhour_fields(args, state)

    assert any("有效工时时长" in item for item in missing)


async def test_invalid_duration_is_blocked_before_write_route():
    state = {
        "user_message": "录今天 P4001 3.7h 后端接口开发",
        "conversation_history": [
            {"role": "user", "content": "录今天 P4001 3.7h 后端接口开发"},
        ],
    }
    args = {"project_id": "P4001", "date": "2026-08-07", "duration": 3.7}

    message = _write_call_clarification("save_workhour", args, state)

    assert message is not None
    assert "有效工时时长" in message


async def test_valid_explicit_dry_run_save_remains_allowed():
    state = {
        "user_message": "2026-08-05 在星云平台工作3.5小时，dry run保存",
        "conversation_history": [
            {"role": "user", "content": "2026-08-05 在星云平台工作3.5小时，dry run保存"},
        ],
    }
    args = {"project_id": "星云平台", "date": "2026-08-05", "duration": 3.5, "dry_run": True}

    with patch("app.services.langgraph_agent._current_date", return_value=date(2026, 8, 7)):
        message = _write_call_clarification("save_workhour", args, state)

    assert message is None


async def test_half_day_duration_can_be_normalized_to_hours():
    state = {
        "user_message": "2026-08-04 在ERP升级项目工作了0.5天，请预存",
        "conversation_history": [
            {"role": "user", "content": "2026-08-04 在ERP升级项目工作了0.5天，请预存"},
        ],
    }
    args = {"project_id": "ERP升级", "date": "2026-08-04", "duration": 4, "dry_run": True}

    with patch("app.services.langgraph_agent._current_date", return_value=date(2026, 8, 7)):
        message = _write_call_clarification("save_workhour", args, state)

    assert message is None


async def test_write_tool_requires_current_turn_write_intent():
    state = {
        "user_message": "为什么第三条没通过？请说明每条的校验结果",
        "conversation_history": [
            {"role": "user", "content": "批量填报三条"},
            {"role": "assistant", "content": "第三条校验失败"},
            {"role": "user", "content": "为什么第三条没通过？请说明每条的校验结果"},
        ],
    }

    message = _write_call_clarification(
        "batch_save_workhour",
        {"text": "three records", "dry_run": True},
        state,
    )

    assert message is not None
    assert "没有明确要求批量写入" in message


async def test_query_timesheet_schema_matches_handler_member_name_support():
    assert "member_name" in QUERY_TIMESHEET_SCHEMA["properties"]


async def test_first_turn_real_save_requires_preview_confirmation():
    state = {
        "user_message": "我在项目P456上昨天工作了半小时，帮我记一下",
        "conversation_history": [
            {"role": "user", "content": "我在项目P456上昨天工作了半小时，帮我记一下"},
        ],
    }
    args = {"project_id": "P456", "date": "2026-08-06", "duration": 0.5}

    with patch("app.services.langgraph_agent._current_date", return_value=date(2026, 8, 7)):
        message = _write_call_clarification("save_workhour", args, state)

    assert message is not None
    assert "写入确认" in message


async def test_batch_preview_allows_other_members_without_persisting():
    state = {
        "user_message": "预览一下，别真存",
        "user_context": {"user_name": "测试用户"},
        "conversation_history": [
            {"role": "user", "content": "张三 2026-08-04 P-789 6h 测试用例编写"},
            {"role": "assistant", "content": "请确认如何处理"},
            {"role": "user", "content": "预览一下，别真存"},
        ],
    }

    message = _write_call_clarification(
        "batch_save_workhour",
        {"text": "张三 2026-08-04 P-789 6h 测试用例编写", "dry_run": True},
        state,
    )

    assert message is None


async def test_batch_real_write_rejects_other_members_for_employee():
    state = {
        "user_message": "确认提交",
        "user_context": {"user_name": "测试用户", "entity_type": "employee"},
        "conversation_history": [
            {"role": "user", "content": "张三 2026-08-04 P-789 6h，dry run"},
            {"role": "assistant", "content": "预览成功"},
            {"role": "user", "content": "确认提交"},
        ],
    }

    message = _write_call_clarification(
        "batch_save_workhour",
        {"text": "张三 2026-08-04 P-789 6h", "dry_run": False},
        state,
    )

    assert message is not None
    assert "其他成员姓名" in message


async def test_project_id_only_reply_is_verified_before_pending_save():
    state = {
        "user_message": "P601",
        "conversation_history": [
            {"role": "user", "content": "补录2026-07-25的2小时"},
            {"role": "assistant", "content": "请提供项目ID"},
            {"role": "user", "content": "P601"},
        ],
    }

    assert _should_verify_project_before_save({"project_id": "P601"}, state) is True
    assert _should_verify_project_before_save({"project_id": "test-erp-upgrade"}, state) is True


async def test_first_turn_multi_record_preview_forces_batch_tool():
    state = {
        "user_message": "李明2026-08-05 3.5h星云平台；王芳2026-08-06 2h智慧园区，请预检",
        "conversation_history": [],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls == [{
        "name": "batch_save_workhour",
        "arguments": {
            "text": state["user_message"],
            "dry_run": True,
        },
    }]


async def test_batch_confirmation_recovers_original_text():
    original = "李明2026-08-05 3.5h星云平台；王芳2026-08-06 2h智慧园区，请预检"
    state = {
        "user_message": "确认提交",
        "conversation_history": [
            {"role": "user", "content": original},
            {"role": "assistant", "content": "预览成功"},
            {"role": "user", "content": "确认提交"},
        ],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls[0]["name"] == "batch_save_workhour"
    assert calls[0]["arguments"] == {"text": original, "dry_run": False}


async def test_suggest_intent_is_not_routed_to_timesheet():
    state = {
        "user_message": "昨天该填什么工时？",
        "conversation_history": [],
    }
    with patch("app.services.langgraph_agent._current_date", return_value=date(2026, 8, 7)):
        calls = _forced_tool_calls_for_request(state)

    assert calls == [{"name": "suggest_workhour", "arguments": {"fill_date": "2026-08-06"}}]


async def test_aggregate_query_is_normalized_to_statistics():
    state = {
        "user_message": "查李明7月一共干了多少小时",
        "user_context": {"user_id": "current", "user_name": "测试用户"},
        "conversation_history": [],
    }
    calls = [{
        "name": "query_timesheet",
        "arguments": {
            "member_name": "李明",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
        },
    }]

    normalized = _normalize_business_tool_calls(calls, state)

    assert normalized[0]["name"] == "compute_statistics"
    assert normalized[0]["arguments"]["statistics_type"] == "user_hours"
    assert normalized[0]["arguments"]["member_name"] == "李明"


async def test_read_tool_missing_date_evidence_clarifies_before_execution(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_tool_registry", None)
    state = {
        "user_message": "查一下王芳在智慧园区项目上的工时记录",
        "conversation_history": [],
    }

    message = _tool_call_clarification(
        "query_timesheet",
        {"member_name": "王芳", "project_id": "智慧园区", "start_date": "2026-01-01", "end_date": "2026-12-31"},
        state,
    )

    assert message is not None
    assert "开始日期和结束日期" in message


async def test_person_target_fields_are_exposed_by_all_relevant_tools():
    assert "member_name" in COMPUTE_STATISTICS_SCHEMA["properties"]
    assert "member_name" in GENERATE_WEEKLY_REPORT_SCHEMA["properties"]
    assert "member_name" in SAVE_WORKHOUR_SCHEMA["properties"]


@pytest.mark.parametrize("preview_phrase", ["预校验", "检查合法性"])
async def test_batch_preview_synonyms_are_recognized(preview_phrase):
    message = f"李明2026-08-05 3.5h星云平台；王芳2026-08-06 2h智慧园区，请{preview_phrase}"
    calls = _forced_tool_calls_for_request({"user_message": message, "conversation_history": []})
    assert calls[0]["name"] == "batch_save_workhour"


async def test_monthly_comparison_is_split_and_current_month_capped():
    state = {
        "user_message": "6月、7月、8月这三个月每月总工时分别是多少",
        "user_context": {"user_id": "current", "user_name": "测试用户"},
        "conversation_history": [],
    }
    calls = [{
        "name": "compute_statistics",
        "arguments": {
            "statistics_type": "monthly_hours",
            "start_date": "2026-06-01",
            "end_date": "2026-08-31",
        },
    }]

    with patch("app.services.langgraph_agent._current_date", return_value=date(2026, 8, 7)):
        normalized = _normalize_business_tool_calls(calls, state)

    assert [call["arguments"]["end_date"] for call in normalized] == [
        "2026-06-30", "2026-07-31", "2026-08-07"
    ]


async def test_explicit_range_crossing_months_is_not_split():
    state = {
        "user_message": "上个自然周（7月26日到8月1日）总共多少工时",
        "user_context": {"user_id": "current"},
        "conversation_history": [],
    }
    calls = [{
        "name": "compute_statistics",
        "arguments": {
            "statistics_type": "weekly_hours",
            "start_date": "2026-07-26",
            "end_date": "2026-08-01",
        },
    }]

    normalized = _normalize_business_tool_calls(calls, state)

    assert len(normalized) == 1
    assert normalized[0]["arguments"]["start_date"] == "2026-07-26"


async def test_named_member_pair_expands_to_two_user_statistics_calls():
    state = {
        "user_message": "李明和王芳在ERP升级项目里各自贡献了多少比例",
        "user_context": {"user_id": "current", "user_name": "测试用户"},
        "conversation_history": [],
    }
    calls = [{
        "name": "compute_statistics",
        "arguments": {
            "project_id": "ERP升级",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
    }]

    normalized = _normalize_business_tool_calls(calls, state)

    assert [call["arguments"]["member_name"] for call in normalized] == ["李明", "王芳"]
    assert all(call["arguments"]["statistics_type"] == "user_hours" for call in normalized)


async def test_bare_project_id_for_pending_save_forces_verification_only():
    state = {
        "user_message": "P601",
        "conversation_history": [
            {"role": "user", "content": "补录2026-07-25的2小时"},
            {"role": "assistant", "content": "请提供项目ID"},
            {"role": "user", "content": "P601"},
        ],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls == [{"name": "query_project", "arguments": {"project_id": "P601"}}]


async def test_pending_save_update_recovers_explicit_user_values():
    state = {
        "user_message": "还是之前的项目，但改成2026-08-06",
        "conversation_history": [
            {"role": "user", "content": "补录2026-07-25的2小时"},
            {"role": "assistant", "content": "请提供项目ID"},
            {"role": "user", "content": "P601"},
            {"role": "assistant", "content": "项目存在"},
            {"role": "user", "content": "还是之前的项目，但改成2026-08-06"},
        ],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls[0]["name"] == "save_workhour"
    assert calls[0]["arguments"] == {
        "project_id": "P601",
        "date": "2026-08-06",
        "duration": 2.0,
        "dry_run": True,
    }


async def test_batch_reference_without_preview_does_not_authorize_tool_call():
    state = {
        "user_message": "批量录这条",
        "conversation_history": [
            {"role": "user", "content": "张三 2026-08-04 P-789 6h 测试用例编写"},
            {"role": "assistant", "content": "请明确是否需要预览"},
            {"role": "user", "content": "批量录这条"},
        ],
    }

    message = langgraph_agent._write_call_clarification(
        "batch_save_workhour",
        {"text": "张三 2026-08-04 P-789 6h 测试用例编写", "dry_run": True},
        state,
    )

    assert message is not None


async def test_pending_save_resolves_relative_weekday_and_description(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    state = {
        "user_message": "查到了，ID确实是CRM2026",
        "conversation_history": [
            {"role": "user", "content": "上周三我给客户做了一次远程支持，大概2小时，项目是CRM系统升级"},
            {"role": "assistant", "content": "请提供项目ID"},
            {"role": "user", "content": "项目ID是CRM2026"},
            {"role": "assistant", "content": "项目存在"},
            {"role": "user", "content": "查到了，ID确实是CRM2026"},
        ],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls == [{
        "name": "save_workhour",
        "arguments": {
            "project_id": "CRM2026",
            "date": "2026-07-29",
            "duration": 2.0,
            "description": "远程支持",
            "dry_run": True,
        },
    }]


async def test_failed_preview_confirmation_cannot_escalate_to_real_write(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    state = {
        "user_message": "确认",
        "conversation_history": [
            {"role": "user", "content": "补录2026-08-05的2小时，项目ID是CRM2026"},
            {"role": "assistant", "content": "工具执行失败"},
            {"role": "user", "content": "确认"},
        ],
    }

    calls = _forced_tool_calls_for_request(state)

    assert calls[0]["arguments"]["dry_run"] is True


async def test_weekly_project_workflows_are_decomposed_deterministically(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))

    forgotten = _forced_tool_calls_for_request({
        "user_message": "智慧园区项目上周的周报，项目ID我忘了",
        "conversation_history": [],
    })
    per_member = _forced_tool_calls_for_request({
        "user_message": "星云平台项目这周的周报，要包含每人小时数统计",
        "conversation_history": [],
    })
    explicit_id = _forced_tool_calls_for_request({
        "user_message": "ERP升级项目第32周的周报，项目ID是P3005",
        "conversation_history": [],
    })

    assert [call["name"] for call in forgotten] == ["query_project", "generate_weekly_report"]
    assert [call["name"] for call in per_member] == ["query_project", "compute_statistics"]
    assert explicit_id == [{"name": "generate_weekly_report", "arguments": {"week": "2026-W32"}}]


async def test_project_comparison_is_split_and_current_month_is_capped(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    pair = _forced_tool_calls_for_request({
        "user_message": "星云平台和智慧园区这周工时差多少",
        "conversation_history": [],
    })
    normalized = _normalize_business_tool_calls(
        [
            {"name": "compute_statistics", "arguments": {
                "statistics_type": "monthly_hours",
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            }},
            {"name": "compute_statistics", "arguments": {
                "statistics_type": "monthly_hours",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            }},
        ],
        {"user_message": "7月和8月环比", "conversation_history": []},
    )

    assert [call["arguments"]["project_id"] for call in pair] == ["星云平台", "智慧园区"]
    assert normalized[0]["arguments"]["end_date"] == "2026-08-07"


async def test_record_shaped_text_without_action_requires_clarification():
    assert langgraph_agent._looks_like_uncommanded_workhour_record(
        "张三 2026-08-04 P-789 6h 测试用例编写"
    ) is True
    assert langgraph_agent._looks_like_uncommanded_workhour_record(
        "预览张三 2026-08-04 P-789 6h 测试用例编写"
    ) is False


async def test_negative_dry_run_instruction_is_always_rejected():
    state = {
        "user_message": "我在智慧园区项目工作了3小时，日期2026-08-05，保存但不要dry run",
        "conversation_history": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "我在智慧园区项目工作了3小时，日期2026-08-05，保存但不要dry run"},
        ],
    }

    missing = langgraph_agent._missing_save_workhour_fields(
        {"project_id": "智慧园区", "date": "2026-08-05", "duration": 3},
        state,
    )

    assert any("写入确认" in item for item in missing)


async def test_pronoun_and_time_are_not_extracted_as_member_name():
    state = {"user_context": {"user_name": "测试用户"}}

    assert langgraph_agent._extract_member_name("我今天在智慧园区项目工作了5小时", state) is None


async def test_forced_statistics_calls_still_receive_identity_normalization(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    state = {
        "user_message": "李明今年平均每天干多少小时",
        "user_context": {"user_id": "current-user", "user_name": "测试用户"},
        "conversation_history": [],
    }

    calls = _normalize_business_tool_calls([], state)

    assert calls == [{
        "name": "compute_statistics",
        "arguments": {
            "statistics_type": "user_hours",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "member_name": "李明",
        },
    }]


async def test_current_week_is_not_capped_as_current_month(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    calls = _normalize_business_tool_calls(
        [{"name": "compute_statistics", "arguments": {
            "statistics_type": "daily_hours",
            "start_date": "2026-08-03",
            "end_date": "2026-08-09",
        }}],
        {"user_message": "这周每天工时", "conversation_history": []},
    )

    assert calls[0]["arguments"]["end_date"] == "2026-08-09"


async def test_statistics_grouping_dimension_takes_precedence_over_member_filter():
    assert langgraph_agent._infer_statistics_type(
        "王芳这周每天各干了多少小时", {"member_name": "王芳"}
    ) == "daily_hours"
    assert langgraph_agent._extract_member_name(
        "星云平台从3月到现在一共花了多少工时", {"user_context": {}}
    ) is None


async def test_quarter_each_month_expands_to_three_calls(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_current_date", lambda: date(2026, 8, 7))
    calls = _normalize_business_tool_calls(
        [{"name": "compute_statistics", "arguments": {
            "statistics_type": "monthly_hours",
            "start_date": "2026-04-01",
            "end_date": "2026-06-30",
            "project_id": "星云平台",
        }}],
        {"user_message": "星云平台项目第二季度每个月各用了多少工时", "conversation_history": []},
    )

    assert [(c["arguments"]["start_date"], c["arguments"]["end_date"]) for c in calls] == [
        ("2026-04-01", "2026-04-30"),
        ("2026-05-01", "2026-05-31"),
        ("2026-06-01", "2026-06-30"),
    ]


async def test_structured_state_inherits_entity_for_followup():
    calls = _normalize_business_tool_calls(
        [{"name": "query_timesheet", "arguments": {
            "start_date": "2026-07-27",
            "end_date": "2026-08-02",
        }}],
        {
            "user_message": "上周呢",
            "conversation_history": [],
            "business_state": {
                "last_tool": {
                    "name": "query_timesheet",
                    "params": {"member_name": "张三"},
                }
            },
        },
    )

    assert calls[0]["arguments"]["member_name"] == "张三"


async def test_structured_failed_preview_confirmation_stays_dry_run():
    calls = _forced_tool_calls_for_request({
        "user_message": "确认",
        "conversation_history": [],
        "business_state": {
            "pending_write": {
                "name": "save_workhour",
                "params": {
                    "project_id": "P601",
                    "date": "2026-08-05",
                    "duration": 2,
                    "dry_run": True,
                },
                "preview_succeeded": False,
            }
        },
    })

    assert calls[0]["arguments"]["dry_run"] is True


async def test_agent_loop_blocks_duplicate_before_second_handler(monkeypatch):
    executor = AsyncMock()
    monkeypatch.setattr(langgraph_agent, "_task_executor", executor)
    state = {
        "tool_name": "save_workhour",
        "tool_params": {
            "project_id": "P999",
            "date": "2026-08-08",
            "duration": 4.0,
        },
        "agent_iterations": 1,
        "agent_history": [{
            "iteration": 0,
            "tool": "save_workhour",
            "args": {
                "project_id": "P999",
                "date": "2026-08-08",
                "duration": 4,
            },
            "observation": {"success": True},
        }],
    }

    result = await langgraph_agent.node_execute_tool(state)

    executor.execute_single_task.assert_not_awaited()
    assert result["tool_result"]["error_code"] == "DUPLICATE_WRITE_BLOCKED"


async def test_agent_loop_is_limited_to_progressive_rag_tools():
    base = {"agent_iterations": 1, "agent_max_iterations": 5, "agent_history": []}

    assert _agent_loop_should_continue({**base, "tool_name": "save_workhour"}) == "end"
    assert _agent_loop_should_continue({**base, "tool_name": "query_timesheet"}) == "end"
    assert _agent_loop_should_continue({**base, "tool_name": "kb_keyword_search"}) == "continue"


async def test_unverified_completion_claim_pattern_is_narrow():
    assert _UNVERIFIED_COMPLETION_RE.search("该工时记录已成功提交")
    assert not _UNVERIFIED_COMPLETION_RE.search("该工时记录尚未成功提交")
    assert not _UNVERIFIED_COMPLETION_RE.search("建议确认记录是否提交成功")


async def test_failed_plan_cannot_be_summarized_as_success(monkeypatch):
    class HallucinatingLLM:
        async def generate(self, **kwargs):
            return "该工时记录已成功提交"

    monkeypatch.setattr(langgraph_agent, "_llm_client", HallucinatingLLM())
    state = {
        "plan_results": {
            "t1": {
                "success": False,
                "status": "unknown",
                "error_code": "WRITE_RESULT_UNKNOWN",
                "message": "提交结果未知，请查询确认",
            }
        },
        "user_message": "确认提交",
    }

    result = await langgraph_agent.node_summarize(state)

    assert result["llm_result"] == "提交结果未知，请查询确认。"
    assert "已成功" not in result["llm_result"]
