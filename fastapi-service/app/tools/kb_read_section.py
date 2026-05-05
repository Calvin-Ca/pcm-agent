"""
kb_read_section Tool — 精读指定文档的某 h2 章节。

当 search 类工具返回的片段不够完整时, 用这个工具按 (file, section) 取完整原文,
可附带前后相邻章节, 形成上下文完整的精读结果。
"""

import logging
from typing import Any, Dict

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.kb_navigator import read_section

logger = logging.getLogger(__name__)


KB_READ_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {
            "type": "string",
            "description": "文档相对路径, 从 kb_outline 或 kb_*_search 的返回里取",
        },
        "section": {
            "type": "string",
            "description": "h2 章节标题, 如 '核心规则' 或 '加班补偿'",
        },
        "include_neighbors": {
            "type": "boolean",
            "default": True,
            "description": "是否附带前后相邻 h2 章节, 默认 true",
        },
    },
    "required": ["file", "section"],
    "additionalProperties": False,
}


async def kb_read_section_handler(**kwargs) -> Dict[str, Any]:
    """精读 h2 章节 + 邻接 (越权访问会被拒绝)"""
    file = kwargs.get("file") or ""
    section = kwargs.get("section") or ""

    if not file:
        return {"error": "file 参数缺失"}
    if not section:
        return {"error": "section 参数缺失"}

    include_neighbors = kwargs.get("include_neighbors")
    if include_neighbors is None:
        include_neighbors = True

    try:
        return await read_section(
            file=file,
            section=section,
            include_neighbors=bool(include_neighbors),
        )
    except Exception as e:
        logger.error(f"kb_read_section 执行失败: {e}", exc_info=True)
        return {"error": str(e)}


def register_kb_read_section_tool():
    try:
        tool_registry.register_tool(
            name="kb_read_section",
            description=(
                "精读指定文档的某个 h2 章节 (返回完整原文 + 前后相邻章节)。"
                "当 kb_keyword_search / kb_semantic_search 返回的片段不够完整时, "
                "用此工具深读关键章节。"
            ),
            json_schema=KB_READ_SECTION_SCHEMA,
            handler=kb_read_section_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=10,
            requires_permission=False,
        )
        logger.info("kb_read_section 工具注册成功")
    except Exception as e:
        logger.error(f"kb_read_section 工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_kb_read_section_tool()
