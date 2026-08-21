"""
Batch Save Workhour 单元测试

覆盖：
- 自由文本解析
- 表格文本解析
- 日期归一化
- 工时数推断
- 重复检测
- 日上限警告 / 拒绝
- 部分失败处理
- 批量上限截断
- 项目名匹配失败 fallback
"""

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.tools.batch_save_workhour import (
    _parse_text_to_records,
    _normalize_hours,
    _is_half_step,
    _validate_records,
    _format_preview_text,
    _save_records,
    _suggest_fix,
    _extract_error_message,
    BATCH_MAX_RECORDS,
    BATCH_DAILY_HOUR_LIMIT,
    BATCH_DAILY_HOUR_BLOCK,
    batch_save_workhour_handler,
)


# ─── 工具函数测试 ─────────────────────────────────────────────────────────────

def test_normalize_hours():
    assert _normalize_hours(8) == 8.0
    assert _normalize_hours(4.5) == 4.5
    assert _normalize_hours(0) == 0.5  # 最小值保护
    assert _normalize_hours(30) == 24.0  # 最大值保护
    assert _normalize_hours("invalid") == 0.0  # 无效输入保护


def test_is_half_step():
    assert _is_half_step(0.5) is True
    assert _is_half_step(4.0) is True
    assert _is_half_step(7.5) is True
    assert _is_half_step(1.3) is False
    assert _is_half_step(2.25) is False


def test_suggest_fix():
    assert "删除" in _suggest_fix("该日期已存在记录", {"date": "2026-04-22"})
    assert "项目名" in _suggest_fix("项目不存在", {"date": "2026-04-22", "suggested_project": "AI助手"})
    assert "减少工时" in _suggest_fix("超过上限", {"hours": 10.0})
    assert "未来" in _suggest_fix("不能填报未来日期", {"date": "2026-04-22"})


def test_extract_error_message():
    # 模拟 ProblemDetail 格式
    mock_resp = MagicMock()
    mock_resp.json = lambda: {"title": "项目不存在", "detail": "null", "message": "error.projectNotFound"}
    mock_resp.status_code = 400
    import httpx
    err = httpx.HTTPStatusError("", request=MagicMock(), response=mock_resp)
    assert _extract_error_message(err) == "项目不存在"

    # detail 为 null 时跳过
    mock_resp.json = lambda: {"title": None, "detail": "null", "message": "error.generic"}
    err = httpx.HTTPStatusError("", request=MagicMock(), response=mock_resp)
    assert _extract_error_message(err) == "error.generic"


# ─── LLM 解析测试 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_text_to_records_free_text():
    """自由文本解析"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-21", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "ECharts集成", "confidence": 0.95},
                    {"date": "2026-04-22", "project_name": "B项目", "hours": 4, "work_type": "需求工作", "content": "需求会", "confidence": 0.9},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        result = await _parse_text_to_records("本周做的事：周一上午做了AI助手，下午开B项目需求会", "2026-04-25")

    assert len(result["records"]) == 2
    assert result["records"][0]["date"] == "2026-04-21"
    assert result["records"][1]["project_name"] == "B项目"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_parse_text_to_records_table_text():
    """表格文本解析"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "ECharts集成", "confidence": 0.95},
                    {"date": "2026-04-23", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "批量填报开发", "confidence": 0.95},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        text = "日期\t项目\t工时\t内容\n4/22\tAI助手\t8\tECharts集成\n4/23\tAI助手\t8\t批量填报开发"
        result = await _parse_text_to_records(text, "2026-04-25")

    assert len(result["records"]) == 2
    assert result["records"][0]["date"] == "2026-04-22"


