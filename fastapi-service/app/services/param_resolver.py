"""
Param Resolver - 统一参数解析层

负责将 LLM 提取的模糊参数（项目名称、成员姓名）解析为系统所需的精确 ID。
提供进程级内存缓存，避免同一会话内重复查询。

项目名称三步降级策略：
  1. projectName.contains=<原始输入>          — 精确/包含匹配
  2. projectName.contains=<去掉末尾常见后缀>  — 如"预管理平台"→"预管理"
  3. 查用户近3个月填过的项目，按最长公共子串评分 — 处理口语化名称

用法（在工具 handler 内）：
    from app.services.param_resolver import resolve_project_id, resolve_member_id

    resolved_id, err = await resolve_project_id("AI平台", auth_token, base_url, user_id=uid)
    if err:
        return {"success": False, "error": err}
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ─── 进程级内存缓存 ───────────────────────────────────────────────────────────
# key 格式：
#   "project:{base_url}:{name_or_id}"
#   "member:{base_url}:{name}"
# value：resolved_id（str）或 None（表示"查过但未找到"）
_resolve_cache: Dict[str, Optional[str]] = {}


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _get_base_url() -> str:
    return os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )


def _is_numeric_id(value: str) -> bool:
    return value.strip().isdigit()


# ─── 核心解析函数 ─────────────────────────────────────────────────────────────

async def resolve_project_id(
    project_id_or_name: str,
    auth_token: Optional[str],
    base_url: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    将 LLM 提取的 project_id 字段解析为真实数字 ID。

    规则：
    - 纯数字 → 直接返回，不发 HTTP 请求
    - 名称字符串 → 三步降级搜索（见模块文档）

    Returns:
        (resolved_id, error_message)
        成功：(str, None)
        失败：(None, str)
    """
    if not project_id_or_name:
        return None, "project_id 不能为空"

    value = project_id_or_name.strip()

    if _is_numeric_id(value):
        return value, None

    resolved_base = base_url or _get_base_url()
    cache_key = f"project:{resolved_base}:{value}"

    if cache_key in _resolve_cache:
        cached = _resolve_cache[cache_key]
        if cached is None:
            return None, f"未找到名为「{value}」的项目，请确认项目名称是否正确或提供项目ID"
        return cached, None

    project_id, error = await _search_project_by_name(value, auth_token, resolved_base, user_id)
    _resolve_cache[cache_key] = project_id
    return project_id, error


async def resolve_member_id(
    member_name: str,
    auth_token: Optional[str],
    base_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    通过成员姓名查询 memberId。

    调用接口：GET /thsuaa/api/sys-users?entityName.contains=xx&page=0&size=10
    优先精确匹配 entityName，否则取第一条模糊结果。

    Returns:
        (member_id, error_message)
    """
    if not member_name:
        return None, "member_name 不能为空"

    resolved_base = base_url or _get_base_url()
    cache_key = f"member:{resolved_base}:{member_name}"

    if cache_key in _resolve_cache:
        cached = _resolve_cache[cache_key]
        if cached is None:
            return None, f"未找到姓名包含「{member_name}」的用户"
        return cached, None

    member_id, error = await _lookup_member_by_name(member_name, auth_token, resolved_base)
    _resolve_cache[cache_key] = member_id
    return member_id, error


def clear_resolve_cache() -> None:
    """清空解析缓存。用于单元测试的 setUp/tearDown。"""
    _resolve_cache.clear()


# ─── 内部工具函数 ──────────────────────────────────────────────────────────────

def _longest_common_substring_len(s1: str, s2: str) -> int:
    """计算两字符串的最长公共子串长度（滚动 DP，O(m*n) 时间 O(n) 空间）。"""
    m, n = len(s1), len(s2)
    if not m or not n:
        return 0
    dp = [[0] * (n + 1) for _ in range(2)]
    max_len = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i % 2][j] = dp[(i - 1) % 2][j - 1] + 1
                if dp[i % 2][j] > max_len:
                    max_len = dp[i % 2][j]
            else:
                dp[i % 2][j] = 0
    return max_len


def _find_best_match(user_input: str, candidates: List[Dict]) -> Optional[Dict]:
    """
    在候选项目列表中找与 user_input 最相似的项目。
    评分 = 最长公共子串长度 / user_input 长度。
    至少 2 字连续匹配 + 覆盖率 ≥ 30% 才算命中。
    """
    best_project: Optional[Dict] = None
    best_score = 0.0

    for candidate in candidates:
        pname = candidate.get("name", "")
        if not pname:
            continue
        lcs = _longest_common_substring_len(user_input, pname)
        if lcs < 2:
            continue
        score = lcs / max(1, len(user_input))
        if score > best_score:
            best_score = score
            best_project = candidate

    return best_project if best_score >= 0.3 else None


async def _fetch_user_recent_projects(
    user_id: str,
    auth_token: Optional[str],
    base_url: str,
    months_back: int = 3,
) -> List[Dict]:
    """
    查询用户近 months_back 个月填过的项目列表（去重）。
    返回 [{"id": "123", "name": "项目名"}, ...]
    """
    from datetime import date, timedelta

    end_date = date.today()
    start_date = end_date - timedelta(days=months_back * 30)

    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/workhour/by-date-range",
                params={
                    "memberId": user_id,
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d"),
                },
                headers=headers,
            )
            resp.raise_for_status()
            records = resp.json()
    except Exception as e:
        logger.warning(f"获取用户历史项目失败: user_id={user_id!r}, err={e}")
        return []

    if isinstance(records, dict):
        records = records.get("data", [])
    if not isinstance(records, list):
        return []

    seen: set = set()
    result: List[Dict] = []
    for r in records:
        pid = str(r.get("projectId", ""))
        pname = r.get("projectName", "")
        if pid and pid not in seen:
            seen.add(pid)
            result.append({"id": pid, "name": pname})
    return result


# ─── 内部 HTTP 实现 ───────────────────────────────────────────────────────────

async def _contains_search(
    query_name: str,
    url: str,
    headers: Dict[str, str],
) -> List[Dict]:
    """发起一次 projectName.contains 搜索，返回项目列表（失败返回空列表）。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"projectName.contains": query_name, "page": 0, "size": 10},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("data", data.get("content", []))
    except Exception as e:
        logger.debug(f"项目搜索请求失败: query={query_name!r}, err={e}")
    return []


