"""
SQL Query MCP Server — 把 sql_query 工具通过 MCP 协议暴露给外部客户端。

用法:
    通过 Claude Desktop / Cursor / Claude Code 等 MCP 客户端的 stdio transport 接入。
    不直接命令行运行（命令行运行会卡住等 stdio 输入）。

设计决策:
    - 薄壳转发：MCP server 只做协议转换，实际执行仍在 ai-service 内
    - sql_query 直连 MySQL（不走 SpringBoot），但 SQL 生成/安全校验/执行仍在
      ai-service 内完成，转发方案同样适用，单点权威源不变
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
_LOG_FILE = _LOG_DIR / "sql_query_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sql-query-mcp")

from mcp.server.fastmcp import FastMCP

# ─── 配置 ────────────────────────────────────────────────────────────────────
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# PoC 阶段：从 env 注入测试用户身份
# 生产环境应通过 MCP Resource 协议或客户端显式传参
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

mcp = FastMCP("workhour-sql-query")


# ─── 内部 HTTP 调用 ──────────────────────────────────────────────────────────

async def _call_ai_service_tool(
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """调用 ai-service 内部工具接口。"""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
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
async def sql_query(
    question: str,
) -> str:
    """执行自定义 SQL 查询（自然语言转 SQL，直连 MySQL）。

    适用场景：多表 JOIN 关联、复杂条件筛选、窗口函数、自定义时间区间聚合等
    复杂分析。SQL 由 ai-service 内 LLM 生成并经安全校验后执行，只读。

    Args:
        question: 用户的自然语言问题，如「各部门本月工时对比」「工时最多的前10人」
    """
    logger.info(f"sql_query called: question={question!r}")

    if not USER_ID or not AUTH_TOKEN:
        return json.dumps(
            {
                "error": "MCP server not configured: 缺少 MCP_TEST_USER_ID / MCP_TEST_AUTH_TOKEN env",
                "hint": "请在 .mcp.json 的 env 中配置 MCP_TEST_USER_ID 和 MCP_TEST_AUTH_TOKEN",
            },
            ensure_ascii=False,
            indent=2,
        )

    params: dict[str, Any] = {"question": question}

    try:
        result = await _call_ai_service_tool("sql_query", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"sql_query failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"SQL 查询失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info(
        f"Starting sql_query MCP server, AI_SERVICE_URL={AI_SERVICE_URL}, "
        f"USER_ID={'set' if USER_ID else 'NOT SET'}"
    )
    mcp.run()
