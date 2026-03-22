"""
周报生成工具单元测试（Task 43.3*）

不依赖真实外部服务，使用 AsyncMock 替代 query_timesheet_handler 和 LLM。
测试覆盖：
- 日期解析（thisWeek / lastWeek / ISO / YYYY-MM-DD）
- 统计计算（按项目分组、百分比）
- Markdown 渲染
- 完整 handler 流程（成功、无记录、工时查询失败）
"""

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.tools.generate_weekly_report import (
    _resolve_week_range,
    _week_label,
    _build_stats,
    _render_markdown,
    generate_weekly_report_handler,
)


# ─── _resolve_week_range ──────────────────────────────────────────────────────

def test_resolve_this_week_none():
    """None 应解析为本周一到本周日"""
    start, end = _resolve_week_range(None)
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert start == expected_monday
    assert end == expected_monday + timedelta(days=6)


def test_resolve_this_week_keyword():
    """'thisWeek' 关键字解析正确"""
    start, end = _resolve_week_range("thisWeek")
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert start == expected_monday


def test_resolve_last_week():
    """'lastWeek' 应解析为上周一到上周日"""
    start, end = _resolve_week_range("lastWeek")
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday() + 7)
    assert start == expected_monday
    assert end == expected_monday + timedelta(days=6)


def test_resolve_iso_week():
    """'2024-W03' ISO 格式解析正确"""
    start, end = _resolve_week_range("2024-W03")
    # 2024 年第 3 周周一 = 2024-01-15
    assert start == date(2024, 1, 15)
    assert end == date(2024, 1, 21)


def test_resolve_date_string():
    """'YYYY-MM-DD' 应取所在周的周一"""
    start, end = _resolve_week_range("2024-03-20")
    # 2024-03-20 是周三，周一为 2024-03-18
    assert start == date(2024, 3, 18)
    assert end == date(2024, 3, 24)


def test_resolve_invalid_falls_back_to_this_week():
    """无效字符串应降级为本周"""
    start, end = _resolve_week_range("invalid-date")
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert start == expected_monday


def test_resolve_range_is_7_days():
    """解析结果区间始终为 7 天"""
    for week in [None, "thisWeek", "lastWeek", "2024-W10", "2025-06-15"]:
        start, end = _resolve_week_range(week)
        assert (end - start).days == 6


# ─── _week_label ─────────────────────────────────────────────────────────────

def test_week_label_format():
    """周标签格式应包含年、周次和日期区间"""
    start = date(2024, 1, 15)  # 2024-W03 周一
    end = date(2024, 1, 21)
    label = _week_label(start, end)
    assert "2024" in label
    assert "3" in label       # 第3周
    assert "01/15" in label
    assert "01/21" in label


# ─── _build_stats ─────────────────────────────────────────────────────────────

def test_build_stats_single_project():
    """单项目统计，占比应为 100%"""
    records = [
        {"project_name": "项目A", "project_id": "p1", "duration": 4.0},
        {"project_name": "项目A", "project_id": "p1", "duration": 4.0},
    ]
    stats = _build_stats(records)
    assert stats["total_hours"] == 8.0
    assert len(stats["projects"]) == 1
    assert stats["projects"][0]["percentage"] == 100.0


def test_build_stats_multiple_projects():
    """多项目统计，工时应分组汇总"""
    records = [
        {"project_name": "项目A", "project_id": "p1", "duration": 6.0},
        {"project_name": "项目B", "project_id": "p2", "duration": 2.0},
        {"project_name": "项目A", "project_id": "p1", "duration": 2.0},
    ]
    stats = _build_stats(records)
    assert stats["total_hours"] == 10.0
    names = [p["project_name"] for p in stats["projects"]]
    assert "项目A" in names
    assert "项目B" in names
    a = next(p for p in stats["projects"] if p["project_name"] == "项目A")
    assert a["hours"] == 8.0
    assert a["percentage"] == 80.0


def test_build_stats_sorted_by_hours_desc():
    """项目按工时降序排列"""
    records = [
        {"project_name": "小项目", "project_id": "p2", "duration": 1.0},
        {"project_name": "大项目", "project_id": "p1", "duration": 5.0},
    ]
    stats = _build_stats(records)
    assert stats["projects"][0]["project_name"] == "大项目"
    assert stats["projects"][1]["project_name"] == "小项目"


def test_build_stats_empty_records():
    """空记录列表，总工时为 0"""
    stats = _build_stats([])
    assert stats["total_hours"] == 0.0
    assert stats["projects"] == []


def test_build_stats_unknown_project_name():
    """project_name 缺失时使用「未知项目」"""
    records = [{"project_id": "p1", "duration": 3.0}]
    stats = _build_stats(records)
    assert stats["projects"][0]["project_name"] == "未知项目"


def test_build_stats_percentages_sum_to_100():
    """所有项目百分比之和应约等于 100"""
    records = [
        {"project_name": f"项目{i}", "project_id": str(i), "duration": float(i + 1)}
        for i in range(5)
    ]
    stats = _build_stats(records)
    total_pct = sum(p["percentage"] for p in stats["projects"])
    assert abs(total_pct - 100.0) < 0.5  # 允许浮点误差


