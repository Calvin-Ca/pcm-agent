"""
内部工具接口 — 仅供 MCP server / 内部脚本调用，不对外暴露。

注意：生产环境必须用 nginx 限制源 IP，禁止公网直接访问。
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException

from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/tools", tags=["internal"])


@router.post("/{tool_name}")
async def call_internal_tool(
    tool_name: str,
    params: Dict[str, Any],
    x_user_id: str = Header(..., alias="X-User-ID"),
    x_entity_type: str = Header(..., alias="X-Entity-Type"),
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
) -> Dict[str, Any]:
    """转发工具调用到 ToolRegistry，带身份头。

    这是内部接口，MCP server 通过 HTTP 调用此端点，由 ai-service 实际执行工具。
    权限 / 参数解析 / SpringBoot 依赖 / 缓存全部在 ai-service 内复用，0 重写。
    """
    logger.info(
        f"[internal] tool={tool_name}, user={x_user_id}, entity_type={x_entity_type}"
    )

    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_name}")

    handler = tool_registry.get_handler(tool_name)
    if not handler:
        raise HTTPException(
            status_code=500, detail=f"tool handler not found: {tool_name}"
        )

    # 把 user context 注进 params（ai-service 现有的 user_context 协议）
    params.setdefault("user_context", {})
    params["user_context"]["user_id"] = x_user_id
    params["user_context"]["entity_type"] = x_entity_type
    params["user_context"]["auth_token"] = x_auth_token

    # 直接注入 auth_token 到顶层（兼容 query_timesheet 等现有工具的参数协议）
    params["auth_token"] = x_auth_token

    try:
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(handler):
            result = await handler(**params)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: handler(**params))

        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[internal] tool={tool_name} execution failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"tool execution failed: {str(e)}"
        )
