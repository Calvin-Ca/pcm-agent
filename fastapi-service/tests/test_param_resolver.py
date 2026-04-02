"""
param_resolver 单元测试

验证 resolve_project_id / resolve_member_id 的核心逻辑：
  - 纯数字 ID 直接返回，不发 HTTP 请求
  - 名称字符串 → 调正确 URL → 精确匹配 / 模糊第一条
  - 空结果 → 返回 (None, 错误消息)
  - HTTP 异常 → 返回 (None, 错误消息)
  - 缓存命中 → 第二次调用不发 HTTP
  - clear_resolve_cache() → 缓存失效，下次重新发请求

不依赖 SpringBoot，全部使用 unittest.mock 拦截 httpx 调用。
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import httpx

from app.services.param_resolver import (
    resolve_project_id,
    resolve_member_id,
    clear_resolve_cache,
)

BASE_URL = "http://localhost:8080"
TOKEN = "Bearer test-token"


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

def _make_mock_client(json_data):
    """构造一个模拟 httpx.AsyncClient，get() 返回指定 JSON。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()  # 不抛异常

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _make_http_error_client(status_code: int = 401):
    """构造一个 get() 抛出 HTTPStatusError 的 mock 客户端。"""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock_resp,
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_cache():
    """每个测试前后清空解析缓存，避免用例间互相污染。"""
    clear_resolve_cache()
    yield
    clear_resolve_cache()


# ─── resolve_project_id 测试 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_project_id_numeric():
    """纯数字 ID 直接返回，不发任何 HTTP 请求。"""
    with patch("app.services.param_resolver.httpx.AsyncClient") as mock_cls:
        result_id, error = await resolve_project_id("123", TOKEN, BASE_URL)

    assert result_id == "123"
    assert error is None
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_project_id_exact_match():
    """名称 → 结果列表中有精确匹配 projectName → 返回对应 ID。"""
    projects = [
        {"id": "10", "projectName": "AI平台升级"},
        {"id": "20", "projectName": "AI平台"},   # 精确匹配
        {"id": "30", "projectName": "AI平台旧版"},
    ]
    mock_client = _make_mock_client(projects)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_project_id("AI平台", TOKEN, BASE_URL)

    assert result_id == "20"
    assert error is None
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    assert "projectName.contains" in call_kwargs.kwargs.get("params", {}) or \
           "projectName.contains" in (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})


@pytest.mark.asyncio
async def test_resolve_project_id_partial_match():
    """无精确匹配时取结果列表第一条。"""
    projects = [
        {"id": "55", "projectName": "AI平台-测试"},
        {"id": "66", "projectName": "AI平台-生产"},
    ]
    mock_client = _make_mock_client(projects)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_project_id("AI平台", TOKEN, BASE_URL)

    assert result_id == "55"
    assert error is None


@pytest.mark.asyncio
async def test_resolve_project_id_not_found():
    """SpringBoot 返回空列表 → 返回 (None, 错误消息)。"""
    mock_client = _make_mock_client([])

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_project_id("不存在的项目", TOKEN, BASE_URL)

    assert result_id is None
    assert error is not None
    assert "不存在的项目" in error


@pytest.mark.asyncio
async def test_resolve_project_id_http_error():
    """HTTP 4xx 异常 → 返回 (None, 错误消息)，包含状态码。"""
    mock_client = _make_http_error_client(401)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_project_id("AI平台", TOKEN, BASE_URL)

    assert result_id is None
    assert error is not None
    assert "401" in error


@pytest.mark.asyncio
async def test_resolve_project_id_cache_hit():
    """第一次查询后结果被缓存，第二次调用不再发 HTTP 请求。"""
    projects = [{"id": "99", "projectName": "智慧园区"}]
    mock_client = _make_mock_client(projects)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        id1, _ = await resolve_project_id("智慧园区", TOKEN, BASE_URL)
        id2, _ = await resolve_project_id("智慧园区", TOKEN, BASE_URL)

    assert id1 == "99"
    assert id2 == "99"
    # HTTP 客户端只被实例化一次（第二次命中缓存）
    assert mock_client.get.call_count == 1


# ─── resolve_member_id 测试 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_member_id_exact_match():
    """成员名精确匹配 entityName → 返回对应 ID。"""
    members = [
        {"id": "201", "entityName": "何思思123"},
        {"id": "202", "entityName": "何思思"},    # 精确匹配
    ]
    mock_client = _make_mock_client(members)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_member_id("何思思", TOKEN, BASE_URL)

    assert result_id == "202"
    assert error is None


@pytest.mark.asyncio
async def test_resolve_member_id_not_found():
    """用户不存在 → 返回 (None, 错误消息)。"""
    mock_client = _make_mock_client([])

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        result_id, error = await resolve_member_id("不存在的人", TOKEN, BASE_URL)

    assert result_id is None
    assert error is not None
    assert "不存在的人" in error


@pytest.mark.asyncio
async def test_clear_resolve_cache():
    """clear_resolve_cache() 后缓存失效，相同参数再次查询会重新发 HTTP 请求。"""
    projects = [{"id": "77", "projectName": "数字化转型"}]
    mock_client = _make_mock_client(projects)

    with patch("app.services.param_resolver.httpx.AsyncClient", return_value=mock_client):
        await resolve_project_id("数字化转型", TOKEN, BASE_URL)   # 第1次：发请求
        clear_resolve_cache()
        await resolve_project_id("数字化转型", TOKEN, BASE_URL)   # 第2次：缓存已清，再次发请求

    assert mock_client.get.call_count == 2
