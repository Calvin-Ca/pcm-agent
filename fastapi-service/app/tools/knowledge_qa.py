"""
知识问答工具 — 查询工时管理制度、政策、规定等知识性问题

通过将 knowledge_qa 注册为 Function Calling 工具，让 LLM 用语义判断
替代代码层的关键词匹配，提升政策类问题的识别准确率。
"""

import logging
from app.services.tool_registry import ToolRegistry, ToolCategory

logger = logging.getLogger(__name__)

KNOWLEDGE_QA_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "用户的问题原文"
        }
    },
    "required": ["query"],
    "additionalProperties": False
}


async def knowledge_qa_handler(query: str, **kwargs):
    """调用 RAG 检索回答工时制度、政策等知识性问题"""
    from app.services.langchain_rag import langchain_rag_query
    return await langchain_rag_query(query)


def register_knowledge_qa_tool():
    """注册知识问答工具到工具注册中心"""
    try:
        tool_registry = ToolRegistry()
        tool_registry.register_tool(
            name="knowledge_qa",
            description=(
                "查询工时管理制度、政策、规定等知识性问题"
                "（如截止时间、加班规则、补填流程、请假期间是否需要填工时、出差怎么记录等）。"
                "当用户在询问规则/制度，而不是要查询或填报工时数据时，调用此工具。"
            ),
            json_schema=KNOWLEDGE_QA_SCHEMA,
            handler=knowledge_qa_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=30,
            requires_permission=False
        )
        logger.info("知识问答工具注册成功")
    except Exception as e:
        logger.error(f"知识问答工具注册失败: {str(e)}")
        raise


# 自动注册工具（当模块被导入时）
if __name__ != "__main__":
    register_knowledge_qa_tool()
