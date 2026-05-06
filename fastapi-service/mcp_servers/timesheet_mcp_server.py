"""
Timesheet MCP Server — 把 query_timesheet 工具通过 MCP 协议暴露给外部客户端。

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
import os
import sys
from pathlib import Path
from typing import Any

# 把 fastapi-service/ 加入 sys.path，方便 import app 模块
_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

# 日志写文件（不写 stderr），避免 Windows 管道缓冲区满导致子进程阻塞
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "timesheet_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("timesheet-mcp")

from mcp.server.fastmcp import FastMCP

# ─── 配置 ────────────────────────────────────────────────────────────────────
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# PoC 阶段：从 env 注入测试用户身份
# 生产环境应通过 MCP Resource 协议或客户端显式传参
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

mcp = FastMCP("workhour-timesheet")


# ─── 内部 HTTP 调用 ──────────────────────────────────────────────────────────

async def _call_ai_service_tool(
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """调用 ai-service 内部工具接口。"""
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


# ─── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def query_timesheet(
    member_id: str | None = None,
    project_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """查询用户的工时填报记录。

    支持按人员、时间范围、项目筛选。不传参数时查询当前用户最近 30 天的工时。
    返回工时明细条目和简单汇总（总工时、按项目分组等）。

    Args:
        member_id: 成员 ID，不传时查询当前用户
        project_id: 项目 ID，不传时查全部项目
        start_date: 开始日期 YYYY-MM-DD，不传时自动推断为最近 30 天
        end_date: 结束日期 YYYY-MM-DD，不传时自动推断为今天
    """
    logger.info(
        f"query_timesheet called: member_id={member_id!r}, "
        f"project_id={project_id!r}, start_date={start_date!r}, end_date={end_date!r}"
    )

    if not USER_ID or not AUTH_TOKEN:
        return json.dumps(
            {
                "error": "MCP server not configured: 缺少 MCP_TEST_USER_ID / MCP_TEST_AUTH_TOKEN env",
                "hint": "请在 .mcp.json 的 env 中配置 MCP_TEST_USER_ID 和 MCP_TEST_AUTH_TOKEN",
            },
            ensure_ascii=False,
            indent=2,
        )

    # 构建参数（过滤 None 值，让 ai-service 使用默认值）
    params: dict[str, Any] = {}
    if member_id is not None:
        params["user_id"] = member_id
    if project_id is not None:
        params["project_id"] = project_id
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date

    try:
        result = await _call_ai_service_tool("query_timesheet", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"query_timesheet failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"查询失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info(
        f"Starting timesheet MCP server, AI_SERVICE_URL={AI_SERVICE_URL}, "
        f"USER_ID={'set' if USER_ID else 'NOT SET'}"
    )
    mcp.run()
