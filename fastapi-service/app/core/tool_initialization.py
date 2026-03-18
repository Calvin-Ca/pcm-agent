"""
工具初始化模块

在应用启动时注册所有工具
"""

import logging
from app.services.tool_registry import tool_registry
from app.models.tool import ToolCategory
from app.tools.query_timesheet import handle_query_timesheet, QUERY_TIMESHEET_TOOL
from app.tools.query_project import handle_query_project, QUERY_PROJECT_TOOL
from app.tools.compute_statistics import handle_compute_statistics, COMPUTE_STATISTICS_TOOL
from app.tools.search_knowledge import handle_search_knowledge, SEARCH_KNOWLEDGE_TOOL

logger = logging.getLogger(__name__)


async def initialize_tools():
    """初始化所有工具"""
    try:
        # 注册工时查询工具
        tool_registry.register_tool(
            name=QUERY_TIMESHEET_TOOL["name"],
            description=QUERY_TIMESHEET_TOOL["description"],
            json_schema=QUERY_TIMESHEET_TOOL["json_schema"],
            handler=handle_query_timesheet,
            category=ToolCategory.DATA_QUERY,
            timeout=QUERY_TIMESHEET_TOOL["timeout"],
            requires_permission=QUERY_TIMESHEET_TOOL["requires_permission"]
        )
        
        # 注册项目查询工具
        tool_registry.register_tool(
            name=QUERY_PROJECT_TOOL["name"],
            description=QUERY_PROJECT_TOOL["description"],
            json_schema=QUERY_PROJECT_TOOL["json_schema"],
            handler=handle_query_project,
            category=ToolCategory.DATA_QUERY,
            timeout=QUERY_PROJECT_TOOL["timeout"],
            requires_permission=QUERY_PROJECT_TOOL["requires_permission"]
        )
        
        # 注册统计计算工具
        tool_registry.register_tool(
            name=COMPUTE_STATISTICS_TOOL["name"],
            description=COMPUTE_STATISTICS_TOOL["description"],
            json_schema=COMPUTE_STATISTICS_TOOL["json_schema"],
            handler=handle_compute_statistics,
            category=ToolCategory.DATA_ANALYSIS,
            timeout=COMPUTE_STATISTICS_TOOL["timeout"],
            requires_permission=COMPUTE_STATISTICS_TOOL["requires_permission"]
        )
        
        # 注册知识库搜索工具
        tool_registry.register_tool(
            name=SEARCH_KNOWLEDGE_TOOL["name"],
            description=SEARCH_KNOWLEDGE_TOOL["description"],
            json_schema=SEARCH_KNOWLEDGE_TOOL["json_schema"],
            handler=handle_search_knowledge,
            category=ToolCategory.KNOWLEDGE,
            timeout=SEARCH_KNOWLEDGE_TOOL["timeout"],
            requires_permission=SEARCH_KNOWLEDGE_TOOL["requires_permission"]
        )
        
        logger.info(f"成功注册 {tool_registry.get_tool_count()} 个工具")
        
        # 初始化知识库搜索服务
        from app.tools.search_knowledge import initialize_knowledge_search
        await initialize_knowledge_search()
        
        # 加载初始知识库
        from app.services.knowledge_loader import initialize_knowledge_base
        result = await initialize_knowledge_base()
        
        if result.get("success", False):
            logger.info(f"知识库初始化成功: {result.get('loaded_files', 0)} 个文件, {result.get('total_chunks', 0)} 个文档块")
        else:
            logger.warning(f"知识库初始化失败: {result.get('error', '未知错误')}")
        
    except Exception as e:
        logger.error(f"工具初始化失败: {e}")
        raise