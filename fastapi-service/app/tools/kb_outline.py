"""
kb_outline Tool — 返回知识库目录大纲。

让 LLM 在面对模糊 / 跨多主题问题时, 先看大纲再决定下一步检索。
内容轻量 (< 500 tokens), 适合作为 agent loop 的第一跳。
"""

import logging
from typing import Any, Dict

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.kb_navigator import get_outline

logger = logging.getLogger(__name__)


KB_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "description": "可选, 限定主题域 (工时管理/假期与加班/薪资福利/请假管理/考勤管理/项目管理流程/通用制度/ALL); 不传时返回全部",
        },
    },
    "required": [],
    "additionalProperties": False,
}


async def kb_outline_handler(**kwargs) -> Dict[str, Any]:
    """返回 knowledge-base 目录下所有文档的 frontmatter + h2 大纲"""
    category = kwargs.get("category")
    try:
        return await get_outline(category=category)
    except Exception as e:
        logger.error(f"kb_outline 执行失败: {e}", exc_info=True)
        return {"documents": [], "error": str(e)}


def register_kb_outline_tool():
    try:
        tool_registry.register_tool(
            name="kb_outline",
            description=(
                "列出知识库的目录大纲 (所有文档的标题 + h2 + metadata)。"
                "当用户问题模糊、涉及多主题或不确定该查哪篇时, 先调这个工具看全貌, "
                "再决定下一步用 kb_keyword_search / kb_semantic_search / kb_read_section。"
                "返回内容很短 (通常 < 500 tokens)。"
            ),
            json_schema=KB_OUTLINE_SCHEMA,
            handler=kb_outline_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=10,
            requires_permission=False,
        )
        logger.info("kb_outline 工具注册成功")
    except Exception as e:
        logger.error(f"kb_outline 工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_kb_outline_tool()
