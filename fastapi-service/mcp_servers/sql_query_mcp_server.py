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

from mcp_servers._service_account import auth_configured, call_ai_service_tool

mcp = FastMCP("workhour-sql-query")


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

    params: dict[str, Any] = {"question": question}

    try:
        result = await call_ai_service_tool("sql_query", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"sql_query failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"SQL 查询失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
