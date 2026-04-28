"""
Chart Builder 单元测试

覆盖场景：
- bar 图（部门工时数据）
- line 图（日维度时间序列）
- pie 图（占比类查询）
- table 降级（行数 > 50）
- None（单值、纯文本、异常输入）
- LLM 失败返回 None（不抛异常）
"""

import pytest
import json
from unittest.mock import AsyncMock, Mock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.services.chart_builder import (
    _extract_rows,
    _is_chartable,
    build_chart_option,
    ECHARTS_TOOL_SCHEMA,
)


# ─── _extract_rows 测试 ───────────────────────────────────────────────────────

class TestExtractRows:
    """测试 _extract_rows 从各种 tool_result 格式中提取数据"""

    def test_extract_from_sql_query(self):
        """sql_query 返回格式：{data: [...], columns: [...]}"""
        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept_name": "研发部", "total_hours": 120},
                    {"dept_name": "产品部", "total_hours": 80},
                ],
                "columns": ["dept_name", "total_hours"],
            },
        }
        rows = _extract_rows(tool_result)
        assert len(rows) == 2
        assert rows[0]["dept_name"] == "研发部"

    def test_extract_from_query_timesheet(self):
        """query_timesheet 返回格式：{records: [...]}"""
        tool_result = {
            "tool_name": "query_timesheet",
            "result": {
                "success": True,
                "records": [
                    {"date": "2026-04-01", "hours": 8},
                    {"date": "2026-04-02", "hours": 7.5},
                ],
            },
        }
        rows = _extract_rows(tool_result)
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-04-01"

    def test_extract_from_compute_statistics(self):
        """compute_statistics 返回格式：{items: [{name, total_hours, ...}]}"""
        tool_result = {
            "tool_name": "compute_statistics",
            "result": {
                "success": True,
                "items": [
                    {"id": "1", "name": "张三", "total_hours": 160, "work_days": 20},
                    {"id": "2", "name": "李四", "total_hours": 140, "work_days": 18},
                ],
            },
        }
        rows = _extract_rows(tool_result)
        assert len(rows) == 2
        assert rows[0]["name"] == "张三"
        assert "total_hours" in rows[0]

    def test_extract_from_compute_statistics_with_details(self):
        """compute_statistics items 含 details 字段时应展开"""
        tool_result = {
            "tool_name": "compute_statistics",
            "result": {
                "success": True,
                "items": [
                    {"name": "A项目", "total_hours": 100, "details": {"budget": 200}},
                ],
            },
        }
        rows = _extract_rows(tool_result)
        assert len(rows) == 1
        assert rows[0]["budget"] == 200  # details 展开到顶层

    def test_extract_from_query_project(self):
        """query_project 返回格式：{projects: [...]}"""
        tool_result = {
            "tool_name": "query_project",
            "result": {
                "success": True,
                "projects": [
                    {"name": "项目A", "status": "进行中"},
                    {"name": "项目B", "status": "已完成"},
                ],
            },
        }
        rows = _extract_rows(tool_result)
        assert len(rows) == 2

    def test_extract_non_dict_result(self):
        """非字典 result 应返回空列表"""
        assert _extract_rows({"result": "纯文本结果"}) == []
        assert _extract_rows({"result": 123}) == []

    def test_extract_empty_result(self):
        """空结果返回空列表"""
        assert _extract_rows({"result": {}}) == []
        assert _extract_rows({"result": {"data": []}}) == []


# ─── _is_chartable 测试 ───────────────────────────────────────────────────────

class TestIsChartable:
    """测试 _is_chartable 判断数据是否适合可视化"""

    def test_chartable_with_numeric_column(self):
        """多行 + 有数值列 → True"""
        rows = [
            {"dept": "研发", "hours": 120},
            {"dept": "产品", "hours": 80},
        ]
        assert _is_chartable(rows) is True

    def test_not_chartable_single_row(self):
        """单行 → False"""
        rows = [{"dept": "研发", "hours": 120}]
        assert _is_chartable(rows) is False

    def test_not_chartable_no_numeric(self):
        """多行但无数值列 → False"""
        rows = [
            {"dept": "研发", "status": "正常"},
            {"dept": "产品", "status": "正常"},
        ]
        assert _is_chartable(rows) is False

    def test_not_chartable_empty(self):
        """空列表 → False"""
        assert _is_chartable([]) is False

    def test_chartable_with_float_values(self):
        """浮点数也应识别为数值"""
        rows = [
            {"date": "2026-04-01", "hours": 8.0},
            {"date": "2026-04-02", "hours": 7.5},
        ]
        assert _is_chartable(rows) is True

    def test_bool_not_numeric(self):
        """bool 不应被识别为数值列"""
        rows = [
            {"name": "A", "is_active": True},
            {"name": "B", "is_active": False},
        ]
        assert _is_chartable(rows) is False


