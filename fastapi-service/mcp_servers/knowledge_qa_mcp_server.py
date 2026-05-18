"""
Knowledge QA MCP Server — 把 knowledge_qa 工具通过 MCP 协议暴露给外部客户端。

用法:
    通过 Claude Desktop / Cursor / Claude Code 等 MCP 客户端的 stdio transport 接入。
    不直接命令行运行（命令行运行会卡住等 stdio 输入）。

设计决策:
    - 薄壳转发：MCP server 只做协议转换，实际执行（RAG 检索）仍在 ai-service 内
    - knowledge_qa 无权限要求（requires_permission=False），但仍走统一内部转发，
      ai-service 维持单点权威源，RAG 管道 / Milvus 依赖全部复用
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
_LOG_FILE = _LOG_DIR / "knowledge_qa_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("knowledge-qa-mcp")

from mcp.server.fastmcp import FastMCP

from mcp_servers._service_account import auth_configured, call_ai_service_tool

mcp = FastMCP("workhour-knowledge-qa")


# ─── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def knowledge_qa(
    query: str,
) -> str:
    """查询工时管理制度、政策、规定等知识性问题。

    通过 RAG 检索企业知识库回答规则/制度类问题（如截止时间、加班规则、补填流程、
    请假期间是否需要填工时、出差怎么记录等）。当用户在询问规则/制度而非查询或
    填报工时数据时使用。

    Args:
        query: 用户的问题原文
    """
    logger.info(f"knowledge_qa called: query={query!r}")

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

    params: dict[str, Any] = {"query": query}

    try:
        result = await call_ai_service_tool("knowledge_qa", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"knowledge_qa failed: {e}", exc_info=True)
        return json.dumps(
            {"error": f"知识问答失败: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
