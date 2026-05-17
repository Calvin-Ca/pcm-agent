"""save_workhour dry_run：预览不写库（MCP Phase 3 G2）。"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.save_workhour import save_workhour_handler

pytestmark = pytest.mark.asyncio


async def _resolvers_ok():
    """patch 项目/工作日历/工种解析为确定值，隔离外部依赖。"""
    return patch.multiple(
        "app.tools.save_workhour",
        resolve_project_id=AsyncMock(return_value=("123", None)),
        _get_workhour_type_for_date=AsyncMock(return_value="正常工时"),
        resolve_work_type=AsyncMock(return_value="开发"),
    )


async def test_dry_run_true_does_not_post():
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            r = await save_workhour_handler(
                project_id="AI平台", date="2026-05-10", duration=8,
                description="开发", dry_run=True, auth_token="t",
            )
    assert r["success"] is True
    assert r["dry_run"] is True
    assert r["preview"]["payload"]["projectId"] == "123"
    assert r["preview"]["payload"]["workhour"] == 8
    mock_post.assert_not_called()


async def test_dry_run_false_attempts_post():
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=RuntimeError("posted"))) as mock_post:
            await save_workhour_handler(
                project_id="AI平台", date="2026-05-10", duration=8,
                dry_run=False, auth_token="t",
            )
    mock_post.assert_called()


async def test_dry_run_validation_fail_no_post():
    """未来日期触发 _validate_date 失败：dry_run 下校验失败也不 POST。"""
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            r = await save_workhour_handler(
                project_id="AI平台", date="2099-01-01", duration=8,
                dry_run=True, auth_token="t",
            )
    assert r["success"] is False
    mock_post.assert_not_called()