# ─── _render_markdown ─────────────────────────────────────────────────────────

def test_render_markdown_contains_week_label():
    """渲染结果应包含周次标签"""
    stats = {"total_hours": 8.0, "projects": [
        {"project_name": "项目A", "hours": 8.0, "percentage": 100.0}
    ]}
    md = _render_markdown("2024年第3周（01/15-01/21）", None, stats, None)
    assert "2024年第3周" in md


def test_render_markdown_contains_project_table():
    """渲染结果应包含项目表格"""
    stats = {"total_hours": 10.0, "projects": [
        {"project_name": "项目A", "hours": 8.0, "percentage": 80.0},
        {"project_name": "项目B", "hours": 2.0, "percentage": 20.0},
    ]}
    md = _render_markdown("本周", None, stats, None)
    assert "项目A" in md
    assert "项目B" in md
    assert "80.0%" in md


def test_render_markdown_with_summary():
    """包含 llm_summary 时应渲染工作总结段落"""
    stats = {"total_hours": 8.0, "projects": [
        {"project_name": "项目A", "hours": 8.0, "percentage": 100.0}
    ]}
    md = _render_markdown("本周", None, stats, "本周主要投入了项目A的开发工作。")
    assert "工作总结" in md
    assert "本周主要投入" in md


def test_render_markdown_no_summary_no_section():
    """无 llm_summary 时不应出现工作总结标题"""
    stats = {"total_hours": 0.0, "projects": []}
    md = _render_markdown("本周", None, stats, None)
    assert "工作总结" not in md


def test_render_markdown_with_user_id():
    """传入 user_id 时应在报告中显示员工信息"""
    stats = {"total_hours": 4.0, "projects": [
        {"project_name": "项目A", "hours": 4.0, "percentage": 100.0}
    ]}
    md = _render_markdown("本周", "user_001", stats, None)
    assert "user_001" in md


# ─── generate_weekly_report_handler (完整流程) ──────────────────────────────

SAMPLE_RECORDS = [
    {"project_name": "项目A", "project_id": "p1", "duration": 6.0},
    {"project_name": "项目B", "project_id": "p2", "duration": 2.0},
]


@pytest.mark.asyncio
async def test_handler_success():
    """正常情况下应返回 success=True 和 report"""
    mock_timesheet = AsyncMock(return_value={
        "success": True,
        "records": SAMPLE_RECORDS,
    })
    # 懒导入在函数内部，patch 源模块
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        result = await generate_weekly_report_handler(week="thisWeek")

    assert result["success"] is True
    assert result["total_hours"] == 8.0
    assert result["report"] is not None
    assert "项目A" in result["report"]


@pytest.mark.asyncio
async def test_handler_no_records():
    """无工时记录时应返回 success=True，total_hours=0，无 LLM 总结"""
    mock_timesheet = AsyncMock(return_value={"success": True, "records": []})
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        result = await generate_weekly_report_handler()

    assert result["success"] is True
    assert result["total_hours"] == 0.0
    assert result["projects"] == []


@pytest.mark.asyncio
async def test_handler_timesheet_failure():
    """工时查询失败时应返回 success=False"""
    mock_timesheet = AsyncMock(return_value={
        "success": False,
        "error": "认证失败",
    })
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        result = await generate_weekly_report_handler()

    assert result["success"] is False
    assert "认证失败" in result["error"]
    assert result["report"] is None


@pytest.mark.asyncio
async def test_handler_llm_failure_graceful():
    """LLM 摘要失败时应降级跳过，周报仍然生成成功"""
    mock_timesheet = AsyncMock(return_value={
        "success": True,
        "records": SAMPLE_RECORDS,
    })
    mock_summary = AsyncMock(side_effect=Exception("LLM 服务不可用"))
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        with patch("app.tools.generate_weekly_report._generate_summary", mock_summary):
            result = await generate_weekly_report_handler()

    assert result["success"] is True
    assert result["report"] is not None
    assert "工作总结" not in result["report"]  # 没有 LLM 总结段落


@pytest.mark.asyncio
async def test_handler_passes_auth_token():
    """auth_token 应传递给 query_timesheet_handler"""
    mock_timesheet = AsyncMock(return_value={"success": True, "records": []})
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        await generate_weekly_report_handler(auth_token="Bearer test-token")

    call_kwargs = mock_timesheet.call_args.kwargs
    assert call_kwargs.get("auth_token") == "Bearer test-token"


@pytest.mark.asyncio
async def test_handler_week_passed_to_timesheet():
    """week='lastWeek' 应解析后以 start_date/end_date 传给 query_timesheet_handler"""
    mock_timesheet = AsyncMock(return_value={"success": True, "records": []})
    with patch("app.tools.query_timesheet.query_timesheet_handler", mock_timesheet):
        await generate_weekly_report_handler(week="lastWeek")

    call_kwargs = mock_timesheet.call_args.kwargs
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday() + 7)
    assert call_kwargs["start_date"] == expected_monday.isoformat()
    assert call_kwargs["end_date"] == (expected_monday + timedelta(days=6)).isoformat()
