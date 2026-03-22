"""
工时填报工具单元测试（Task 44.4*）

不依赖真实外部服务，使用 AsyncMock / patch 替代 HTTP 调用。
测试覆盖：
- 工时步长校验（0.5 整数倍）
- 日期格式和未来日期校验
- 当日工时合计超限校验
- 填报成功（多种 SpringBoot 响应格式）
- 网络/HTTP 错误处理
"""

import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.tools.save_workhour import (
    _validate_duration,
    _validate_date,
    save_workhour_handler,
)


# ─── _validate_duration ───────────────────────────────────────────────────────

@pytest.mark.parametrize("duration", [0.5, 1.0, 1.5, 2.0, 7.5, 8.0, 24.0])
def test_validate_duration_valid(duration):
    """合法工时步长（0.5 整数倍，范围 0.5~24）应返回 None"""
    assert _validate_duration(duration) is None


@pytest.mark.parametrize("duration", [0.3, 0.7, 1.2, 3.3, 7.9])
def test_validate_duration_not_half_step(duration):
    """非 0.5 整数倍应返回错误信息"""
    result = _validate_duration(duration)
    assert result is not None
    assert "0.5" in result


def test_validate_duration_zero():
    """0 工时应返回错误"""
    assert _validate_duration(0) is not None


def test_validate_duration_negative():
    """负工时应返回错误"""
    assert _validate_duration(-1.0) is not None


def test_validate_duration_exceeds_24():
    """超过 24h 应返回错误"""
    result = _validate_duration(24.5)
    assert result is not None
    assert "24" in result


# ─── _validate_date ───────────────────────────────────────────────────────────

def test_validate_date_valid_today():
    """今天的日期应通过校验"""
    today = date.today().isoformat()
    assert _validate_date(today) is None


def test_validate_date_valid_past():
    """过去的日期应通过校验"""
    past = (date.today() - timedelta(days=30)).isoformat()
    assert _validate_date(past) is None


def test_validate_date_future():
    """未来日期应返回错误"""
    future = (date.today() + timedelta(days=1)).isoformat()
    result = _validate_date(future)
    assert result is not None
    assert "未来" in result


def test_validate_date_invalid_format():
    """错误格式（如 20240315）应返回错误"""
    result = _validate_date("20240315")
    assert result is not None
    assert "格式" in result or "无效" in result


def test_validate_date_empty():
    """空字符串应返回格式错误"""
    result = _validate_date("")
    assert result is not None


# ─── save_workhour_handler 参数校验 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_missing_project_id():
    """缺少 project_id 应返回 success=False"""
    result = await save_workhour_handler(
        date=date.today().isoformat(),
        duration=4.0,
    )
    assert result["success"] is False
    assert "project_id" in result["error"] or "项目" in result["error"]


@pytest.mark.asyncio
async def test_handler_missing_date():
    """缺少 date 应返回 success=False"""
    result = await save_workhour_handler(
        project_id="p1",
        duration=4.0,
    )
    assert result["success"] is False
    assert "日期" in result["error"] or "date" in result["error"]


@pytest.mark.asyncio
async def test_handler_invalid_duration():
    """非 0.5 步长应返回 success=False"""
    result = await save_workhour_handler(
        project_id="p1",
        date=date.today().isoformat(),
        duration=1.3,
    )
    assert result["success"] is False
    assert "0.5" in result["error"]


@pytest.mark.asyncio
async def test_handler_future_date():
    """未来日期应返回 success=False"""
    future = (date.today() + timedelta(days=1)).isoformat()
    result = await save_workhour_handler(
        project_id="p1",
        date=future,
        duration=4.0,
    )
    assert result["success"] is False
    assert "未来" in result["error"]


# ─── 当日工时超限校验 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_daily_total_exceeded():
    """当日已有工时 + 本次工时 > 24h 时应返回 success=False"""
    mock_daily = AsyncMock(return_value=22.0)
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        result = await save_workhour_handler(
            project_id="p1",
            date=date.today().isoformat(),
            duration=3.0,
        )
    assert result["success"] is False
    assert "24" in result["error"]


@pytest.mark.asyncio
async def test_handler_daily_total_exactly_24_allowed():
    """当日已有工时 + 本次工时 == 24h 时应允许填报"""
    mock_daily = AsyncMock(return_value=16.0)
    mock_post = AsyncMock(return_value=MagicMock(
        json=lambda: {"id": "record-1"},
        raise_for_status=lambda: None,
    ))
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=MagicMock(
                json=lambda: {"id": "record-1"},
                raise_for_status=lambda: None,
            ))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="p1",
                date=date.today().isoformat(),
                duration=8.0,
            )
    assert result["success"] is True


# ─── 填报成功（多种 SpringBoot 响应格式）───────────────────────────────────

def _make_http_mock(json_data):
    """构造 httpx response mock"""
    resp_mock = MagicMock()
    resp_mock.json = lambda: json_data
    resp_mock.raise_for_status = MagicMock()
    return resp_mock


@pytest.mark.asyncio
async def test_handler_success_with_id():
    """SpringBoot 返回 {id: ...} 时 record_id 应有值"""
    mock_daily = AsyncMock(return_value=0.0)
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=_make_http_mock({"id": "abc-123"}))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="p1",
                date=date.today().isoformat(),
                duration=4.0,
                description="开发工作",
            )

    assert result["success"] is True
    assert result["record_id"] == "abc-123"
    assert result["duration"] == 4.0
    assert result["project_id"] == "p1"


@pytest.mark.asyncio
async def test_handler_success_springboot_nested_data():
    """SpringBoot 返回 {success: true, data: {id: ...}} 格式"""
    mock_daily = AsyncMock(return_value=0.0)
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=_make_http_mock({
                "success": True,
                "data": {"id": "nested-456"},
            }))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="p2",
                date=date.today().isoformat(),
                duration=2.5,
            )

    assert result["success"] is True
    assert result["record_id"] == "nested-456"


@pytest.mark.asyncio
async def test_handler_springboot_returns_failure():
    """SpringBoot 返回 {success: false} 时应返回 success=False"""
    mock_daily = AsyncMock(return_value=0.0)
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=_make_http_mock({
                "success": False,
                "message": "项目不存在",
            }))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="bad-project",
                date=date.today().isoformat(),
                duration=4.0,
            )

    assert result["success"] is False
    assert "项目不存在" in result["error"]


# ─── 网络错误处理 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_http_network_error():
    """网络请求异常应返回 success=False"""
    import httpx

    mock_daily = AsyncMock(return_value=0.0)
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=httpx.ConnectError("连接拒绝"))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="p1",
                date=date.today().isoformat(),
                duration=4.0,
            )

    assert result["success"] is False
    assert "网络" in result["error"] or "失败" in result["error"]


# ─── 成功消息验证 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_success_message_contains_details():
    """成功消息应包含日期和工时信息"""
    mock_daily = AsyncMock(return_value=0.0)
    today = date.today().isoformat()
    with patch("app.tools.save_workhour._get_daily_total", mock_daily):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=_make_http_mock({"id": "x"}))
            mock_client_cls.return_value = mock_ctx

            result = await save_workhour_handler(
                project_id="p1",
                date=today,
                duration=6.0,
            )

    assert today in result["message"]
    assert "6.0" in result["message"] or "6" in result["message"]
