"""共享 Service Account 认证 + ai-service 内部工具转发。

7 个 A 类 MCP server 复用本模块，消除各自重复的鉴权/转发样板。
对标 _gateway_core.py 范式。仅依赖 httpx + env，无 app 依赖。

认证优先级（ensure_auth）：
    1. 预配 MCP_TEST_AUTH_TOKEN
    2. 进程级缓存的 Service Account token
    3. Service Account 自取（MCP_ENTITY_ID + MCP_API_KEY）

日志铁律：只记 tool_name + user_id + auth 来源，绝不记 auth_token / api_key。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("mcp-service-account")

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# Service Account 凭据
MCP_ENTITY_ID = os.getenv("MCP_ENTITY_ID", "")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

# 预配回退
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

# 进程级缓存（每个 stdio server 是独立子进程，各自一份，预期行为）
_cached_token: str | None = None
_cached_user_id: str | None = None
_cached_entity_type: str | None = None


def auth_configured() -> bool:
    """预配 token 或 (entity_id + api_key) 任一齐备即视为已配置。"""
    return bool(AUTH_TOKEN) or bool(MCP_ENTITY_ID and MCP_API_KEY)
