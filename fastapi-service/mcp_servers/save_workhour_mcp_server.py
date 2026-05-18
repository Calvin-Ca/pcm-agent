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

from mcp_servers._service_account import auth_configured, call_ai_service_tool

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

    if not auth_configured():
        return json.dumps({
            "error": "MCP server not configured",
            "hint": "请配置以下任一组认证信息：\n"
                    "1) Service Account（推荐）: MCP_ENTITY_ID + MCP_API_KEY\n"
                    "2) 预配 Token: MCP_TEST_USER_ID + MCP_TEST_AUTH_TOKEN",
        }, ensure_ascii=False, indent=2)

    params = _build_params(project_id, date, duration, description, confirm)
    try:
        result = await call_ai_service_tool("save_workhour", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {str(e)}"}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    from mcp_servers._service_account import auth_configured as _ac
    logger.info("Starting save_workhour MCP server, auth_configured=%s", _ac())
    mcp.run()
