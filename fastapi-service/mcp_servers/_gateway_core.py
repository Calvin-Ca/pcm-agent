"""
网关核心：鉴权中间件 + 每请求身份 contextvar + 转发到同机 ai-service。

设计（spec §5.2/5.3/5.5）：
    - GatewayAuthMiddleware：校验 X-Gateway-Token（健康检查路径放行），
      把 X-Auth-Token/X-User-ID/X-Entity-Type 存进 contextvar。
    - forward_to_ai_service：读 contextvar 身份 → httpx POST ai-service
      内部端点（Phase 3 白名单/dry_run/审计在 ai-service 端生效，不绕过）。
铁律：绝不记录 X-Auth-Token / X-Gateway-Token。
"""

from __future__ import annotations

import contextvars
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp-gateway")

GATEWAY_TOKEN = os.getenv("MCP_GATEWAY_TOKEN", "")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://ai-service:8000")

# 健康检查路径前缀放行（不需网关 token，供 compose healthcheck）
_HEALTH_PREFIXES = ("/health",)


@dataclass
class Identity:
    user_id: str = ""
    entity_type: str = "employee"
    auth_token: str = ""


_IDENTITY: contextvars.ContextVar[Identity] = contextvars.ContextVar(
    "mcp_gateway_identity", default=Identity()
)


def get_identity() -> Identity:
    return _IDENTITY.get()


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _HEALTH_PREFIXES):
            return await call_next(request)

        supplied = request.headers.get("X-Gateway-Token", "")
        if not GATEWAY_TOKEN or supplied != GATEWAY_TOKEN:
            # 不回显任何 token；只说明缺哪个头
            return JSONResponse(
                {"error": "missing or invalid X-Gateway-Token"},
                status_code=401,
            )

        ident = Identity(
            user_id=request.headers.get("X-User-ID", ""),
            entity_type=request.headers.get("X-Entity-Type", "employee"),
            auth_token=request.headers.get("X-Auth-Token", ""),
        )
        token = _IDENTITY.set(ident)
        try:
            return await call_next(request)
        finally:
            _IDENTITY.reset(token)


async def forward_to_ai_service(
    tool_name: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """转发到同机 ai-service 内部端点，带 contextvar 里的身份头。"""
    ident = get_identity()
    url = f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}"
    logger.info(f"[gateway] forward tool={tool_name} user={ident.user_id}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            json=params,
            headers={
                "X-User-ID": ident.user_id,
                "X-Entity-Type": ident.entity_type,
                "X-Auth-Token": ident.auth_token,
            },
        )
        resp.raise_for_status()
        return resp.json()
