"""
hours_resolver 单元测试

验证 resolve_hours_suggestion 核心逻辑：
  - (user, project) 历史命中：返回中位数
  - (user, project) 为空，user 有数据：返回 user 全部历史中位数
  - 完全无历史：返回默认 8.0
  - SpringBoot 不可达：返回 8.0，不抛异常
  - TTL 缓存命中：第二次调用不发 HTTP
"""

import pytest
from unittest.mock import patch, AsyncMock

from app.services.hours_resolver import (
    resolve_hours_suggestion,
    clear_hours_cache,
    DEFAULT_HOURS,
)

BASE_URL = "http://localhost:8080"
TOKEN = "Bearer test-token"
USER_ID = "U001"
PROJECT_ID = "P001"


@pytest.fixture(autouse=True)
def clean_cache():
    """每个测试前后清空缓存。"""
    clear_hours_cache()
    yield
    clear_hours_cache()


# ─── 测试用例 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_hours_pair_hits():
    """(user, project) 有历史时返回该 pair 的工时中位数。"""
    # 8, 8, 8, 4, 4 → median = 8
    records = [
        {"projectId": "P001", "workhour": 8},
        {"projectId": "P001", "workhour": 8},
        {"projectId": "P001", "workhour": 8},
        {"projectId": "P001", "workhour": 4},
        {"projectId": "P001", "workhour": 4},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 8.0


@pytest.mark.asyncio
async def test_resolve_hours_pair_median_odd():
    """奇数条记录时中位数是中间值。"""
    records = [
        {"projectId": "P001", "workhour": 2},
        {"projectId": "P001", "workhour": 4},
        {"projectId": "P001", "workhour": 8},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 4.0


@pytest.mark.asyncio
async def test_resolve_hours_pair_median_even():
    """偶数条记录时中位数是中间两数平均值。"""
    records = [
        {"projectId": "P001", "workhour": 2},
        {"projectId": "P001", "workhour": 4},
        {"projectId": "P001", "workhour": 6},
        {"projectId": "P001", "workhour": 8},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 5.0  # (4 + 6) / 2


@pytest.mark.asyncio
async def test_resolve_hours_fallback_to_user():
    """(user, project) 无数据时降级到 user 全部历史中位数。"""
    # 第一次调用 (user, project) 返回空
    # 第二次调用 (user, None) 返回 user 全部历史
    pair_records = []  # P001 无历史
    user_records = [
        {"projectId": "P002", "workhour": 6},
        {"projectId": "P002", "workhour": 6},
        {"projectId": "P003", "workhour": 10},
    ]  # median = 6

    mock_fetch = AsyncMock(side_effect=[pair_records, user_records])

    with patch("app.services.hours_resolver._fetch_history", mock_fetch):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 6.0
    assert mock_fetch.call_count == 2
    # 第二次调用 project_id 为 None（通过位置参数）
    second_call_args = mock_fetch.call_args_list[1].args
    assert second_call_args[1] is None  # project_id 是第2个位置参数


@pytest.mark.asyncio
async def test_resolve_hours_fallback_to_default():
    """user 也完全无历史时返回默认 8.0。"""
    mock_fetch = AsyncMock(return_value=[])

    with patch("app.services.hours_resolver._fetch_history", mock_fetch):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == DEFAULT_HOURS


@pytest.mark.asyncio
async def test_resolve_hours_http_error():
    """SpringBoot 不可达时返回默认值 8.0，不抛异常。"""
    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, side_effect=Exception("connection refused")):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == DEFAULT_HOURS


@pytest.mark.asyncio
async def test_resolve_hours_cache_hit():
    """TTL 缓存命中：连续两次调用只发一次 HTTP 请求。"""
    records = [
        {"projectId": "P001", "workhour": 8},
        {"projectId": "P001", "workhour": 8},
    ]
    mock_fetch = AsyncMock(return_value=records)

    with patch("app.services.hours_resolver._fetch_history", mock_fetch):
        result1 = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)
        result2 = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result1 == result2 == 8.0
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_resolve_hours_none_project_id():
    """project_id 为 None 时也能正常工作。"""
    records = [
        {"projectId": "P001", "workhour": 4},
        {"projectId": "P002", "workhour": 8},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, None, TOKEN, BASE_URL)

    assert result == 6.0  # median of [4, 8]


@pytest.mark.asyncio
async def test_resolve_hours_string_workhour():
    """workhour 为字符串数字时也能正确解析。"""
    records = [
        {"projectId": "P001", "workhour": "8.0"},
        {"projectId": "P001", "workhour": "4.5"},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 6.25  # (8.0 + 4.5) / 2


@pytest.mark.asyncio
async def test_resolve_hours_invalid_workhour():
    """workhour 无效值时跳过，用有效值计算。"""
    records = [
        {"projectId": "P001", "workhour": "invalid"},
        {"projectId": "P001", "workhour": 8},
        {"projectId": "P001", "workhour": None},
    ]

    with patch("app.services.hours_resolver._fetch_history", new_callable=AsyncMock, return_value=records):
        result = await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert result == 8.0  # 只有 8 有效


@pytest.mark.asyncio
async def test_resolve_hours_clear_cache():
    """clear_hours_cache() 后缓存失效，下次重新发请求。"""
    records = [
        {"projectId": "P001", "workhour": 8},
    ]
    mock_fetch = AsyncMock(return_value=records)

    with patch("app.services.hours_resolver._fetch_history", mock_fetch):
        await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)
        clear_hours_cache()
        await resolve_hours_suggestion(USER_ID, PROJECT_ID, TOKEN, BASE_URL)

    assert mock_fetch.call_count == 2