async def _search_project_by_name(
    name: str,
    auth_token: Optional[str],
    base_url: str,
    user_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    三步降级策略搜索项目名称。
    """
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    url = f"{base_url}/api/project-infos"

    # Step 1: 原始输入 contains 搜索
    projects = await _contains_search(name, url, headers)

    # Step 2: 后缀去除降级（如"预管理平台"→"预管理"）
    if not projects:
        for suffix in ["平台", "系统", "项目", "应用", "模块"]:
            if len(name) > len(suffix) and name.endswith(suffix):
                shorter = name[:-len(suffix)]
                projects = await _contains_search(shorter, url, headers)
                if projects:
                    logger.info(f"项目名称后缀降级: {name!r} → {shorter!r}")
                    break

    # Step 3: 用户历史项目模糊匹配
    if not projects and user_id:
        recent = await _fetch_user_recent_projects(user_id, auth_token, base_url)
        if recent:
            best = _find_best_match(name, recent)
            if best:
                logger.info(f"历史项目模糊匹配: {name!r} → {best['name']!r} (id={best['id']})")
                return best["id"], None
        logger.warning(f"项目名称三步降级均无结果: name={name!r}, user_id={user_id!r}")
        return None, f"未找到名为「{name}」的项目，请确认项目名称是否正确或提供项目ID"

    if not projects:
        logger.warning(f"项目名称搜索无结果: name={name!r}")
        return None, f"未找到名为「{name}」的项目，请确认项目名称是否正确或提供项目ID"

    # 优先精确匹配 projectName，否则取第一条模糊结果
    exact = [p for p in projects if p.get("projectName") == name]
    project = exact[0] if exact else projects[0]

    project_id = str(project.get("id") or "")
    if not project_id:
        logger.error(f"项目搜索结果缺少 id 字段: {project}")
        return None, f"项目「{name}」数据异常，请联系管理员"

    matched_name = project.get("projectName", name)
    logger.info(f"项目名称解析成功: {name!r} → id={project_id}, 匹配项目={matched_name!r}")
    return project_id, None


async def _lookup_member_by_name(
    name: str,
    auth_token: Optional[str],
    base_url: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    通过姓名从 /thsuaa/api/sys-users 查找用户 ID。
    """
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        url = f"{base_url}/thsuaa/api/sys-users"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"entityName.contains": name, "page": 0, "size": 10},
                headers=headers,
            )
            resp.raise_for_status()
            members = resp.json()

        if not members:
            return None, f"未找到姓名包含「{name}」的用户"

        exact = [m for m in members if m.get("entityName") == name]
        member = exact[0] if exact else members[0]

        member_id = str(member.get("id", ""))
        if not member_id:
            return None, f"用户「{name}」数据异常，缺少 id 字段"

        logger.info(
            f"成员名称解析成功: {name!r} → id={member_id}, "
            f"entityName={member.get('entityName', name)!r}"
        )
        return member_id, None

    except httpx.HTTPStatusError as e:
        return None, f"用户查询失败: HTTP {e.response.status_code}"
    except Exception as e:
        return None, f"用户查询异常: {str(e)}"
