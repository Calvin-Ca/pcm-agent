"""
Work Type Resolver - 工时类别智能识别

根据 (user_id, project_id) 二维历史取众数 → user_id 单维 → LLM 兜底 → 默认值。

候选值：研发工作 / 商务工作 / 综合管理工作 / 履约工作 / 需求工作
"""

import logging
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

try:
    from cachetools import TTLCache
except ImportError:  # pragma: no cover
    TTLCache = None  # type: ignore[misc]

import httpx

logger = logging.getLogger(__name__)

WORK_TYPE_CANDIDATES = ["研发工作", "商务工作", "综合管理工作", "履约工作", "需求工作"]
DEFAULT_WORK_TYPE = "研发工作"

if TTLCache is not None:
    _pair_cache: TTLCache = TTLCache(maxsize=500, ttl=300)   # (user_id, project_id) -> work_type
    _user_cache: TTLCache = TTLCache(maxsize=200, ttl=300)   # user_id -> work_type
else:
    # 无 cachetools 时的降级（理论上不应出现）
    _pair_cache: Dict[Any, str] = {}  # type: ignore[assignment]
    _user_cache: Dict[str, str] = {}  # type: ignore[assignment]


# ─── 内部实现 ─────────────────────────────────────────────────────────────────

async def _fetch_history(
    user_id: str,
    project_id: Optional[str],
    auth_token: Optional[str],
    base_url: str,
) -> List[Dict[str, Any]]:
    """调用 SpringBoot 拿当前用户最近 30 天填报历史，可选按 project_id 过滤。"""
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    end = date.today()
    start = end - timedelta(days=30)

    params: Dict[str, Any] = {
        "memberId": user_id,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    }
    if project_id:
        params["projectId"] = project_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/workhour/by-date-range",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        records = data if isinstance(data, list) else data.get("content", [])
        return records if isinstance(records, list) else []
    except Exception as e:
        logger.warning(f"workType 历史查询失败 user={user_id} project={project_id}: {e}")
        return []


def _mode_work_type(records: List[Dict[str, Any]]) -> Optional[str]:
    """从历史记录取 workType 众数；候选外的值忽略。"""
    types = [r.get("workType") for r in records if r.get("workType") in WORK_TYPE_CANDIDATES]
    if not types:
        return None
    return Counter(types).most_common(1)[0][0]


async def _llm_infer_work_type(description: str) -> Optional[str]:
    """LLM 兜底：从 5 候选选一个，结果不在候选列表则返回 None。"""
    if not description:
        return None
    try:
        from app.services.llm_client import LLMClient
        client = LLMClient(env_prefix="INTENT", temperature=0.1, max_tokens=20)
        prompt = (
            f"从候选列表选最匹配的工时类型。只返回类型名称，不解释。\n"
            f"候选：{', '.join(WORK_TYPE_CANDIDATES)}\n"
            f"工作描述：{description}"
        )
        result = await client.generate(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
            extra={"chat_template_kwargs": {"enable_thinking": False}},
        )
        content = (result or "").strip()
        for cand in WORK_TYPE_CANDIDATES:
            if cand in content:
                logger.info(f"workType LLM 推断: '{cand}' (raw='{content}')")
                return cand
        logger.warning(f"workType LLM 返回非候选值: '{content}'")
        return None
    except Exception as e:
        logger.warning(f"workType LLM 推断失败: {e}")
        return None


# ─── 公开 API ─────────────────────────────────────────────────────────────────

async def resolve_work_type(
    user_id: str,
    project_id: Optional[str],
    description: str,
    auth_token: Optional[str],
    base_url: str,
) -> str:
    """
    主入口：按 (user_id, project_id) 二维分组取众数 → user_id 单维 → LLM → 默认。
    """
    # 1. 二维缓存命中
    pair_key = (user_id, project_id) if project_id else None
    if pair_key and pair_key in _pair_cache:
        return _pair_cache[pair_key]

    # 2. 拿 (user, project) 历史取众数
    if project_id:
        records = await _fetch_history(user_id, project_id, auth_token, base_url)
        mode = _mode_work_type(records)
        if mode:
            if TTLCache is not None:
                _pair_cache[pair_key] = mode
            else:
                _pair_cache[pair_key] = mode  # type: ignore[index]
            logger.info(f"workType (user×project) 众数: '{mode}' ({user_id}×{project_id})")
            return mode

    # 3. user_id 单维缓存命中
    if user_id in _user_cache:
        return _user_cache[user_id]

    # 4. 拿 user 全部历史取众数
    records = await _fetch_history(user_id, None, auth_token, base_url)
    mode = _mode_work_type(records)
    if mode:
        if TTLCache is not None:
            _user_cache[user_id] = mode
        else:
            _user_cache[user_id] = mode
        logger.info(f"workType (user) 众数: '{mode}' ({user_id})")
        return mode

    # 5. LLM 兜底
    inferred = await _llm_infer_work_type(description)
    if inferred:
        return inferred

    # 6. 默认值
    logger.info(f"workType 无历史/LLM，兜底为 '{DEFAULT_WORK_TYPE}' ({user_id})")
    return DEFAULT_WORK_TYPE


def clear_work_type_cache() -> None:
    """清空缓存。用于单元测试。"""
    if TTLCache is not None:
        _pair_cache.clear()
        _user_cache.clear()
    else:
        _pair_cache.clear()
        _user_cache.clear()
