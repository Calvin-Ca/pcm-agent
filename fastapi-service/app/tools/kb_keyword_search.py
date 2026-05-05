"""
kb_keyword_search Tool — BM25 关键词检索。

直接复用 langchain_rag 内已就绪的 BM25Retriever (jieba 分词)。
适合查找精确术语、制度编号、特定数字。
"""

import logging
from typing import Any, Dict

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.kb_navigator import keyword_search

logger = logging.getLogger(__name__)


KB_KEYWORD_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "检索关键词, 支持多个词组合",
        },
        "category": {
            "type": "string",
            "description": "可选, 限定主题域",
        },
        "top_k": {
            "type": "integer",
            "default": 5,
            "minimum": 1,
            "maximum": 20,
            "description": "返回 chunk 数, 默认 5",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def kb_keyword_search_handler(**kwargs) -> Dict[str, Any]:
    """BM25 关键词检索"""
    query = kwargs.get("query") or ""
    if not query:
        return {"results": [], "error": "query 为空"}

    category = kwargs.get("category")
    try:
        top_k = int(kwargs.get("top_k") or 5)
    except (TypeError, ValueError):
        top_k = 5

    try:
        results = await keyword_search(query=query, category=category, top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error(f"kb_keyword_search 执行失败: {e}", exc_info=True)
        return {"results": [], "error": str(e)}


def register_kb_keyword_search_tool():
    try:
        tool_registry.register_tool(
            name="kb_keyword_search",
            description=(
                "BM25 关键词检索, 适合查找精确术语、制度编号、特定数字。"
                "比 kb_semantic_search 快但只能匹配字面词。"
                "返回 [{file, section, score, snippet}, ...]。"
            ),
            json_schema=KB_KEYWORD_SEARCH_SCHEMA,
            handler=kb_keyword_search_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=15,
            requires_permission=False,
        )
        logger.info("kb_keyword_search 工具注册成功")
    except Exception as e:
        logger.error(f"kb_keyword_search 工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_kb_keyword_search_tool()
