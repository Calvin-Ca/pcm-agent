"""
Layer 1：意图分类精度测试

验证 node_llm_with_tools 对每条测试用例是否输出正确的：
  - intent（tool_execution / knowledge_qa / general_chat / clarify）
  - tool_name（query_timesheet / save_workhour / query_project / null）

不依赖 SpringBoot，纯 LLM 行为测试。

运行：
    pytest tests/test_classification_accuracy.py -v --tb=short
    pytest tests/test_classification_accuracy.py -v --tb=short \
        --json-report --json-report-file=reports/layer1.json
"""

import pytest
from typing import Any

from tests.utils.test_data_loader import load_all_cases


# ─── 构建 AgentState ──────────────────────────────────────────────────────────

def build_state(case: dict[str, Any]) -> dict:
    ctx = case["user_context"]

    from datetime import date, timedelta
    t = date.today()
    ws = t - timedelta(days=t.weekday())
    we = ws + timedelta(days=6)
    lws = ws - timedelta(weeks=1)
    lwe = lws + timedelta(days=6)
    today = t.strftime("%Y-%m-%d")

    ms = t.replace(day=1)
    if t.month == 12:
        me = t.replace(year=t.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        me = t.replace(month=t.month + 1, day=1) - timedelta(days=1)
    lme = ms - timedelta(days=1)
    lms = lme.replace(day=1)

    # 使用真实的 system.yaml（与生产环境一致）
    from app.services.prompt_manager import get_prompt_manager
    pm = get_prompt_manager()
    system_content = pm.format(
        "system",
        user_id=ctx.get("user_id", "1001"),
        user_name=ctx.get("user_name", "测试用户"),
        entity_type=ctx.get("entity_type", "employee"),
        department_id=ctx.get("department_id", "dept_01"),
        today=today,
        week_start=ws.strftime("%Y-%m-%d"),
        week_end=we.strftime("%Y-%m-%d"),
        last_week_start=lws.strftime("%Y-%m-%d"),
        last_week_end=lwe.strftime("%Y-%m-%d"),
        month_start=ms.strftime("%Y-%m-%d"),
        month_end=me.strftime("%Y-%m-%d"),
        last_month_start=lms.strftime("%Y-%m-%d"),
        last_month_end=lme.strftime("%Y-%m-%d"),
    )

    return {
        "user_message": case["input"],
        "user_context": {
            "user_id": ctx.get("user_id", "1001"),
            "user_name": ctx.get("user_name", "测试用户"),
            "entity_type": ctx.get("entity_type", "employee"),
            "department_id": ctx.get("department_id", "dept_01"),
            "auth_token": "Bearer test-token",
        },
        "session_id": "test-layer1-session",
        "conversation_history": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": case["input"]},
        ],
        "intent": None,
        "tool_name": None,
        "tool_params": {},
        "query": "",
        "clarify_message": None,
        "llm_result": None,
        "error": None,
    }


# ─── 测试用例加载 ─────────────────────────────────────────────────────────────

def pytest_generate_tests(metafunc):
    """动态参数化：从 data/ 目录加载所有用例"""
    if "case" in metafunc.fixturenames:
        cases = load_all_cases()
        metafunc.parametrize(
            "case",
            cases,
            ids=[c["id"] for c in cases],
        )


# ─── Layer 1 测试 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_classification(case: dict[str, Any]):
    """
    验证意图分类是否正确。

    断言：
    1. result["intent"] == expected["intent"]
    2. 若 expected["tool_name"] 非 null，则 result["tool_name"] 也要匹配
    """
    from app.services.langgraph_agent import node_llm_with_tools

    state = build_state(case)
    result = await node_llm_with_tools(state)

    expected = case["expected"]
    expected_intent = expected["intent"]
    expected_tool = expected.get("tool_name")

    assert result.get("intent") == expected_intent, (
        f"\n  输入: {case['input']!r}\n"
        f"  期望 intent: {expected_intent}\n"
        f"  实际 intent: {result.get('intent')}\n"
        f"  sub_type: {case.get('sub_type')}\n"
        f"  notes: {case.get('notes')}"
    )

    if expected_tool:
        assert result.get("tool_name") == expected_tool, (
            f"\n  输入: {case['input']!r}\n"
            f"  期望 tool_name: {expected_tool}\n"
            f"  实际 tool_name: {result.get('tool_name')}"
        )
