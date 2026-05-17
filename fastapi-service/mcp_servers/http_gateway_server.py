"""
HTTP MCP 网关（方案 2）— 单一常驻 streamable-http MCP 服务，部署在 172。

开发者 .mcp.json 配 type:http + url + headers(X-Gateway-Token / X-Auth-Token /
X-User-ID / X-Entity-Type)，零本机环境。网关只做鉴权+header透传+转发同机
ai-service 内部端点；Phase 3 写白名单/dry_run强制/审计在 ai-service 端生效。

二段确认（save_workhour）：confirm=False(默认)→dry_run=true 预览不写库；
用户明确同意后 confirm=True→dry_run=false 真写。不接受任意 user_id（杜绝跨人写）。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "http_gateway_server.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-gateway")

from mcp.server.fastmcp import FastMCP
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_servers._gateway_core import (
    GatewayAuthMiddleware,
    forward_to_ai_service,
)

mcp = FastMCP("workhour-gateway")


async def _save_workhour_impl(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """内部实现，便于单测；confirm→dry_run 映射，不含任意 user_id。"""
    params = {
        "project_id": project_id,
        "date": date,
        "duration": duration,
        "description": description,
        "dry_run": not confirm,
    }
    result = await forward_to_ai_service("save_workhour", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def save_workhour(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """填报单条工时（二段确认）。

    1. 首次 confirm=False（默认）→ 返回预览，不写库。把 preview 原样给用户。
    2. 用户明确同意后，相同参数 + confirm=True 再调一次才真写。
    只为当前请求身份（X-Auth-Token 对应的人）填报，不接受代填他人。

    Args:
        project_id: 项目名称或 ID（系统解析）
        date: 工时日期 YYYY-MM-DD
        duration: 工时（小时），0.5 的整数倍，0.5~10
        description: 工作内容（可选）
        confirm: False=预览（默认）；True=确认写入
    """
    logger.info(f"save_workhour confirm={confirm!r} project={project_id!r}")
    try:
        return await _save_workhour_impl(
            project_id, date, duration, description, confirm
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {e}"}, ensure_ascii=False)


async def _health(request):
    return PlainTextResponse("ok")


def build_app():
    """组装 ASGI app：streamable_http_app + 健康路由 + 鉴权中间件。"""
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health/health", _health))
    app.add_middleware(GatewayAuthMiddleware)
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting HTTP MCP gateway on 0.0.0.0:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765)
