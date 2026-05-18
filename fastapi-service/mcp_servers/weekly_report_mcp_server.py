"""
Weekly Report MCP Server — 把 generate_weekly_report 工具通过 MCP 协议暴露给外部客户端。

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
_LOG_FILE = _LOG_DIR / "weekly_report_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("weekly-report-mcp")

from mcp.server.fastmcp import FastMCP

from mcp_servers._service_account import auth_configured, call_ai_service_tool

mcp = FastMCP("workhour-weekly-report")


# ─── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def generate_weekly_report(
    user_id: str | None = None,
    week: str | None = None,
) -> str:
    """根据工时数据自动生成 Markdown 格式周报。

    包含项目分布统计和 LLM 生成的工作总结。

    Args:
        user_id: 用户 ID（可选），不填则查询当前登录用户
        week: 周次参数（可选）。支持：'thisWeek'（本周）、'lastWeek'（上周）、
            'YYYY-WNN'（如 '2024-W01'）、'YYYY-MM-DD'（该日期所在周）。默认本周。
    """
    logger.info(f"generate_weekly_report called: user_id={user_id!r}, week={week!r}")

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
    if user_id is not None:
        params["user_id"] = user_id
    if week is not None:
        params["week"] = week

    try:
        result = await call_ai_service_tool("generate_weekly_report", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"generate_weekly_report failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"周报生成失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
