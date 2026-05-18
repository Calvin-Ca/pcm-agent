"""
内部认证代理接口 — 为 MCP server 提供 Service Account 认证支持。

MCP server 通过此接口获取 JWT Token，无需预配长期有效的 token。
请求被代理到 SpringBoot 的 /api/auth/mcp-token 端点。
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/auth", tags=["internal-auth"])


class McpTokenRequest:
    """MCP Service Account 认证请求体"""
    def __init__(self, entity_id: str, api_key: str):
        self.entity_id = entity_id
        self.api_key = api_key


@router.post("/mcp-token")
async def get_mcp_token(
    request: Dict[str, str] = Body(...),
) -> Dict[str, Any]:
    """代理 MCP Service Account 认证请求到 SpringBoot。

    请求体: { "entity_id": "钉钉userid", "api_key": "预共享密钥" }
    返回: { "token": "JWT", "userId": "...", "userName": "...", ... }
    """
    entity_id = request.get("entity_id")
    api_key = request.get("api_key")

    if not entity_id or not api_key:
        raise HTTPException(status_code=400, detail="entity_id 和 api_key 不能为空")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.SPRINGBOOT_BASE_URL}/api/auth/mcp-token",
                json={"entity_id": entity_id, "api_key": api_key},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "MCP auth failed: status=%d, body=%s",
            e.response.status_code, e.response.text
        )
        try:
            error_body = e.response.json()
            detail = error_body.get("message", "MCP 认证失败")
        except Exception:
            detail = "MCP 认证失败"
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except Exception as e:
        logger.error(f"MCP auth request failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"MCP 认证请求失败: {str(e)}")
