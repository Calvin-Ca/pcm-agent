"""
project_resolver 单元测试

验证 resolve_project_suggestion 核心逻辑：
  - 历史命中：返回 Top3 推荐项目（按频次降序）
  - 历史为空：返回空列表
  - SpringBoot 不可达：返回空列表，不抛异常
  - TTL 缓存命中：第二次调用不发 HTTP
"""

import pytest
from unittest.mock import patch, AsyncMock

from app.services.project_resolver import (
    resolve_project_suggestion,
    clear_project_cache,
)

BASE_URL = "http://localhost:8080"
TOKEN = "Bearer test-token"
USER_ID = "U001"


@pytest.fixture(autouse=True)
def clean_cache():
    """每个测试前后清空缓存。"""
    clear_project_cache()
    yield
    clear_project_cache()


# ─── 辅助：构造模拟的 _fetch_history ──────────────────────────────────────────

def _make_history_records():
    """构造有 projectId 分布的 30 天历史记录。"""
    return [
        # P001 出现 5 次（最多）
        {"projectId": "P001", "projectName": "工时管理系统", "workhourDate": "2026-04-27", "workhour": 8},
        {"projectId": "P001", "projectName": "工时管理系统", "workhourDate": "2026-04-26", "workhour": 8},
        {"projectId": "P001", "projectName": "工时管理系统", "workhourDate": "2026-04-25", "workhour": 8},
        {"projectId": "P001", "projectName": "工时管理系统", "workhourDate": "2026-04-24", "workhour": 8},
        {"projectId": "P001", "projectName": "工时管理系统", "workhourDate": "2026-04-23", "workhour": 8},
        # P003 出现 3 次
        {"projectId": "P003", "projectName": "数据中台", "workhourDate": "2026-04-22", "workhour": 8},
        {"projectId": "P003", "projectName": "数据中台", "workhourDate": "2026-04-21", "workhour": 8},
        {"projectId": "P003", "projectName": "数据中台", "workhourDate": "2026-04-20", "workhour": 8},
        # P005 出现 2 次
        {"projectId": "P005", "projectName": "OA 升级", "workhourDate": "2026-04-19", "workhour": 4},
        {"projectId": "P005", "projectName": "OA 升级", "workhourDate": "2026-04-18", "workhour": 4},
    ]


# ─── 测试用例 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_project_suggestion_hits():
    """有历史记录时，返回 Top3 按频次降序排列。"""
    records = _make_history_records()

    with patch("app.services.project_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL, top_k=3)

    assert len(result) == 3
    assert result[0]["project_id"] == "P001"
    assert result[0]["project_name"] == "工时管理系统"
    assert result[0]["frequency"] == 5
    assert result[0]["last_fill_date"] == "2026-04-27"

    assert result[1]["project_id"] == "P003"
    assert result[1]["frequency"] == 3

    assert result[2]["project_id"] == "P005"
    assert result[2]["frequency"] == 2


@pytest.mark.asyncio
async def test_resolve_project_suggestion_empty():
    """历史为空时返回空列表。"""
    with patch("app.services.project_resolver._fetch_history", new_callable=AsyncMock, return_value=[]):
        result = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)

    assert result == []


@pytest.mark.asyncio
async def test_resolve_project_suggestion_no_project_id():
    """历史记录里没有 projectId 字段时返回空列表。"""
    records = [
        {"workhourDate": "2026-04-27", "workhour": 8},
        {"workhourDate": "2026-04-26", "workhour": 8},
    ]

    with patch("app.services.project_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)

    assert result == []


@pytest.mark.asyncio
async def test_resolve_project_suggestion_http_error():
    """SpringBoot 不可达时返回空列表，不抛异常。"""
    with patch("app.services.project_resolver._fetch_history", new_callable=AsyncMock, side_effect=Exception("connection refused")):
        result = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)

    assert result == []


@pytest.mark.asyncio
async def test_resolve_project_suggestion_cache_hit():
    """TTL 缓存命中：连续两次调用只发一次 HTTP 请求。"""
    records = _make_history_records()
    mock_fetch = AsyncMock(return_value=records)

    with patch("app.services.project_resolver._fetch_history", mock_fetch):
        result1 = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)
        result2 = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)

    assert result1 == result2
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_resolve_project_suggestion_top_k_limit():
    """top_k 限制返回数量。"""
    records = _make_history_records()

    with patch("app.services.project_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL, top_k=1)

    assert len(result) == 1
    assert result[0]["project_id"] == "P001"


@pytest.mark.asyncio
async def test_resolve_project_suggestion_clear_cache():
    """clear_project_cache() 后缓存失效，下次重新发请求。"""
    records = _make_history_records()
    mock_fetch = AsyncMock(return_value=records)

    with patch("app.services.project_resolver._fetch_history", mock_fetch):
        await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)
        clear_project_cache()
        await resolve_project_suggestion(USER_ID, TOKEN, BASE_URL)

    assert mock_fetch.call_count == 2
