"""
Tool Registry属性测试

使用Hypothesis进行基于属性的测试，验证工具注册中心的核心属性。
重点测试属性8: 工具注册唯一性（需求23.3）
"""

import pytest
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize
from typing import Dict, Any

from app.services.tool_registry import ToolRegistry, ToolRegistryError
from app.models.tool import ToolCategory


# ============================================================================
# Hypothesis策略定义
# ============================================================================

# 生成有效的工具名称
tool_names = st.text(
    alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_'),
    min_size=1,
    max_size=50
).filter(lambda x: x and x.strip() and x.replace('_', '').isalnum())

# 生成工具描述
tool_descriptions = st.text(min_size=1, max_size=200)

# 生成工具类别
tool_categories = st.sampled_from(list(ToolCategory))

# 生成简单的JSON Schema
@st.composite
def json_schemas(draw):
    """生成有效的JSON Schema"""
    schema_type = draw(st.sampled_from(['object', 'string', 'number', 'boolean']))
    
    if schema_type == 'object':
        # 生成object类型的schema
        num_properties = draw(st.integers(min_value=0, max_value=5))
        properties = {}
        
        for i in range(num_properties):
            prop_name = draw(st.text(
                alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
                min_size=1,
                max_size=20
            ))
            prop_type = draw(st.sampled_from(['string', 'number', 'integer', 'boolean']))
            properties[prop_name] = {
                'type': prop_type,
                'description': draw(st.text(min_size=1, max_size=50))
            }
        
        # 确保required字段唯一
        required_fields = []
        if properties:
            required_fields = list(set(draw(st.lists(st.sampled_from(list(properties.keys())), max_size=len(properties)))))
        
        return {
            'type': 'object',
            'properties': properties,
            'required': required_fields
        }
    else:
        # 生成简单类型的schema
        return {
            'type': schema_type,
            'description': draw(st.text(min_size=1, max_size=50))
        }


# 生成完整的工具定义
@st.composite
def tool_definitions(draw):
    """生成随机工具定义"""
    return {
        'name': draw(tool_names),
        'description': draw(tool_descriptions),
        'json_schema': draw(json_schemas()),
        'category': draw(tool_categories),
        'timeout': draw(st.integers(min_value=1, max_value=300)),
        'requires_permission': draw(st.booleans())
    }


# ============================================================================
# 属性8: 工具注册唯一性测试
# ============================================================================

