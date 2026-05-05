"""
kb_semantic_search Tool — 向量语义检索。

直接复用 langchain_rag 内已就绪的 vector retriever。
适合自然语言问题、近义概念、口语化提问。
"""

import logging
from typing import Any, Dict

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.kb_navigator import semantic_search

logger = logging.getLogger(__name__)


KB_SEMANTIC_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "自然语言查询, 不必是精确关键词",
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


async def kb_semantic_search_handler(**kwargs) -> Dict[str, Any]:
    """向量语义检索"""
    query = kwargs.get("query") or ""
    if not query:
        return {"results": [], "error": "query 为空"}

    category = kwargs.get("category")
    try:
        top_k = int(kwargs.get("top_k") or 5)
    except (TypeError, ValueError):
        top_k = 5

    try:
        results = await semantic_search(query=query, category=category, top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error(f"kb_semantic_search 执行失败: {e}", exc_info=True)
        return {"results": [], "error": str(e)}


def register_kb_semantic_search_tool():
    try:
        tool_registry.register_tool(
            name="kb_semantic_search",
            description=(
                "向量语义检索, 适合自然语言问题、近义概念、口语化提问。"
                "比 kb_keyword_search 慢但能理解语义。"
                "返回 [{file, section, score, snippet}, ...]。"
            ),
            json_schema=KB_SEMANTIC_SEARCH_SCHEMA,
            handler=kb_semantic_search_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=15,
            requires_permission=False,
        )
        logger.info("kb_semantic_search 工具注册成功")
    except Exception as e:
        logger.error(f"kb_semantic_search 工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_kb_semantic_search_tool()
