"""
Statistics MCP Server — 把 compute_statistics 工具通过 MCP 协议暴露给外部客户端。

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
_LOG_FILE = _LOG_DIR / "statistics_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("statistics-mcp")

from mcp.server.fastmcp import FastMCP

from mcp_servers._service_account import auth_configured, call_ai_service_tool

mcp = FastMCP("workhour-statistics")


# ─── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def compute_statistics(
    statistics_type: str,
    start_date: str,
    end_date: str,
    user_id: str | None = None,
    project_id: str | None = None,
    department_id: str | None = None,
    work_type: str | None = None,
) -> str:
    """对工时数据进行汇总统计与排名分析。

    返回合计、均值、排名、趋势等聚合数据（不返回明细条目）。适用于：统计总工时、
    部门/人员工时排名、项目工时占比、月度/季度趋势、TopN 排名。

    Args:
        statistics_type: 统计类型，必填，取值之一：
            user_hours(用户工时) / project_hours(项目工时) / department_hours(部门工时)
            / daily_hours(每日工时) / weekly_hours(每周工时) / monthly_hours(每月工时)
        start_date: 开始日期，必填，格式 YYYY-MM-DD
        end_date: 结束日期，必填，格式 YYYY-MM-DD
        user_id: 用户 ID（可选），筛选特定用户
        project_id: 项目 ID（可选），筛选特定项目
        department_id: 部门 ID（可选），筛选特定部门
        work_type: 工时类型筛选（可选），如 '其他工时' 表示加班工时；不填统计全部
    """
    logger.info(
        f"compute_statistics called: statistics_type={statistics_type!r}, "
        f"start_date={start_date!r}, end_date={end_date!r}, user_id={user_id!r}, "
        f"project_id={project_id!r}, department_id={department_id!r}, work_type={work_type!r}"
    )

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
    params: dict[str, Any] = {
        "statistics_type": statistics_type,
        "start_date": start_date,
        "end_date": end_date,
    }
    if user_id is not None:
        params["user_id"] = user_id
    if project_id is not None:
        params["project_id"] = project_id
    if department_id is not None:
        params["department_id"] = department_id
    if work_type is not None:
        params["work_type"] = work_type

    try:
        result = await call_ai_service_tool("compute_statistics", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"compute_statistics failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"统计失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
