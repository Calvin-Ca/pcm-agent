"""
Suggest Workhour Tool - 智能填报建议

用户准备填报工时但未提供完整字段时，基于历史记录返回项目和工时推荐。
仅在用户表达填报意图但缺少 project_id 或 hours 时调用。
"""

import logging
import os
from typing import Any, Dict

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.project_resolver import resolve_project_suggestion
from app.services.hours_resolver import resolve_hours_suggestion

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

SUGGEST_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "fill_date": {
            "type": "string",
            "description": "ISO 8601 日期，默认今天（YYYY-MM-DD）",
        },
    },
    "required": [],
    "additionalProperties": False,
}


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def suggest_workhour_handler(**kwargs) -> Dict[str, Any]:
    """
    智能填报建议工具处理函数。

    返回基于用户最近 30 天填报历史的项目和工时推荐。
    """
    context = kwargs.pop("context", {})
    user_id = context.get("user_id")
    auth_token = kwargs.get("auth_token") or context.get("auth_token")

    if not user_id:
        return {
            "success": False,
            "error": "缺少用户标识，无法提供个性化推荐",
        }

    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )

    try:
        # 1. 获取项目推荐
        projects = await resolve_project_suggestion(
            user_id, auth_token, base_url, top_k=3
        )

        # 2. 获取工时推荐（以第一个推荐项目为上下文）
        default_project_id = projects[0]["project_id"] if projects else None
        default_hours = await resolve_hours_suggestion(
            user_id, default_project_id, auth_token, base_url
        )

        return {
            "success": True,
            "suggested_projects": projects,
            "suggested_hours": default_hours,
            "tip": "以上是基于您最近 30 天填报历史的推荐，可直接使用或自行修改",
        }

    except Exception as e:
        logger.error(f"智能填报建议异常: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"获取建议失败: {e}",
        }


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_suggest_workhour_tool():
    """注册智能填报建议工具"""
    try:
        tool_registry.register_tool(
            name="suggest_workhour",
            description="用户准备填报工时但未提供完整字段时，返回基于历史的项目和工时推荐。仅在用户表达填报意图但缺少 project_id 或 hours 时调用。",
            json_schema=SUGGEST_WORKHOUR_SCHEMA,
            handler=suggest_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=15,
            requires_permission=False,  # 只读历史，不写数据
        )
        logger.info("智能填报建议工具注册成功")
    except Exception as e:
        logger.error(f"智能填报建议工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_suggest_workhour_tool()