class TestToolRegistrationUniqueness:
    """测试工具注册唯一性属性（需求23.3）"""
    
    def setup_method(self):
        """每个测试前清空注册表"""
        # 强制重新创建单例实例
        ToolRegistry._instance = None
        self.registry = ToolRegistry()
    
    def teardown_method(self):
        """每个测试后清空注册表"""
        self.registry.clear()
        ToolRegistry._instance = None
    
    @given(tool_def=tool_definitions())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_duplicate_registration_fails(self, tool_def):
        """
        属性8: 工具注册唯一性
        
        验证：同一个工具名称不能被注册两次
        需求：23.3 - 工具名称必须唯一
        """
        # 创建一个简单的handler
        def dummy_handler(**kwargs):
            return {"success": True}
        
        # 第一次注册应该成功
        tool1 = self.registry.register_tool(
            name=tool_def['name'],
            description=tool_def['description'],
            json_schema=tool_def['json_schema'],
            handler=dummy_handler,
            category=tool_def['category'],
            timeout=tool_def['timeout'],
            requires_permission=tool_def['requires_permission']
        )
        
        assert tool1 is not None
        assert self.registry.tool_exists(tool_def['name'])
        
        # 第二次注册相同名称应该失败
        with pytest.raises(ToolRegistryError) as exc_info:
            self.registry.register_tool(
                name=tool_def['name'],  # 相同的名称
                description="Different description",
                json_schema=tool_def['json_schema'],
                handler=dummy_handler,
                category=tool_def['category']
            )
        
        # 验证错误信息包含工具名称
        assert tool_def['name'] in str(exc_info.value)
        assert "已存在" in str(exc_info.value) or "重复" in str(exc_info.value).lower()
    
    @given(
        tool_def1=tool_definitions(),
        tool_def2=tool_definitions()
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_different_names_can_coexist(self, tool_def1, tool_def2):
        """
        属性8扩展: 不同名称的工具可以共存
        
        验证：只要工具名称不同，就可以同时注册多个工具
        """
        # 确保两个工具名称不同
        assume(tool_def1['name'] != tool_def2['name'])
        
        def dummy_handler(**kwargs):
            return {"success": True}
        
        # 注册第一个工具
        tool1 = self.registry.register_tool(
            name=tool_def1['name'],
            description=tool_def1['description'],
            json_schema=tool_def1['json_schema'],
            handler=dummy_handler,
            category=tool_def1['category']
        )
        
        # 注册第二个工具（不同名称）
        tool2 = self.registry.register_tool(
            name=tool_def2['name'],
            description=tool_def2['description'],
            json_schema=tool_def2['json_schema'],
            handler=dummy_handler,
            category=tool_def2['category']
        )
        
        # 验证两个工具都存在
        assert tool1 is not None
        assert tool2 is not None
        assert self.registry.tool_exists(tool_def1['name'])
        assert self.registry.tool_exists(tool_def2['name'])
        assert self.registry.get_tool_count() == 2
    
    @given(tool_def=tool_definitions())
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_unregister_allows_reregistration(self, tool_def):
        """
        属性8扩展: 注销后可以重新注册
        
        验证：工具注销后，相同名称可以再次注册
        """
        def dummy_handler(**kwargs):
            return {"success": True}
        
        # 第一次注册
        tool1 = self.registry.register_tool(
            name=tool_def['name'],
            description=tool_def['description'],
            json_schema=tool_def['json_schema'],
            handler=dummy_handler,
            category=tool_def['category']
        )
        
        assert self.registry.tool_exists(tool_def['name'])
        
        # 注销工具
        success = self.registry.unregister_tool(tool_def['name'])
        assert success is True
        assert not self.registry.tool_exists(tool_def['name'])
        
        # 重新注册相同名称应该成功
        tool2 = self.registry.register_tool(
            name=tool_def['name'],
            description="New description",
            json_schema=tool_def['json_schema'],
            handler=dummy_handler,
            category=tool_def['category']
        )
        
        assert tool2 is not None
        assert self.registry.tool_exists(tool_def['name'])


# ============================================================================
# 状态机测试：模拟复杂的注册/注销序列
# ============================================================================

class ToolRegistryStateMachine(RuleBasedStateMachine):
    """
    使用状态机测试工具注册中心的复杂交互
    
    验证在各种操作序列下，工具注册唯一性始终保持
    """
    
    def __init__(self):
        super().__init__()
        self.registry = ToolRegistry()
        self.registry.clear()
        self.registered_tools = set()
    
    @initialize()
    def init_registry(self):
        """初始化注册表"""
        self.registry.clear()
        self.registered_tools.clear()
    
    @rule(
        name=tool_names,
        description=tool_descriptions,
        schema=json_schemas(),
        category=tool_categories
    )
    def register_tool(self, name, description, schema, category):
        """尝试注册工具"""
        def dummy_handler(**kwargs):
            return {"success": True}
        
        if name in self.registered_tools:
            # 如果已注册，应该失败
            with pytest.raises(ToolRegistryError):
                self.registry.register_tool(
                    name=name,
                    description=description,
                    json_schema=schema,
                    handler=dummy_handler,
                    category=category
                )
        else:
            # 如果未注册，应该成功
            tool = self.registry.register_tool(
                name=name,
                description=description,
                json_schema=schema,
                handler=dummy_handler,
                category=category
            )
            assert tool is not None
            self.registered_tools.add(name)
    
    @rule(name=tool_names)
    def unregister_tool(self, name):
        """尝试注销工具"""
        if name in self.registered_tools:
            success = self.registry.unregister_tool(name)
            assert success is True
            self.registered_tools.remove(name)
        else:
            success = self.registry.unregister_tool(name)
            assert success is False
    
    @invariant()
    def check_consistency(self):
        """不变式：注册表状态与跟踪状态一致"""
        # 验证工具数量一致
        assert self.registry.get_tool_count() == len(self.registered_tools)
        
        # 验证每个已注册工具都存在
        for name in self.registered_tools:
            assert self.registry.tool_exists(name)
            assert self.registry.get_tool(name) is not None
    
    @invariant()
    def check_uniqueness(self):
        """不变式：所有工具名称唯一"""
        tools = self.registry.list_tools()
        tool_names_list = [t.name for t in tools]
        
        # 验证没有重复的工具名称
        assert len(tool_names_list) == len(set(tool_names_list))


# 运行状态机测试
TestToolRegistryState = ToolRegistryStateMachine.TestCase


# ============================================================================
# 额外的边界条件测试
# ============================================================================

class TestToolRegistryEdgeCases:
    """测试边界条件和特殊情况"""
    
    def setup_method(self):
        """每个测试前清空注册表"""
        # 强制重新创建单例实例
        ToolRegistry._instance = None
        self.registry = ToolRegistry()
    
    def teardown_method(self):
        """每个测试后清空注册表"""
        self.registry.clear()
        ToolRegistry._instance = None
    
    @given(name=tool_names)
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_case_sensitive_names(self, name):
        """
        验证工具名称是否区分大小写
        
        如果名称区分大小写，"Tool"和"tool"应该是不同的工具
        """
        # 跳过全小写或全大写的名称
        assume(name.lower() != name and name.upper() != name)
        
        def dummy_handler(**kwargs):
            return {"success": True}
        
        schema = {'type': 'object', 'properties': {}}
        
        # 注册原始名称
        tool1 = self.registry.register_tool(
            name=name,
            description="Original",
            json_schema=schema,
            handler=dummy_handler
        )
        
        # 尝试注册不同大小写的名称
        different_case_name = name.swapcase()
        
        # 这应该成功，因为名称不同
        tool2 = self.registry.register_tool(
            name=different_case_name,
            description="Different case",
            json_schema=schema,
            handler=dummy_handler
        )
        
        assert tool1 is not None
        assert tool2 is not None
        assert self.registry.get_tool_count() == 2
