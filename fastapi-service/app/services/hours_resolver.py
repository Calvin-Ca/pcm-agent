"""
Hours Resolver - 工时数推荐

根据 (user_id, project_id) 历史中位数 → user_id 工作日中位数 → 默认 8.0。

策略：
1. 先拉取 (user, project) 历史，取工时中位数
2. 该 pair 无数据时，拉取 user 全部历史取中位数
3. 仍无数据时返回默认 8.0
4. TTL 缓存 5 分钟
"""

import logging
import statistics
from typing import Any, Dict, Optional

try:
    from cachetools import TTLCache
except ImportError:  # pragma: no cover
    TTLCache = None  # type: ignore[misc]

from app.services.work_type_resolver import _fetch_history

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 8.0

if TTLCache is not None:
    _hours_cache: TTLCache = TTLCache(maxsize=500, ttl=300)  # "hrs:{user_id}:{project_id}" -> float
else:
    _hours_cache: Dict[str, float] = {}  # type: ignore[assignment]


# ─── 内部辅助 ─────────────────────────────────────────────────────────────────

def _extract_hours(records: list) -> list[float]:
    """从 records 提取有效工时数，返回 float 列表。"""
    hours_list: list[float] = []
    for r in records:
        h = r.get("workhour") or r.get("hours")
        if h is not None:
            try:
                hours_list.append(float(h))
            except (ValueError, TypeError):
                continue
    return hours_list


# ─── 公开 API ─────────────────────────────────────────────────────────────────

async def resolve_hours_suggestion(
    user_id: str,
    project_id: Optional[str],
    auth_token: Optional[str],
    base_url: str,
) -> float:
    """
    基于历史记录推荐工时数。

    降级链路：
    1. (user, project) 30 天历史中位数
    2. user 全部 30 天历史中位数
    3. 默认 8.0
    """
    cache_key = f"hrs:{user_id}:{project_id or 'global'}"
    if cache_key in _hours_cache:
        return _hours_cache[cache_key]

    try:
        # 1. 先拉 (user, project) 历史
        records = await _fetch_history(user_id, project_id, auth_token, base_url)
        hours_list = _extract_hours(records)

        if hours_list:
            result = statistics.median(hours_list)
            _hours_cache[cache_key] = result
            logger.info(f"hours (user×project) 中位数: {result} ({user_id}×{project_id})")
            return result

        # 2. 降级：拉 user 全部历史
        all_records = await _fetch_history(user_id, None, auth_token, base_url)
        all_hours = _extract_hours(all_records)

        if all_hours:
            result = statistics.median(all_hours)
            _hours_cache[cache_key] = result
            logger.info(f"hours (user) 中位数: {result} ({user_id})")
            return result

        # 3. 默认值
        _hours_cache[cache_key] = DEFAULT_HOURS
        logger.info(f"hours 无历史，兜底为 {DEFAULT_HOURS} ({user_id})")
        return DEFAULT_HOURS
    except Exception as e:
        logger.warning(f"hours 推荐获取历史失败 user={user_id}: {e}")
        return DEFAULT_HOURS


def clear_hours_cache() -> None:
    """清空缓存。用于单元测试。"""
    if TTLCache is not None:
        _hours_cache.clear()
    else:
        _hours_cache.clear()
