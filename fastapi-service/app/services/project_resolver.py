"""
Project Resolver - 项目推荐

根据用户最近 30 天填报历史，按 projectId 频次取 TopK 推荐。

策略：
1. 拉取 user 最近 30 天全部历史
2. 统计 projectId 频次，取 TopK
3. TTL 缓存 5 分钟
"""

import logging
from collections import Counter
from datetime import date
from typing import Any, Dict, List, Optional

try:
    from cachetools import TTLCache
except ImportError:  # pragma: no cover
    TTLCache = None  # type: ignore[misc]

from app.services.work_type_resolver import _fetch_history

logger = logging.getLogger(__name__)

if TTLCache is not None:
    _project_cache: TTLCache = TTLCache(maxsize=500, ttl=300)  # user_id -> List[Dict]
else:
    _project_cache: Dict[str, List[Dict]] = {}  # type: ignore[assignment]


# ─── 内部辅助 ─────────────────────────────────────────────────────────────────

def _resolve_project_name(project_id: str, records: List[Dict[str, Any]]) -> str:
    """从 records 里找 project_id 对应的 projectName，找不到返回空字符串。"""
    for r in records:
        if str(r.get("projectId", "")) == project_id and r.get("projectName"):
            return str(r["projectName"])
    return ""


def _last_fill_date(project_id: str, records: List[Dict[str, Any]]) -> Optional[str]:
    """从 records 里找 project_id 最近一次填报日期。"""
    dates = []
    for r in records:
        if str(r.get("projectId", "")) == project_id:
            d = r.get("workhourDate") or r.get("date")
            if d:
                dates.append(str(d))
    if not dates:
        return None
    # 取最新日期（简单字符串比较 YYYY-MM-DD 格式有效）
    return max(dates)


# ─── 公开 API ─────────────────────────────────────────────────────────────────

async def resolve_project_suggestion(
    user_id: str,
    auth_token: Optional[str],
    base_url: str,
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """
    基于用户最近 30 天填报历史，返回 TopK 推荐项目。

    返回格式：
    [
        {"project_id": "P001", "project_name": "工时管理系统", "frequency": 12, "last_fill_date": "2026-04-27"},
        ...
    ]
    """
    cache_key = f"proj:{user_id}"
    if cache_key in _project_cache:
        return _project_cache[cache_key]

    try:
        records = await _fetch_history(user_id, None, auth_token, base_url)
    except Exception as e:
        logger.warning(f"project 推荐获取历史失败 user={user_id}: {e}")
        return []

    if not records:
        return []

    counter = Counter(str(r["projectId"]) for r in records if r.get("projectId"))
    if not counter:
        return []

    suggestions = [
        {
            "project_id": pid,
            "project_name": _resolve_project_name(pid, records),
            "frequency": freq,
            "last_fill_date": _last_fill_date(pid, records),
        }
        for pid, freq in counter.most_common(top_k)
    ]

    _project_cache[cache_key] = suggestions
    return suggestions


def clear_project_cache() -> None:
    """清空缓存。用于单元测试。"""
    if TTLCache is not None:
        _project_cache.clear()
    else:
        _project_cache.clear()