@pytest.mark.asyncio
async def test_parse_text_to_records_truncation():
    """批量上限截断：41条输入 → 30条 + 提示"""
    records = [
        {"date": "2026-04-22", "project_name": f"项目{i}", "hours": 8, "work_type": "研发工作", "content": f"工作{i}", "confidence": 0.9}
        for i in range(41)
    ]
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": records,
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        result = await _parse_text_to_records("大量工时记录...", "2026-04-25")

    assert len(result["records"]) == BATCH_MAX_RECORDS
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_parse_text_to_records_llm_failure():
    """LLM 解析失败降级"""
    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(side_effect=Exception("API timeout"))
        MockLLM.return_value = mock_client

        result = await _parse_text_to_records("一些文本", "2026-04-25")

    assert result["records"] == []
    assert "parse_error" in result


@pytest.mark.asyncio
async def test_parse_text_to_records_no_tool_calls():
    """LLM 返回非 tool_calls"""
    mock_llm_result = {
        "finish_reason": "stop",
        "content": "我不知道怎么解析",
        "tool_calls": [],
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        result = await _parse_text_to_records("一些文本", "2026-04-25")

    assert result["records"] == []
    assert "parse_error" in result


# ─── 校验层测试 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_records_project_resolution():
    """项目名解析 + 匹配失败 fallback"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "开发", "confidence": 0.9},
        {"date": "2026-04-22", "project_name": "不存在的项目", "hours": 4, "work_type": "研发工作", "content": "测试", "confidence": 0.8},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id") as mock_resolve:
        async def side_effect(name, *args, **kwargs):
            if name == "AI助手":
                return "123", None
            return None, f"未找到名为「{name}」的项目"
        mock_resolve.side_effect = side_effect

        with patch("app.tools.batch_save_workhour._suggest_similar_project", new=AsyncMock(return_value="工时管理AI")):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    validated = result["records"]
    assert validated[0]["project_id"] == "123"
    assert validated[0]["warnings"] == []

    assert validated[1]["project_id"] is None
    assert any("未识别" in w for w in validated[1]["warnings"])
    assert validated[1]["suggested_project"] == "工时管理AI"


@pytest.mark.asyncio
async def test_validate_records_duplicate_detection():
    """重复检测：同日同项目已有记录"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "开发", "confidence": 0.9},
    ]

    existing = [
        {"workhourDate": "2026-04-22T00:00:00Z", "projectId": "123", "workhour": 4.0},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
        with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=existing)):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    assert len(result["duplicates"]) == 1
    assert result["duplicates"][0]["existing_hours"] == 4.0
    assert result["duplicates"][0]["new_hours"] == 8.0
    assert any("已有" in w for w in result["records"][0]["warnings"])


@pytest.mark.asyncio
async def test_validate_records_daily_warning():
    """日上限警告：> 8h 黄色"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 10, "work_type": "研发工作", "content": "加班", "confidence": 0.9},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
        with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    assert len(result["daily_warnings"]) == 1
    assert result["daily_warnings"][0]["total_hours"] == 10.0
    assert result["daily_warnings"][0]["level"] == "warning"
    assert str(BATCH_DAILY_HOUR_LIMIT) in result["daily_warnings"][0]["message"]
    assert len(result["daily_blockers"]) == 0


@pytest.mark.asyncio
async def test_validate_records_daily_blocker():
    """日上限拒绝：> 24h 红色"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 26, "work_type": "研发工作", "content": "不可能", "confidence": 0.9},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
        with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    assert len(result["daily_blockers"]) == 1
    assert result["daily_blockers"][0]["total_hours"] == 26.0
    assert result["daily_blockers"][0]["level"] == "blocker"
    assert str(BATCH_DAILY_HOUR_BLOCK) in result["daily_blockers"][0]["message"]
    assert len(result["daily_warnings"]) == 0


