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
import time
from dataclasses import dataclass
from typing import Any, Dict

import httpx

from mcp_servers._service_account import fetch_service_account_token
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp-gateway")

GATEWAY_TOKEN = os.getenv("MCP_GATEWAY_TOKEN", "")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://ai-service:8000")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")
MCP_GATEWAY_TOKEN_TTL = int(os.getenv("MCP_GATEWAY_TOKEN_TTL", "1500"))

# 健康检查路径前缀放行（不需网关 token，供 compose healthcheck）
_HEALTH_PREFIXES = ("/health",)


@dataclass
class Identity:
    user_id: str = ""
    entity_type: str = "employee"
    auth_token: str = ""
    entity_id: str = ""


_IDENTITY: contextvars.ContextVar[Identity] = contextvars.ContextVar(
    "mcp_gateway_identity", default=Identity()
)


def get_identity() -> Identity:
    return _IDENTITY.get()


_TOKEN_CACHE: dict[str, tuple[Identity, float]] = {}


async def resolve_identity(entity_id: str) -> Identity:
    """按自声明 entity_id 换 JWT，per-entity_id TTL 缓存。"""
    now = time.monotonic()
    hit = _TOKEN_CACHE.get(entity_id)
    if hit and hit[1] > now:
        return hit[0]
    token, user_id, etype = await fetch_service_account_token(
        entity_id, MCP_API_KEY, ai_service_url=AI_SERVICE_URL
    )
    ident = Identity(
        user_id=user_id, entity_type=etype,
        auth_token=token, entity_id=entity_id,
    )
    _TOKEN_CACHE[entity_id] = (ident, now + MCP_GATEWAY_TOKEN_TTL)
    return ident


def _evict(entity_id: str) -> None:
    _TOKEN_CACHE.pop(entity_id, None)


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

        entity_id = request.headers.get("X-Entity-ID", "")
        if not entity_id:
            return JSONResponse(
                {"error": "missing or invalid X-Entity-ID"},
                status_code=401,
            )

        try:
            ident = await resolve_identity(entity_id)
        except Exception as e:  # noqa: BLE001
            # 不回显 token/key/上游 detail；内部日志不记密钥
            logger.error(
                "[gateway] identity resolution failed entity_id=%s err=%s",
                entity_id, type(e).__name__,
            )
            return JSONResponse(
                {"error": "identity resolution failed"},
                status_code=502,
            )

        token = _IDENTITY.set(ident)
        try:
            return await call_next(request)
        finally:
            _IDENTITY.reset(token)


async def forward_to_ai_service(
    tool_name: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """转发到同机 ai-service 内部端点。ai-service 返 401 → 重换 token 重发一次。"""
    ident = get_identity()
    url = f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}"
    logger.info(f"[gateway] forward tool={tool_name} user={ident.user_id}")

    async def _post(identity: Identity) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(
                url,
                json=params,
                headers={
                    "X-User-ID": identity.user_id,
                    "X-Entity-Type": identity.entity_type,
                    "X-Auth-Token": identity.auth_token,
                },
            )

    resp = await _post(ident)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401 and ident.entity_id:
            _evict(ident.entity_id)
            ident = await resolve_identity(ident.entity_id)
            resp = await _post(ident)
            resp.raise_for_status()
        else:
            raise
    return resp.json()
