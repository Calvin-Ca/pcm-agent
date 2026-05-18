"""
Project MCP Server — 把 query_project 工具通过 MCP 协议暴露给外部客户端。

用法:
    通过 Claude Desktop / Cursor / Claude Code 等 MCP 客户端的 stdio transport 接入。
    不直接命令行运行（命令行运行会卡住等 stdio 输入）。

设计决策:
    - 薄壳转发：MCP server 只做协议转换，实际执行仍在 ai-service 内
    - ai-service 维持单点权威源，权限 / 参数解析 / SpringBoot 依赖全部复用
    - 身份通过 env 注入（PoC 阶段），生产环境应迁移到 MCP Resource 协议
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# 把 fastapi-service/ 加入 sys.path，方便 import app 模块
_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

# 日志写文件（不写 stderr），避免 Windows 管道缓冲区满导致子进程阻塞
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "project_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("project-mcp")

from mcp.server.fastmcp import FastMCP

from mcp_servers._service_account import auth_configured, call_ai_service_tool

mcp = FastMCP("workhour-project")


# ─── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def query_project(
    project_id: str | None = None,
) -> str:
    """查询项目信息。

    适用：查项目详情/成员/进度、查哪些项目可以填工时、项目列表、某项目什么时候开始。
    不传 project_id 时返回项目列表（含可填报项目）；传入时返回该项目详情、成员、进度。
    系统会自动把项目名称解析为 ID，可直接传项目名。

    Args:
        project_id: 项目 ID 或名称，不传时返回项目列表
    """
    logger.info(f"query_project called: project_id={project_id!r}")

    if not auth_configured():
        return json.dumps(
            {
                "error": "MCP server not configured",
                "hint": "请配置以下任一组认证信息：\n"
                        "1) Service Account（推荐）: MCP_ENTITY_ID + MCP_API_KEY\n"
                        "2) 预配 Token: MCP_TEST_USER_ID + MCP_TEST_AUTH_TOKEN",
            },
            ensure_ascii=False,
            indent=2,
        )

    # 构建参数（过滤 None 值，让 ai-service 使用默认值）
    params: dict[str, Any] = {}
    if project_id is not None:
        params["project_id"] = project_id

    try:
        result = await call_ai_service_tool("query_project", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"query_project failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"查询失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