@pytest.mark.asyncio
async def test_validate_records_hours_normalization():
    """工时数归一化：非 0.5 倍数 → 归一化"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 3.3, "work_type": "研发工作", "content": "开发", "confidence": 0.9},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
        with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    assert result["records"][0]["hours"] == 3.5  # 3.3 → 3.5
    assert any("归一化" in w for w in result["records"][0]["warnings"])


@pytest.mark.asyncio
async def test_validate_records_invalid_work_type():
    """无效 work_type → 默认研发工作"""
    records = [
        {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "不存在的工作", "content": "开发", "confidence": 0.9},
    ]

    with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
        with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
            result = await _validate_records(records, "user1", "token", "http://localhost:8080")

    assert result["records"][0]["work_type"] == "研发工作"
    assert any("默认" in w for w in result["records"][0]["warnings"])


# ─── 预览文本测试 ─────────────────────────────────────────────────────────────

def test_format_preview_text():
    parsed = {
        "records": [
            {"date": "2026-04-22", "project_id": "123", "project_name": "AI助手", "hours": 8.0, "work_type": "研发工作", "content": "ECharts集成", "confidence": 0.95, "warnings": []},
            {"date": "2026-04-23", "project_id": None, "project_name": "?", "hours": 4.0, "work_type": "研发工作", "content": "周报", "confidence": 0.6, "warnings": ["项目名'?'未识别"]},
        ],
        "daily_warnings": [{"date": "2026-04-23", "total_hours": 10.0, "level": "warning", "message": "该日合计 10h，超过建议上限 8h"}],
        "daily_blockers": [],
    }
    unparsed = ["请假记录请走请假流程"]
    truncated = False

    text = _format_preview_text(parsed, unparsed, truncated)

    assert "📋 解析到 2 条工时记录" in text
    assert "✅" in text
    assert "🟡" in text or "⚠️" in text
    assert "AI助手" in text
    assert "8.0h" in text
    assert "每日工时警告" in text
    assert "未解析片段" in text
    assert "确认提交" in text


def test_format_preview_text_blocked():
    """存在 blocker 时禁用提交"""
    parsed = {
        "records": [
            {"date": "2026-04-22", "project_id": "123", "project_name": "AI助手", "hours": 26.0, "work_type": "研发工作", "content": "不可能", "confidence": 0.9, "warnings": []},
        ],
        "daily_warnings": [],
        "daily_blockers": [{"date": "2026-04-22", "total_hours": 26.0, "level": "blocker", "message": "超限"}],
    }
    text = _format_preview_text(parsed, [], False)

    assert "❌ 存在超限记录" in text
    assert "确认提交" not in text


def test_format_preview_text_truncated():
    """截断提示"""
    parsed = {"records": [], "daily_warnings": [], "daily_blockers": []}
    text = _format_preview_text(parsed, [], True)
    assert "超过单次上限" in text


# ─── 入库测试 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_records_partial_failure():
    """部分失败：4条成功，1条失败"""
    records = [
        {"date": "2026-04-22", "project_id": "123", "project_name": "AI助手", "hours": 8.0, "work_type": "研发工作", "content": "开发", "confidence": 0.9, "warnings": [], "suggested_project": None},
        {"date": "2026-04-23", "project_id": None, "project_name": "坏项目", "hours": 4.0, "work_type": "研发工作", "content": "测试", "confidence": 0.8, "warnings": ["未识别"], "suggested_project": "好项目"},
    ]

    # 第一条成功，第二条无 project_id 直接失败
    with patch("app.tools.batch_save_workhour._save_single_workhour", new=AsyncMock(return_value={
        "success": True, "workhour_id": "wh-001"
    })):
        result = await _save_records(records, "user1", "token", "http://localhost:8080")

    assert result["success"] is True  # 部分成功也算 success
    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert len(result["failed_items"]) == 1
    assert result["failed_items"][0]["project_name"] == "坏项目"
    assert "建议" in result["failed_items"][0]["suggested_fix"]


@pytest.mark.asyncio
async def test_save_records_stops_after_unknown_write_result():
    """某条提交结果未知后，应停止后续写入并返回不可伪装的 unknown。"""
    records = [
        {"date": "2026-04-22", "project_id": "123", "project_name": "AI助手", "hours": 4.0, "work_type": "研发工作", "content": "开发", "confidence": 0.9, "warnings": [], "suggested_project": None},
        {"date": "2026-04-23", "project_id": "123", "project_name": "AI助手", "hours": 4.0, "work_type": "研发工作", "content": "测试", "confidence": 0.9, "warnings": [], "suggested_project": None},
    ]
    save_mock = AsyncMock(return_value={
        "success": False,
        "status": "unknown",
        "error_code": "WRITE_RESULT_UNKNOWN",
        "suggested_fix": "请先查询确认",
    })

    with patch("app.tools.batch_save_workhour._save_single_workhour", new=save_mock):
        result = await _save_records(records, "user1", "token", "http://localhost:8080")

    assert save_mock.await_count == 1
    assert result["success"] is False
    assert result["status"] == "unknown"
    assert result["error_code"] == "WRITE_RESULT_UNKNOWN"
    assert result["unknown_count"] == 1
    assert result["message"] == "提交结果未知，请查询确认"


# ─── Handler 端到端测试 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_empty_text():
    """空文本应返回错误"""
    result = await batch_save_workhour_handler(text="", dry_run=True)
    assert result["success"] is False
    assert "不能为空" in result["error"]


@pytest.mark.asyncio
async def test_handler_dry_run_success():
    """dry_run=true 完整流程"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "开发", "confidence": 0.95},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
            with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
                result = await batch_save_workhour_handler(
                    text="4/22 AI助手 8h 开发",
                    dry_run=True,
                    context={"user_id": "user1"},
                    auth_token="token",
                )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert "preview_text" in result
    assert len(result["parsed_records"]) == 1
    assert result["summary"]["total_records"] == 1
    assert result["summary"]["blocked"] is False


