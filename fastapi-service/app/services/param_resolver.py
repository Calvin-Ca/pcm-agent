"""
Param Resolver - 统一参数解析层

负责将 LLM 提取的模糊参数（项目名称、成员姓名）解析为系统所需的精确 ID。
提供进程级内存缓存，避免同一会话内重复查询。

用法（在工具 handler 内）：
    from app.services.param_resolver import resolve_project_id, resolve_member_id

    resolved_id, err = await resolve_project_id("AI平台", auth_token, base_url)
    if err:
        return {"success": False, "error": err}
"""

import logging
import os
from typing import Dict, Optional, Tuple

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
    """获取 SpringBoot base_url，与各工具保持相同优先级逻辑。"""
    return os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )


def _is_numeric_id(value: str) -> bool:
    """判断字符串是否为纯数字 ID。"""
    return value.strip().isdigit()


# ─── 核心解析函数 ─────────────────────────────────────────────────────────────

async def resolve_project_id(
    project_id_or_name: str,
    auth_token: Optional[str],
    base_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    将 LLM 提取的 project_id 字段解析为真实数字 ID。

    规则：
    - 纯数字 → 直接返回，不发 HTTP 请求
    - 名称字符串 → GET /api/project/search?name=xx
      优先精确匹配 projectName，否则取第一条结果

    Returns:
        (resolved_id, error_message)
        成功：(str, None)
        失败：(None, str)  —— 调用方应将 error_message 返回给用户
    """
    if not project_id_or_name:
        return None, "project_id 不能为空"

    value = project_id_or_name.strip()

    # 快速路径：已是数字 ID，直接返回
    if _is_numeric_id(value):
        return value, None

    resolved_base = base_url or _get_base_url()
    cache_key = f"project:{resolved_base}:{value}"

    if cache_key in _resolve_cache:
        cached = _resolve_cache[cache_key]
        if cached is None:
            return None, f"未找到名为「{value}」的项目，请确认项目名称是否正确或提供项目ID"
        return cached, None

    project_id, error = await _search_project_by_name(value, auth_token, resolved_base)
    _resolve_cache[cache_key] = project_id  # None 也缓存，避免重复无效请求
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
        成功：(str, None)
        失败：(None, str)
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


# ─── 内部 HTTP 实现 ───────────────────────────────────────────────────────────

async def _search_project_by_name(
    name: str,
    auth_token: Optional[str],
    base_url: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    调用 GET /api/project-infos?projectName.contains=xx 按名称搜索项目。
    使用 JHipster Criteria 过滤语法，projectName 字段对应 ProjectInfoDTO.projectName。
    """
    headers: Dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = auth_token

    try:
        url = f"{base_url}/api/project-infos"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"projectName.contains": name, "page": 0, "size": 10},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        # /api/project-infos 返回列表（分页数据在响应体中直接是数组）
        if isinstance(data, list):
            projects = data
        elif isinstance(data, dict):
            projects = data.get("data", data.get("content", []))
        else:
            projects = []

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

    except httpx.HTTPStatusError as e:
        logger.error(f"项目搜索 API 失败: HTTP {e.response.status_code}, name={name!r}")
        return None, f"项目查询失败: HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        logger.error(f"项目搜索网络错误: {e}, name={name!r}")
        return None, "项目查询网络错误，请稍后重试"
    except Exception as e:
        logger.error(f"项目搜索异常: {e}, name={name!r}", exc_info=True)
        return None, f"项目查询异常: {e}"


async def _lookup_member_by_name(
    name: str,
    auth_token: Optional[str],
    base_url: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    通过姓名从 /thsuaa/api/sys-users 查找用户 ID。
    与 query_timesheet.py 中的同名函数逻辑一致，此处为带缓存的统一版本。
    """
    headers: Dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = auth_token

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

        # 优先精确匹配 entityName，否则取第一条
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
