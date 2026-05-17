"""
Save Workhour MCP Server — 把 save_workhour 写工具经 MCP 暴露。

安全设计（MCP Phase 3）：
    - 默认 dry_run（confirm=False）：首次调用只返回预览，不写库
    - 二段确认：用户明确同意后，以 confirm=True 重发才真写
    - 不接受任意目标 user_id：只为 env 注入身份（token）写，杜绝跨人写
    - 真实写权限闸门是 SpringBoot JWT（MCP_TEST_AUTH_TOKEN 必须合法）
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
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

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


async def _call_ai_service_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}",
            json=params,
            headers={
                "X-User-ID": USER_ID,
                "X-Entity-Type": ENTITY_TYPE,
                "X-Auth-Token": AUTH_TOKEN,
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

    if not USER_ID or not AUTH_TOKEN:
        return json.dumps({
            "error": "MCP server not configured: 缺少 MCP_TEST_USER_ID / MCP_TEST_AUTH_TOKEN env",
            "hint": "请在 .mcp.json 的 env 中配置 MCP_TEST_USER_ID 和 MCP_TEST_AUTH_TOKEN（须合法 SpringBoot JWT）",
        }, ensure_ascii=False, indent=2)

    params = _build_params(project_id, date, duration, description, confirm)
    try:
        result = await _call_ai_service_tool("save_workhour", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {str(e)}"}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    logger.info(
        f"Starting save_workhour MCP server, AI_SERVICE_URL={AI_SERVICE_URL}, "
        f"USER_ID={'set' if USER_ID else 'NOT SET'}"
    )
    mcp.run()
