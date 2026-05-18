"""
Save Workhour MCP Server — 把 save_workhour 写工具经 MCP 暴露。

安全设计（MCP Phase 3）：
    - 默认 dry_run（confirm=False）：首次调用只返回预览，不写库
    - 二段确认：用户明确同意后，以 confirm=True 重发才真写
    - 不接受任意目标 user_id：只为 env 注入身份（token）写，杜绝跨人写
    - 真实写权限闸门是 SpringBoot JWT（MCP_TEST_AUTH_TOKEN 或 Service Account）

Service Account 认证（新增）：
    - 支持 MCP_ENTITY_ID + MCP_API_KEY 动态获取 JWT，替代预配 token
    - token 在首次调用时懒加载并缓存，解决 token 过期问题
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "save_workhour_mcp_server.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("save-workhour-mcp")

from mcp.server.fastmcp import FastMCP

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# Service Account 认证（推荐）
MCP_ENTITY_ID = os.getenv("MCP_ENTITY_ID", "")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

# 传统预配 token（fallback）
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

# 运行时缓存
_cached_auth_token: str | None = None
_cached_user_id: str | None = None

mcp = FastMCP("workhour-save")


def _build_params(
    project_id: str, date: str, duration: float, description: str, confirm: bool
) -> dict[str, Any]:
    """confirm→dry_run 映射。不含任何目标 user_id（只为 token 身份写，G4）。"""
    return {
        "project_id": project_id,
        "date": date,
        "duration": duration,
        "description": description,
        "dry_run": not confirm,
    }


async def _fetch_token_via_service_account() -> tuple[str, str]:
    """通过 Service Account 从 FastAPI 获取 JWT token 和 userId。"""
    import httpx

    logger.info("Fetching JWT token via Service Account (entity_id=%s)", MCP_ENTITY_ID)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{AI_SERVICE_URL}/api/internal/auth/mcp-token",
            json={"entity_id": MCP_ENTITY_ID, "api_key": MCP_API_KEY},
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("token", "")
        user_id = data.get("userId", "")
        if not token:
            raise RuntimeError("Service Account 认证返回空 token")
        logger.info("Service Account 认证成功: userId=%s", user_id)
        return token, user_id


async def _ensure_auth() -> tuple[str, str]:
    """确保有有效的认证信息。返回 (user_id, auth_token)。

    优先级：
    1. 预配的 MCP_TEST_AUTH_TOKEN（已设置则直接使用）
    2. 缓存的 Service Account token
    3. 首次通过 Service Account 获取 token 并缓存
    """
    global _cached_auth_token, _cached_user_id

    # 优先使用预配 token
    if AUTH_TOKEN:
        return USER_ID or MCP_ENTITY_ID, AUTH_TOKEN

    # 使用缓存的 Service Account token
    if _cached_auth_token:
        return _cached_user_id or MCP_ENTITY_ID, _cached_auth_token

    # 通过 Service Account 获取新 token
    if MCP_ENTITY_ID and MCP_API_KEY:
        token, user_id = await _fetch_token_via_service_account()
        _cached_auth_token = token
        _cached_user_id = user_id
        return user_id, token

    return "", ""


async def _call_ai_service_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    import httpx

    user_id, auth_token = await _ensure_auth()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}",
            json=params,
            headers={
                "X-User-ID": user_id,
                "X-Entity-Type": ENTITY_TYPE,
                "X-Auth-Token": auth_token,
            },
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def save_workhour(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """填报单条工时。

    **二段确认协议（必须遵守）**：
    1. 首次调用 confirm=False（默认）→ 返回预览（不写库）。把 preview
       原样呈现给用户。
    2. 仅在用户明确同意后，用完全相同参数 + confirm=True 再次调用，才真正写入。

    本工具只为当前配置身份填报，不接受代填他人。

    Args:
        project_id: 项目名称或 ID（系统会解析）
        date: 工时日期 YYYY-MM-DD
        duration: 工时（小时），0.5 的整数倍，0.5~10
        description: 工作内容（可选）
        confirm: False=预览（默认）；True=确认写入
    """
    logger.info(
        f"save_workhour called: project={project_id!r}, date={date!r}, "
        f"duration={duration!r}, confirm={confirm!r}"
    )

    # 检查认证配置
    if not AUTH_TOKEN and not (MCP_ENTITY_ID and MCP_API_KEY):
        return json.dumps({
            "error": "MCP server not configured",
            "hint": "请配置以下任一组认证信息：\n"
                    "1) Service Account（推荐）: MCP_ENTITY_ID + MCP_API_KEY\n"
                    "2) 预配 Token: MCP_TEST_USER_ID + MCP_TEST_AUTH_TOKEN",
        }, ensure_ascii=False, indent=2)

    params = _build_params(project_id, date, duration, description, confirm)
    try:
        result = await _call_ai_service_tool("save_workhour", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {str(e)}"}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    auth_mode = (
        "ServiceAccount" if (MCP_ENTITY_ID and MCP_API_KEY)
        else "PreconfiguredToken" if AUTH_TOKEN
        else "NOT_CONFIGURED"
    )
    logger.info(
        f"Starting save_workhour MCP server, AI_SERVICE_URL={AI_SERVICE_URL}, "
        f"auth_mode={auth_mode}"
    )
    mcp.run()
