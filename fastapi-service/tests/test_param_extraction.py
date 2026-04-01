"""
Layer 2：参数提取精度测试

在 Layer 1 通过（意图分类正确）的基础上，
验证 tool_params 中各参数是否被正确提取。

断言规则：
  - params 中的字段：精确匹配（经 date_resolver 动态替换后）
  - params_exists 中的字段：只检查"存在且非空"
  - params_fuzzy 中的字段：跳过值校验
  - informal_date / date_range 类 sub_type：日期字段只做存在性检查

运行：
    pytest tests/test_param_extraction.py -v --tb=short
"""

import pytest
from typing import Any

from tests.utils.test_data_loader import load_tool_execution_cases
from tests.utils.date_resolver import resolve_relative_dates, should_skip_date_assertion


def build_state(case: dict[str, Any]) -> dict:
    """复用 Layer 1 的 state 构建逻辑"""
    from tests.test_classification_accuracy import build_state as _build
    return _build(case)


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        cases = load_tool_execution_cases()
        metafunc.parametrize(
            "case",
            cases,
            ids=[c["id"] for c in cases],
        )


@pytest.mark.asyncio
async def test_param_extraction(case: dict[str, Any]):
    """
    验证工具调用参数提取是否正确。

    若意图分类本身就错了，跳过本测试（避免掩盖 Layer 1 的问题）。
    """
    from app.services.langgraph_agent import node_llm_with_tools

    state = build_state(case)
    result = await node_llm_with_tools(state)

    # 意图分类错误时跳过，不在此处报告（Layer 1 负责）
    if result.get("intent") != "tool_execution":
        pytest.skip(
            f"意图分类错误（intent={result.get('intent')!r}），由 Layer 1 负责报告"
        )

    params = result.get("tool_params", {})
    expected = case["expected"]
    sub_type = case.get("sub_type", "")

    fuzzy_keys = set(expected.get("params_fuzzy", []))
    exists_keys = set(expected.get("params_exists", []))
    expected_params = expected.get("params", {}).copy()

    # ── 动态替换相对日期 ──
    skip_date = should_skip_date_assertion(sub_type)
    if expected.get("date_relative") and not skip_date:
        expected_params = resolve_relative_dates(expected_params, sub_type=sub_type)

    # ── 精确匹配 ──
    date_keys = {"start_date", "end_date", "date"}
    for key, expected_val in expected_params.items():
        if key in fuzzy_keys:
            continue
        if skip_date and key in date_keys:
            # informal_date 等场景：日期只检查存在性
            assert params.get(key), (
                f"\n  [{case['id']}] 参数 {key!r} 缺失\n"
                f"  输入: {case['input']!r}"
            )
            continue
        actual_val = params.get(key)
        assert actual_val == expected_val, (
            f"\n  [{case['id']}] 参数 {key!r} 值错误\n"
            f"  输入: {case['input']!r}\n"
            f"  期望: {expected_val!r}\n"
            f"  实际: {actual_val!r}\n"
            f"  sub_type: {sub_type}"
        )

    # ── 存在性检查 ──
    for key in exists_keys:
        assert params.get(key), (
            f"\n  [{case['id']}] 参数 {key!r} 缺失或为空\n"
            f"  输入: {case['input']!r}\n"
            f"  实际 tool_params: {params}"
        )
