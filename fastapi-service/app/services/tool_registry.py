"""
Tool Registry - 工具注册中心

提供动态工具注册、查询和管理功能。
"""

import logging
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import jsonschema
from jsonschema import validate, ValidationError

from app.models.tool import (
    ToolDefinition,
    ToolResult,
    ToolExecutionContext,
    ToolCategory
)

logger = logging.getLogger(__name__)


class ToolRegistryError(Exception):
    """工具注册中心异常"""
    pass


class ToolRegistry:
    """工具注册中心
    
    负责管理所有可用工具的注册、查询和验证。
    采用单例模式确保全局唯一。
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """初始化工具注册中心"""
        if self._initialized:
            return
        
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable] = {}
        self._initialized = True
        logger.info("Tool Registry initialized")
    
    def register_tool(
        self,
        name: str,
        description: str,
        json_schema: Dict[str, Any],
        handler: Callable,
        category: ToolCategory = ToolCategory.DATA_QUERY,
        timeout: int = 30,
        requires_permission: bool = True
    ) -> ToolDefinition:
        """注册新工具
        
        Args:
            name: 工具名称，必须唯一
            description: 工具描述
            json_schema: JSON Schema参数定义
            handler: 工具处理函数
            category: 工具类别
            timeout: 超时时间（秒）
            requires_permission: 是否需要权限验证
            
        Returns:
            ToolDefinition: 工具定义对象
            
        Raises:
            ToolRegistryError: 工具名称已存在或参数无效
        """
        # 验证工具名称唯一性
        if name in self._tools:
            raise ToolRegistryError(f"工具 '{name}' 已存在，无法重复注册")
        
        # 验证handler是否可调用
        if not callable(handler):
            raise ToolRegistryError(f"handler必须是可调用对象")
        
        # 验证JSON Schema有效性
        try:
            self._validate_json_schema(json_schema)
        except Exception as e:
            raise ToolRegistryError(f"JSON Schema验证失败: {str(e)}")
        
        # 创建工具定义
        try:
            tool_def = ToolDefinition(
                name=name,
                description=description,
                category=category,
                json_schema=json_schema,
                timeout=timeout,
                requires_permission=requires_permission,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        except Exception as e:
            raise ToolRegistryError(f"创建工具定义失败: {str(e)}")
        
        # 注册工具
        self._tools[name] = tool_def
        self._handlers[name] = handler
        
        logger.info(f"Tool '{name}' registered successfully (category: {category})")
        return tool_def
    
    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """获取工具定义
        
        Args:
            name: 工具名称
            
        Returns:
            ToolDefinition: 工具定义对象，不存在则返回None
        """
        return self._tools.get(name)
    
    def get_handler(self, name: str) -> Optional[Callable]:
        """获取工具处理函数
        
        Args:
            name: 工具名称
            
        Returns:
            Callable: 工具处理函数，不存在则返回None
        """
        return self._handlers.get(name)
    
    def list_tools(
        self,
        category: Optional[ToolCategory] = None
    ) -> List[ToolDefinition]:
        """获取工具列表
        
        Args:
            category: 可选的工具类别过滤
            
        Returns:
            List[ToolDefinition]: 工具定义列表
        """
        tools = list(self._tools.values())
        
        if category:
            tools = [t for t in tools if t.category == category]
        
        return tools
    
    def validate_params(
        self,
        tool_name: str,
        params: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """验证工具参数
        
        Args:
            tool_name: 工具名称
            params: 参数字典
            
        Returns:
            tuple[bool, Optional[str]]: (是否有效, 错误信息)
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return False, f"工具 '{tool_name}' 不存在"
        
        try:
            validate(instance=params, schema=tool.json_schema)
            return True, None
        except ValidationError as e:
            error_msg = f"参数验证失败: {e.message}"
            if e.path:
                error_msg += f" (路径: {'.'.join(str(p) for p in e.path)})"
            return False, error_msg
        except Exception as e:
            return False, f"参数验证异常: {str(e)}"
    
    def unregister_tool(self, name: str) -> bool:
        """注销工具
        
        Args:
            name: 工具名称
            
        Returns:
            bool: 是否成功注销
        """
        if name not in self._tools:
            return False
        
        del self._tools[name]
        del self._handlers[name]
        
        logger.info(f"Tool '{name}' unregistered")
        return True
    
    def tool_exists(self, name: str) -> bool:
        """检查工具是否存在
        
        Args:
            name: 工具名称
            
        Returns:
            bool: 是否存在
        """
        return name in self._tools
    
    def get_tool_count(self) -> int:
        """获取已注册工具数量
        
        Returns:
            int: 工具数量
        """
        return len(self._tools)
    
    def clear(self):
        """清空所有已注册工具（仅用于测试）"""
        self._tools.clear()
        self._handlers.clear()
        logger.warning("All tools cleared from registry")
    
    def _validate_json_schema(self, schema: Dict[str, Any]):
        """验证JSON Schema格式
        
        Args:
            schema: JSON Schema定义
            
        Raises:
            Exception: Schema格式无效
        """
        # 验证schema本身是否符合JSON Schema规范
        try:
            # 使用Draft7验证器检查schema格式
            jsonschema.Draft7Validator.check_schema(schema)
        except jsonschema.SchemaError as e:
            raise Exception(f"JSON Schema格式错误: {str(e)}")
        
        # 验证必需字段
        if 'type' not in schema:
            raise Exception("JSON Schema必须包含type字段")
        
        # 如果是object类型，验证properties
        if schema.get('type') == 'object':
            if 'properties' not in schema:
                raise Exception("object类型的JSON Schema必须包含properties字段")
    
    def get_tools_metadata(self) -> List[Dict[str, Any]]:
        """获取所有工具的元数据（用于API返回）
        
        Returns:
            List[Dict]: 工具元数据列表
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category.value,
                "timeout": tool.timeout,
                "requires_permission": tool.requires_permission,
                "json_schema": tool.json_schema,
                "created_at": tool.created_at.isoformat(),
                "updated_at": tool.updated_at.isoformat()
            }
            for tool in self._tools.values()
        ]


# 全局单例实例
tool_registry = ToolRegistry()
