"""
Knowledge Base MCP Server - 把 4 个 kb_* 工具通过 MCP 协议暴露给外部客户端。

用法:
    通过 Claude Desktop / Cursor / 任何 MCP 客户端的 stdio transport 接入,
    不直接命令行运行(命令行运行会卡住等 stdio 输入)。
    调试用 `mcp dev mcp_servers/kb_mcp_server.py`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 把 fastapi-service/ 加入 sys.path, 这样能 import app.services.kb_navigator
_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

# 加载 .env 文件(业务代码依赖 os.getenv 读取配置)
_BASE_DIR = _SERVICE_ROOT.parent  # ai-service 根目录
try:
    from dotenv import load_dotenv

    env_file = _BASE_DIR / ".env"
    env_local = _BASE_DIR / ".env.local"
    if env_file.exists():
        load_dotenv(env_file)
    if env_local.exists():
        load_dotenv(env_local, override=True)
except ImportError:
    pass

# 确保关键环境变量有默认值(避免业务代码读取不到)
if not os.getenv("CHAT_LLM_API_KEY"):
    os.environ["CHAT_LLM_API_KEY"] = "EMPTY"
if not os.getenv("CHAT_LLM_API_BASE"):
    os.environ["CHAT_LLM_API_BASE"] = "http://172.19.3.136:8099/v1"
if not os.getenv("MILVUS_HOST"):
    os.environ["MILVUS_HOST"] = "172.19.3.136"
if not os.getenv("MILVUS_PORT"):
    os.environ["MILVUS_PORT"] = "19530"

from mcp.server.fastmcp import FastMCP
from app.services.kb_navigator import (
    get_outline,
    keyword_search,
    read_section,
    semantic_search,
)
from app.services.langchain_rag import initialize_langchain_rag

# 日志写文件(不写 stderr)，避免 Windows 管道缓冲区满导致子进程阻塞。
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "kb_mcp_server.log"

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("kb-mcp")

# 全局状态
_rag_initialized = False


def _is_rag_ready() -> bool:
    """RAG 是否已就绪。不阻塞，立刻返回。"""
    return _rag_initialized


async def _ensure_rag_initialized() -> None:
    """确保 RAG 已初始化（在主线程事件循环中运行，约 15-20 秒）。"""
    global _rag_initialized
    if _rag_initialized:
        return
    kb_path = os.getenv("KB_PATH", str(_BASE_DIR / "knowledge-base"))
    logger.info(f"RAG not ready, initializing synchronously, kb_path={kb_path}")
    result = await initialize_langchain_rag(kb_path=kb_path)
    if result.get("success"):
        logger.info(
            f"RAG initialized: {result.get('loaded_files', 0)} files, "
            f"{result.get('total_chunks', 0)} chunks"
        )
        _rag_initialized = True
    else:
        logger.error(f"RAG init failed: {result.get('error')}")


mcp = FastMCP("workhour-knowledge-base")


def _to_text(result: Any) -> str:
    """把 Python 对象转成 JSON 字符串, 作为 MCP TextContent。"""
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def kb_outline(category: str | None = None) -> str:
    """列出知识库的目录大纲 (所有文档的标题 + h2 + metadata)。

    当用户问题模糊、涉及多主题或不确定该查哪篇时, 先调这个工具看全貌,
    再决定下一步用 kb_keyword_search / kb_semantic_search / kb_read_section。
    返回内容很短 (通常 < 500 tokens)。

    Args:
        category: 可选, 限定主题域 (工时管理/假期与加班/薪资福利/请假管理/考勤管理/项目管理流程/通用制度/ALL); 不传时返回全部
    """
    logger.info(f"kb_outline called, category={category!r}")
    result = await get_outline(category=category)
    return _to_text(result)


@mcp.tool()
async def kb_keyword_search(
    query: str,
    category: str | None = None,
    top_k: int = 5,
) -> str:
    """BM25 关键词检索, 适合查找精确术语、制度编号、特定数字。

    比 kb_semantic_search 快但只能匹配字面词。
    返回 [{file, section, score, snippet}, ...]。

    Args:
        query: 检索关键词, 支持多个词组合
        category: 可选, 限定主题域
        top_k: 返回 chunk 数, 默认 5, 范围 1~20
    """
    logger.info(f"kb_keyword_search called, query={query!r}, category={category!r}, top_k={top_k}")
    await _ensure_rag_initialized()
    if not _is_rag_ready():
        return _to_text({
            "results": [],
            "count": 0,
            "status": "error",
            "message": "RAG 初始化失败，无法执行搜索。",
        })
    results = await keyword_search(query=query, category=category, top_k=top_k)
    return _to_text({"results": results, "count": len(results)})


@mcp.tool()
async def kb_semantic_search(
    query: str,
    category: str | None = None,
    top_k: int = 5,
) -> str:
    """向量语义检索, 适合自然语言问题、近义概念、口语化提问。

    比 kb_keyword_search 慢但能理解语义。
    返回 [{file, section, score, snippet}, ...]。

    Args:
        query: 自然语言查询, 不必是精确关键词
        category: 可选, 限定主题域
        top_k: 返回 chunk 数, 默认 5, 范围 1~20
    """
    logger.info(f"kb_semantic_search called, query={query!r}, category={category!r}, top_k={top_k}")
    await _ensure_rag_initialized()
    if not _is_rag_ready():
        return _to_text({
            "results": [],
            "count": 0,
            "status": "error",
            "message": "RAG 初始化失败，无法执行搜索。",
        })
    results = await semantic_search(query=query, category=category, top_k=top_k)
    return _to_text({"results": results, "count": len(results)})


@mcp.tool()
async def kb_read_section(
    file: str,
    section: str,
    include_neighbors: bool = True,
) -> str:
    """精读指定文档的某个 h2 章节 (返回完整原文 + 前后相邻章节)。

    当 kb_keyword_search / kb_semantic_search 返回的片段不够完整时,
    用此工具深读关键章节。

    Args:
        file: 文档相对路径, 从 kb_outline 或 kb_*_search 的返回里取
        section: h2 章节标题, 如 '核心规则' 或 '加班补偿'
        include_neighbors: 是否附带前后相邻 h2 章节, 默认 true
    """
    logger.info(f"kb_read_section called, file={file!r}, section={section!r}, include_neighbors={include_neighbors}")
    result = await read_section(
        file=file,
        section=section,
        include_neighbors=bool(include_neighbors),
    )
    return _to_text(result)


if __name__ == "__main__":
    # 在 mcp.run()（anyio 事件循环）之前，用 asyncio.run() 预初始化 RAG。
    # 实测 8.8s 完成，绕过 anyio ThreadPoolExecutor 与 asyncio.to_thread 的兼容性问题。
    if not _rag_initialized:
        kb_path = os.getenv("KB_PATH", str(_BASE_DIR / "knowledge-base"))
        result = asyncio.run(initialize_langchain_rag(kb_path=kb_path))
        if result.get("success"):
            _rag_initialized = True
            logger.info(
                f"RAG pre-initialized: {result.get('loaded_files')} files, "
                f"{result.get('total_chunks')} chunks"
            )
        else:
            logger.error(f"RAG pre-init failed: {result.get('error')}")
    mcp.run()