# ─── build_chart_option 测试 ──────────────────────────────────────────────────

@pytest.fixture
def mock_llm_client():
    """创建模拟 LLM 客户端"""
    client = Mock()
    return client


class TestBuildChartOptionBar:
    """bar 图：部门工时数据"""

    @pytest.mark.asyncio
    async def test_bar_chart_for_department_hours(self, mock_llm_client):
        """部门工时数据应生成 bar 图"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "name": "render_chart",
                "arguments": {
                    "chart_type": "bar",
                    "should_render": True,
                    "echarts_option": {
                        "title": {"text": "各部门工时统计"},
                        "xAxis": {"type": "category", "data": ["研发部", "产品部"]},
                        "yAxis": {"type": "value"},
                        "series": [{"type": "bar", "data": [120, 80]}],
                    },
                },
            }],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept_name": "研发部", "total_hours": 120},
                    {"dept_name": "产品部", "total_hours": 80},
                    {"dept_name": "测试部", "total_hours": 60},
                ],
            },
        }

        result = await build_chart_option(
            user_query="统计本月各部门工时",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is not None
        assert result["chart_type"] == "bar"
        assert "echarts_option" in result
        assert result["echarts_option"]["title"]["text"] == "各部门工时统计"
        mock_llm_client.generate_with_tools.assert_called_once()


class TestBuildChartOptionLine:
    """line 图：日维度时间序列"""

    @pytest.mark.asyncio
    async def test_line_chart_for_daily_trend(self, mock_llm_client):
        """日维度时间序列应生成 line 图"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "name": "render_chart",
                "arguments": {
                    "chart_type": "line",
                    "should_render": True,
                    "echarts_option": {
                        "title": {"text": "近7天工时趋势"},
                        "xAxis": {"type": "category", "data": ["04-01", "04-02"]},
                        "yAxis": {"type": "value"},
                        "series": [{"type": "line", "data": [8, 7.5]}],
                    },
                },
            }],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"date": "2026-04-01", "hours": 8},
                    {"date": "2026-04-02", "hours": 7.5},
                    {"date": "2026-04-03", "hours": 8},
                ],
            },
        }

        result = await build_chart_option(
            user_query="近7天工时趋势",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is not None
        assert result["chart_type"] == "line"
        assert "echarts_option" in result


class TestBuildChartOptionPie:
    """pie 图：占比类查询"""

    @pytest.mark.asyncio
    async def test_pie_chart_for_proportion(self, mock_llm_client):
        """占比类查询应生成 pie 图"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "name": "render_chart",
                "arguments": {
                    "chart_type": "pie",
                    "should_render": True,
                    "echarts_option": {
                        "title": {"text": "各部门工时占比"},
                        "series": [{
                            "type": "pie",
                            "data": [
                                {"name": "研发部", "value": 120},
                                {"name": "产品部", "value": 80},
                            ],
                        }],
                    },
                },
            }],
        })

        tool_result = {
            "tool_name": "compute_statistics",
            "result": {
                "success": True,
                "items": [
                    {"name": "研发部", "total_hours": 120},
                    {"name": "产品部", "total_hours": 80},
                    {"name": "测试部", "total_hours": 60},
                ],
            },
        }

        result = await build_chart_option(
            user_query="本月各部门工时占比",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is not None
        assert result["chart_type"] == "pie"
        assert "echarts_option" in result


class TestBuildChartOptionTableFallback:
    """table 降级：行数 > 50"""

    @pytest.mark.asyncio
    async def test_table_fallback_when_too_many_rows(self):
        """60 行数据应降级为 table"""
        # 无需 LLM，直接降级
        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"id": i, "value": i * 10}
                    for i in range(60)
                ],
            },
        }

        result = await build_chart_option(
            user_query="查询所有记录",
            tool_result=tool_result,
            llm_client=None,  # 即使不提供 LLM 也应返回 table
        )

        assert result is not None
        assert result["chart_type"] == "table"
        assert "fallback_table" in result
        assert len(result["fallback_table"]) == 60


class TestBuildChartOptionNone:
    """None 降级：单值、纯文本、异常输入"""

    @pytest.mark.asyncio
    async def test_none_when_single_value(self):
        """单值结果 → None"""
        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [{"total": 100}],
            },
        }

        result = await build_chart_option(
            user_query="总工时是多少",
            tool_result=tool_result,
            llm_client=None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_none_when_text_only(self):
        """纯文本结果 → None"""
        tool_result = {
            "tool_name": "save_workhour",
            "result": {
                "success": True,
                "message": "工时填报成功",
            },
        }

        result = await build_chart_option(
            user_query="填报工时",
            tool_result=tool_result,
            llm_client=None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_none_when_no_llm_client(self):
        """无 LLM 客户端且数据行数 ≤50 → None"""
        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept": "研发", "hours": 120},
                    {"dept": "产品", "hours": 80},
                ],
            },
        }

        result = await build_chart_option(
            user_query="统计各部门工时",
            tool_result=tool_result,
            llm_client=None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_none_when_no_numeric(self):
        """多行但全文本 → None"""
        tool_result = {
            "tool_name": "query_project",
            "result": {
                "success": True,
                "projects": [
                    {"name": "项目A", "status": "进行中"},
                    {"name": "项目B", "status": "已完成"},
                ],
            },
        }

        result = await build_chart_option(
            user_query="查询所有项目",
            tool_result=tool_result,
            llm_client=None,
        )

        assert result is None


class TestBuildChartOptionLLMFailure:
    """LLM 失败返回 None（不抛异常）"""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, mock_llm_client):
        """LLM 调用异常时应静默返回 None"""
        mock_llm_client.generate_with_tools = AsyncMock(
            side_effect=Exception("LLM API 超时")
        )

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept": "研发", "hours": 120},
                    {"dept": "产品", "hours": 80},
                ],
            },
        }

        # 不应抛异常
        result = await build_chart_option(
            user_query="统计各部门工时",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_stop_without_tool_calls(self, mock_llm_client):
        """LLM 以 stop 结束（没有 tool_calls）→ None"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "stop",
            "content": "这是一个文本回复",
            "tool_calls": [],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept": "研发", "hours": 120},
                    {"dept": "产品", "hours": 80},
                ],
            },
        }

        result = await build_chart_option(
            user_query="统计各部门工时",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_should_render_false(self, mock_llm_client):
        """LLM 返回 should_render=false → None"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "name": "render_chart",
                "arguments": {
                    "chart_type": "bar",
                    "should_render": False,
                },
            }],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept": "研发", "hours": 120},
                    {"dept": "产品", "hours": 80},
                ],
            },
        }

        result = await build_chart_option(
            user_query="统计各部门工时",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_content_with_json(self, mock_llm_client):
        """LLM 以 stop 结束但 content 包含合法 JSON → 解析成功"""
        echarts_json = json.dumps({
            "chart_type": "bar",
            "echarts_option": {
                "title": {"text": "测试"},
                "series": [{"type": "bar", "data": [1, 2]}],
            },
        })
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "stop",
            "content": f"这是一些说明文字\n{echarts_json}\n更多文字",
            "tool_calls": [],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"dept": "研发", "hours": 120},
                    {"dept": "产品", "hours": 80},
                ],
            },
        }

        result = await build_chart_option(
            user_query="统计各部门工时",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is not None
        assert result["chart_type"] == "bar"
        assert "echarts_option" in result


class TestBuildChartOptionEdgeCases:
    """边界情况测试"""

    @pytest.mark.asyncio
    async def test_empty_tool_result(self):
        """空 tool_result → None"""
        result = await build_chart_option(
            user_query="查询",
            tool_result={},
            llm_client=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_tool_result_with_error(self):
        """工具执行失败 → None"""
        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": False,
                "error": "SQL 执行失败",
            },
        }

        result = await build_chart_option(
            user_query="查询",
            tool_result=tool_result,
            llm_client=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_large_data_truncation(self, mock_llm_client):
        """大量数据时只给 LLM 前20行，但返回全部行（≤50）"""
        mock_llm_client.generate_with_tools = AsyncMock(return_value={
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "name": "render_chart",
                "arguments": {
                    "chart_type": "bar",
                    "should_render": True,
                    "echarts_option": {
                        "title": {"text": "测试"},
                        "series": [{"type": "bar", "data": [1]}],
                    },
                },
            }],
        })

        tool_result = {
            "tool_name": "sql_query",
            "result": {
                "success": True,
                "data": [
                    {"id": i, "value": i * 10}
                    for i in range(45)  # 45 行，触发 LLM 但不触发 table
                ],
            },
        }

        result = await build_chart_option(
            user_query="查询",
            tool_result=tool_result,
            llm_client=mock_llm_client,
        )

        assert result is not None
        assert result["chart_type"] == "bar"
        # 验证 LLM 被调用（说明行数 ≤50 走了 LLM 路径）
        mock_llm_client.generate_with_tools.assert_called_once()


class TestSchema:
    """Schema 测试"""

    def test_echarts_tool_schema_structure(self):
        """验证 ECHARTS_TOOL_SCHEMA 结构正确"""
        assert ECHARTS_TOOL_SCHEMA["type"] == "function"
        func = ECHARTS_TOOL_SCHEMA["function"]
        assert func["name"] == "render_chart"
        params = func["parameters"]["properties"]
        assert "chart_type" in params
        assert "echarts_option" in params
        assert "should_render" in params
        assert func["parameters"]["required"] == ["chart_type", "should_render"]
        assert params["chart_type"]["enum"] == ["bar", "line", "pie", "table"]