@pytest.mark.asyncio
async def test_handler_dry_run_blocked():
    """dry_run=true 但存在 blocker"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-22", "project_name": "AI助手", "hours": 26, "work_type": "研发工作", "content": "不可能", "confidence": 0.9},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
            with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
                result = await batch_save_workhour_handler(
                    text="4/22 AI助手 26h",
                    dry_run=True,
                    context={"user_id": "user1"},
                    auth_token="token",
                )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["summary"]["blocked"] is True
    assert len(result["daily_blockers"]) == 1


@pytest.mark.asyncio
async def test_handler_dry_run_false():
    """dry_run=false 实际入库"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-22", "project_name": "AI助手", "hours": 8, "work_type": "研发工作", "content": "开发", "confidence": 0.95},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
            with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
                with patch("app.tools.batch_save_workhour._save_single_workhour", new=AsyncMock(return_value={
                    "success": True, "workhour_id": "wh-001"
                })):
                    result = await batch_save_workhour_handler(
                        text="4/22 AI助手 8h 开发",
                        dry_run=False,
                        context={"user_id": "user1"},
                        auth_token="token",
                    )

    assert result["success"] is True
    assert result["dry_run"] is False
    assert result["success_count"] == 1
    assert result["failed_count"] == 0
    assert "summary_text" in result
    assert "✅" in result["summary_text"]


@pytest.mark.asyncio
async def test_handler_dry_run_false_all_blocked():
    """dry_run=false 但全部记录被 blocker 过滤"""
    mock_llm_result = {
        "finish_reason": "tool_calls",
        "tool_calls": [{
            "name": "parse_workhour_records",
            "arguments": {
                "records": [
                    {"date": "2026-04-22", "project_name": "AI助手", "hours": 26, "work_type": "研发工作", "content": "不可能", "confidence": 0.9},
                ],
                "unparsed_segments": [],
            }
        }]
    }

    with patch("app.tools.batch_save_workhour.LLMClient") as MockLLM:
        mock_client = MagicMock()
        mock_client.generate_with_tools = AsyncMock(return_value=mock_llm_result)
        MockLLM.return_value = mock_client

        with patch("app.tools.batch_save_workhour.resolve_project_id", new=AsyncMock(return_value=("123", None))):
            with patch("app.tools.batch_save_workhour._fetch_workhour_by_range", new=AsyncMock(return_value=[])):
                result = await batch_save_workhour_handler(
                    text="4/22 AI助手 26h",
                    dry_run=False,
                    context={"user_id": "user1"},
                    auth_token="token",
                )

    assert result["success"] is False
    assert "所有记录均因超限" in result["error"]
