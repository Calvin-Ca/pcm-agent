"""
Tool Registry工具注册唯一性属性测试

专门测试需求23.3：工具名称必须唯一
"""

import pytest
from hypothesis import given, strategies as st, settings
from typing import Dict, Any

from app.services.tool_registry import ToolRegistry, ToolRegistryError
from app.models.tool import ToolCategory


# 生成有效的工具名称
tool_names = st.text(
    alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_'),
    min_size=1,
    max_size=20
).filter(lambda x: x and x.strip() and x.replace('_', '').isalnum())


def create_simple_schema():
    """创建简单的JSON Schema"""
    return {
        'type': 'object',
        'properties': {
            'param1': {'type': 'string', 'description': 'Test parameter'}
        },
        'required': []
    }


def dummy_handler(**kwargs):
    """测试用的简单handler"""
    return {"success": True, "data": kwargs}


class TestToolRegistrationUniqueness:
    """测试工具注册唯一性属性（需求23.3）"""
    
    def setup_method(self):
        """每个测试前重置单例"""
        # 强制重新创建单例实例以确保干净状态
        if hasattr(ToolRegistry, '_instance'):
            ToolRegistry._instance = None
        self.registry = ToolRegistry()
    
    def teardown_method(self):
        """每个测试后清理"""
        if hasattr(self, 'registry'):
            self.registry.clear()
        if hasattr(ToolRegistry, '_instance'):
            ToolRegistry._instance = None
    
    @given(name=tool_names)
    @settings(max_examples=20)
    def test_duplicate_registration_fails(self, name):
        """
        属性8: 工具注册唯一性

        验证：同一个工具名称不能被注册两次
        需求：23.3 - 工具名称必须唯一
        """
        self.registry.clear()
        schema = create_simple_schema()
        
        # 第一次注册应该成功
        tool1 = self.registry.register_tool(
            name=name,
            description="First registration",
            json_schema=schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )
        
        assert tool1 is not None
        assert self.registry.tool_exists(name)
        assert self.registry.get_tool_count() == 1
        
        # 第二次注册相同名称应该失败
        with pytest.raises(ToolRegistryError) as exc_info:
            self.registry.register_tool(
                name=name,  # 相同的名称
                description="Second registration",
                json_schema=schema,
                handler=dummy_handler,
                category=ToolCategory.DATA_QUERY
            )
        
        # 验证错误信息包含工具名称和重复注册提示
        error_msg = str(exc_info.value)
        assert name in error_msg
        assert ("已存在" in error_msg or "重复" in error_msg.lower())
        
        # 验证工具数量没有增加
        assert self.registry.get_tool_count() == 1
    
    def test_different_names_can_coexist(self):
        """
        属性8扩展: 不同名称的工具可以共存
        
        验证：只要工具名称不同，就可以同时注册多个工具
        """
        schema = create_simple_schema()
        
        # 注册第一个工具
        tool1 = self.registry.register_tool(
            name="tool_one",
            description="First tool",
            json_schema=schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )
        
        # 注册第二个工具（不同名称）
        tool2 = self.registry.register_tool(
            name="tool_two",
            description="Second tool",
            json_schema=schema,
            handler=dummy_handler,
            category=ToolCategory.STATISTICS
        )
        
        # 验证两个工具都存在
        assert tool1 is not None
        assert tool2 is not None
        assert self.registry.tool_exists("tool_one")
        assert self.registry.tool_exists("tool_two")
        assert self.registry.get_tool_count() == 2
        
        # 验证可以获取到不同的工具
        retrieved_tool1 = self.registry.get_tool("tool_one")
        retrieved_tool2 = self.registry.get_tool("tool_two")
        assert retrieved_tool1.name == "tool_one"
        assert retrieved_tool2.name == "tool_two"
        assert retrieved_tool1.category == ToolCategory.DATA_QUERY
        assert retrieved_tool2.category == ToolCategory.STATISTICS
    
    def test_unregister_allows_reregistration(self):
        """
        属性8扩展: 注销后可以重新注册
        
        验证：工具注销后，相同名称可以再次注册
        """
        schema = create_simple_schema()
        tool_name = "reusable_tool"
        
        # 第一次注册
        tool1 = self.registry.register_tool(
            name=tool_name,
            description="First registration",
            json_schema=schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )
        
        assert self.registry.tool_exists(tool_name)
        assert self.registry.get_tool_count() == 1
        
        # 注销工具
        success = self.registry.unregister_tool(tool_name)
        assert success is True
        assert not self.registry.tool_exists(tool_name)
        assert self.registry.get_tool_count() == 0
        
        # 重新注册相同名称应该成功
        tool2 = self.registry.register_tool(
            name=tool_name,
            description="Second registration",
            json_schema=schema,
            handler=dummy_handler,
            category=ToolCategory.STATISTICS  # 不同的类别
        )
        
        assert tool2 is not None
        assert self.registry.tool_exists(tool_name)
        assert self.registry.get_tool_count() == 1
        
        # 验证新注册的工具有不同的属性
        retrieved_tool = self.registry.get_tool(tool_name)
        assert retrieved_tool.description == "Second registration"
        assert retrieved_tool.category == ToolCategory.STATISTICS
    
    def test_case_sensitive_names(self):
        """
        验证工具名称区分大小写
        
        "Tool"和"tool"应该是不同的工具
        """
        schema = create_simple_schema()
        
        # 注册小写名称
        tool1 = self.registry.register_tool(
            name="mytool",
            description="Lowercase tool",
            json_schema=schema,
            handler=dummy_handler
        )
        
        # 注册大写名称（应该成功，因为名称不同）
        tool2 = self.registry.register_tool(
            name="MyTool",
            description="Capitalized tool",
            json_schema=schema,
            handler=dummy_handler
        )
        
        # 注册全大写名称（应该成功，因为名称不同）
        tool3 = self.registry.register_tool(
            name="MYTOOL",
            description="Uppercase tool",
            json_schema=schema,
            handler=dummy_handler
        )
        
        assert tool1 is not None
        assert tool2 is not None
        assert tool3 is not None
        assert self.registry.get_tool_count() == 3
        
        # 验证每个工具都可以独立访问
        assert self.registry.tool_exists("mytool")
        assert self.registry.tool_exists("MyTool")
        assert self.registry.tool_exists("MYTOOL")
        
        # 验证工具属性
        assert self.registry.get_tool("mytool").description == "Lowercase tool"
        assert self.registry.get_tool("MyTool").description == "Capitalized tool"
        assert self.registry.get_tool("MYTOOL").description == "Uppercase tool"
    
    @given(name=tool_names)
    @settings(max_examples=10)
    def test_registration_idempotency_check(self, name):
        """
        验证注册操作的幂等性检查

        多次尝试注册相同工具应该始终失败（除了第一次）
        """
        self.registry.clear()
        schema = create_simple_schema()
        
        # 第一次注册
        self.registry.register_tool(
            name=name,
            description="Original",
            json_schema=schema,
            handler=dummy_handler
        )
        
        # 多次尝试重复注册都应该失败
        for i in range(3):
            with pytest.raises(ToolRegistryError):
                self.registry.register_tool(
                    name=name,
                    description=f"Attempt {i+1}",
                    json_schema=schema,
                    handler=dummy_handler
                )
        
        # 工具数量应该始终为1
        assert self.registry.get_tool_count() == 1
        
        # 原始工具的属性应该保持不变
        tool = self.registry.get_tool(name)
        assert tool.description == "Original"