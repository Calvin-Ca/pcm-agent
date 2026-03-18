"""
Tool Registry单元测试

测试工具注册中心的各项功能，包括：
- 工具注册成功场景
- 参数验证失败场景
- 工具不存在场景
"""

import pytest
from unittest.mock import Mock, AsyncMock
import asyncio

from app.services.tool_registry import ToolRegistry, ToolRegistryError
from app.models.tool import ToolCategory


class TestToolRegistryUnit:
    """Tool Registry单元测试类"""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """每个测试前重置单例"""
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    @pytest.fixture
    def registry(self):
        """创建新的注册表实例"""
        return ToolRegistry()

    @pytest.fixture
    def dummy_handler(self):
        """创建虚拟处理函数"""
        async def handler(**kwargs):
            return {"success": True, "data": kwargs}
        return handler

    @pytest.fixture
    def valid_schema(self):
        """创建有效的JSON Schema"""
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户ID"},
                "date_range": {"type": "string", "description": "日期范围"}
            },
            "required": ["user_id"]
        }

    # ============================================================================
    # 工具注册成功场景测试
    # ============================================================================

    def test_register_tool_success(self, registry, dummy_handler, valid_schema):
        """测试工具注册成功"""
        tool = registry.register_tool(
            name="query_timesheet",
            description="查询工时记录",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=30,
            requires_permission=True
        )

        assert tool is not None
        assert tool.name == "query_timesheet"
        assert tool.description == "查询工时记录"
        assert tool.category == ToolCategory.DATA_QUERY
        assert tool.timeout == 30
        assert tool.requires_permission is True
        assert registry.tool_exists("query_timesheet")

    def test_register_multiple_tools(self, registry, dummy_handler, valid_schema):
        """测试注册多个工具"""
        tools = [
            ("query_timesheet", "查询工时", ToolCategory.DATA_QUERY),
            ("query_project", "查询项目", ToolCategory.DATA_QUERY),
            ("compute_statistics", "计算统计", ToolCategory.STATISTICS),
        ]

        for name, desc, category in tools:
            registry.register_tool(
                name=name,
                description=desc,
                json_schema=valid_schema,
                handler=dummy_handler,
                category=category
            )

        assert registry.get_tool_count() == 3
        for name, _, _ in tools:
            assert registry.tool_exists(name)

    def test_register_tool_with_different_categories(self, registry, dummy_handler):
        """测试使用不同类别注册工具"""
        schema = {"type": "object", "properties": {}}

        categories = [
            ToolCategory.DATA_QUERY,
            ToolCategory.STATISTICS,
            ToolCategory.REPORT,
            ToolCategory.KNOWLEDGE,
            ToolCategory.WORKHOUR,
        ]

        for i, category in enumerate(categories):
            tool = registry.register_tool(
                name=f"tool_{category.value}",
                description=f"Tool for {category.value}",
                json_schema=schema,
                handler=dummy_handler,
                category=category
            )
            assert tool.category == category

    def test_register_tool_default_values(self, registry, dummy_handler, valid_schema):
        """测试工具注册默认值"""
        tool = registry.register_tool(
            name="test_tool",
            description="测试工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        assert tool.category == ToolCategory.DATA_QUERY  # 默认类别
        assert tool.timeout == 30  # 默认超时
        assert tool.requires_permission is True  # 默认需要权限

    # ============================================================================
    # 参数验证失败场景测试
    # ============================================================================

    def test_register_duplicate_tool_fails(self, registry, dummy_handler, valid_schema):
        """测试重复注册工具失败"""
        registry.register_tool(
            name="unique_tool",
            description="唯一工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        with pytest.raises(ToolRegistryError) as exc_info:
            registry.register_tool(
                name="unique_tool",
                description="重复工具",
                json_schema=valid_schema,
                handler=dummy_handler
            )

        assert "unique_tool" in str(exc_info.value)
        assert "已存在" in str(exc_info.value)

    def test_register_tool_invalid_handler(self, registry, valid_schema):
        """测试注册非可调用对象作为handler失败"""
        with pytest.raises(ToolRegistryError) as exc_info:
            registry.register_tool(
                name="bad_tool",
                description="无效工具",
                json_schema=valid_schema,
                handler="not_a_function"
            )

        assert "handler" in str(exc_info.value).lower()
        assert "可调用" in str(exc_info.value)

    def test_register_tool_invalid_schema_type(self, registry, dummy_handler):
        """测试注册无效schema类型失败"""
        with pytest.raises(ToolRegistryError) as exc_info:
            registry.register_tool(
                name="bad_schema_tool",
                description="无效schema工具",
                json_schema={"properties": {}},  # 缺少type字段
                handler=dummy_handler
            )

        assert "type" in str(exc_info.value).lower() or "schema" in str(exc_info.value).lower()

    def test_register_tool_object_without_properties(self, registry, dummy_handler):
        """测试object类型schema缺少properties失败"""
        with pytest.raises(ToolRegistryError) as exc_info:
            registry.register_tool(
                name="bad_object_tool",
                description="无效object工具",
                json_schema={"type": "object"},  # 缺少properties
                handler=dummy_handler
            )

        assert "properties" in str(exc_info.value).lower() or "schema" in str(exc_info.value).lower()

    def test_register_tool_invalid_json_schema(self, registry, dummy_handler):
        """测试注册无效JSON Schema失败"""
        with pytest.raises(ToolRegistryError):
            registry.register_tool(
                name="invalid_schema_tool",
                description="无效schema",
                json_schema={"type": "invalid_type"},  # 无效类型
                handler=dummy_handler
            )

    # ============================================================================
    # 工具查询和获取测试
    # ============================================================================

    def test_get_tool_existing(self, registry, dummy_handler, valid_schema):
        """测试获取存在的工具"""
        registry.register_tool(
            name="existing_tool",
            description="存在的工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        tool = registry.get_tool("existing_tool")
        assert tool is not None
        assert tool.name == "existing_tool"

    def test_get_tool_nonexistent(self, registry):
        """测试获取不存在的工具返回None"""
        tool = registry.get_tool("nonexistent_tool")
        assert tool is None

    def test_get_handler_existing(self, registry, dummy_handler, valid_schema):
        """测试获取存在的handler"""
        registry.register_tool(
            name="tool_with_handler",
            description="带handler的工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        handler = registry.get_handler("tool_with_handler")
        assert handler is not None
        assert handler == dummy_handler

    def test_get_handler_nonexistent(self, registry):
        """测试获取不存在的handler返回None"""
        handler = registry.get_handler("nonexistent_tool")
        assert handler is None

    def test_tool_exists_true(self, registry, dummy_handler, valid_schema):
        """测试工具存在检查返回True"""
        registry.register_tool(
            name="existing_tool",
            description="存在的工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        assert registry.tool_exists("existing_tool") is True

    def test_tool_exists_false(self, registry):
        """测试工具不存在检查返回False"""
        assert registry.tool_exists("nonexistent_tool") is False

    # ============================================================================
    # 工具列表查询测试
    # ============================================================================

    def test_list_tools_all(self, registry, dummy_handler, valid_schema):
        """测试列出所有工具"""
        registry.register_tool(
            name="tool1",
            description="工具1",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )
        registry.register_tool(
            name="tool2",
            description="工具2",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.STATISTICS
        )

        tools = registry.list_tools()
        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert "tool1" in tool_names
        assert "tool2" in tool_names

    def test_list_tools_by_category(self, registry, dummy_handler, valid_schema):
        """测试按类别列出工具"""
        registry.register_tool(
            name="query_tool",
            description="查询工具",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )
        registry.register_tool(
            name="statistics_tool",
            description="统计工具",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.STATISTICS
        )
        registry.register_tool(
            name="another_query_tool",
            description="另一个查询工具",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY
        )

        query_tools = registry.list_tools(category=ToolCategory.DATA_QUERY)
        assert len(query_tools) == 2
        for tool in query_tools:
            assert tool.category == ToolCategory.DATA_QUERY

        statistics_tools = registry.list_tools(category=ToolCategory.STATISTICS)
        assert len(statistics_tools) == 1
        assert statistics_tools[0].name == "statistics_tool"

    def test_list_tools_empty(self, registry):
        """测试空注册表列出工具返回空列表"""
        tools = registry.list_tools()
        assert tools == []

    def test_get_tool_count_empty(self, registry):
        """测试空注册表工具数量为0"""
        assert registry.get_tool_count() == 0

    def test_get_tool_count_multiple(self, registry, dummy_handler, valid_schema):
        """测试多个工具的计数"""
        for i in range(5):
            registry.register_tool(
                name=f"tool_{i}",
                description=f"工具{i}",
                json_schema=valid_schema,
                handler=dummy_handler
            )

        assert registry.get_tool_count() == 5

    # ============================================================================
    # 工具注销测试
    # ============================================================================

    def test_unregister_tool_success(self, registry, dummy_handler, valid_schema):
        """测试成功注销工具"""
        registry.register_tool(
            name="tool_to_remove",
            description="待移除工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        assert registry.tool_exists("tool_to_remove")

        success = registry.unregister_tool("tool_to_remove")
        assert success is True
        assert not registry.tool_exists("tool_to_remove")
        assert registry.get_tool_count() == 0

    def test_unregister_tool_nonexistent(self, registry):
        """测试注销不存在的工具返回False"""
        success = registry.unregister_tool("nonexistent_tool")
        assert success is False

    def test_reregister_after_unregister(self, registry, dummy_handler, valid_schema):
        """测试注销后重新注册"""
        registry.register_tool(
            name="reregistrable_tool",
            description="可重新注册工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        registry.unregister_tool("reregistrable_tool")

        # 重新注册应该成功
        tool = registry.register_tool(
            name="reregistrable_tool",
            description="重新注册的工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        assert tool is not None
        assert tool.description == "重新注册的工具"

    # ============================================================================
    # 参数验证测试
    # ============================================================================

    def test_validate_params_success(self, registry, dummy_handler, valid_schema):
        """测试参数验证成功"""
        registry.register_tool(
            name="validate_tool",
            description="验证参数工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        valid, error = registry.validate_params(
            "validate_tool",
            {"user_id": "123", "date_range": "2024-01-01~2024-01-31"}
        )

        assert valid is True
        assert error is None

    def test_validate_params_missing_required(self, registry, dummy_handler, valid_schema):
        """测试缺少必需参数验证失败"""
        registry.register_tool(
            name="validate_tool",
            description="验证参数工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        valid, error = registry.validate_params(
            "validate_tool",
            {"date_range": "2024-01-01~2024-01-31"}  # 缺少user_id
        )

        assert valid is False
        assert error is not None
        assert "user_id" in error or "required" in error.lower()

    def test_validate_params_wrong_type(self, registry, dummy_handler, valid_schema):
        """测试参数类型错误验证失败"""
        registry.register_tool(
            name="validate_tool",
            description="验证参数工具",
            json_schema=valid_schema,
            handler=dummy_handler
        )

        # 如果schema中定义了类型检查
        schema_with_types = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "name": {"type": "string"}
            },
            "required": ["count", "name"]
        }

        registry.register_tool(
            name="typed_tool",
            description="类型检查工具",
            json_schema=schema_with_types,
            handler=dummy_handler
        )

        valid, error = registry.validate_params(
            "typed_tool",
            {"count": "not_a_number", "name": "test"}
        )

        assert valid is False
        assert error is not None

    def test_validate_params_tool_not_exist(self, registry):
        """测试验证不存在工具的参数"""
        valid, error = registry.validate_params(
            "nonexistent_tool",
            {"param": "value"}
        )

        assert valid is False
        assert "不存在" in error

    # ============================================================================
    # 元数据获取测试
    # ============================================================================

    def test_get_tools_metadata(self, registry, dummy_handler, valid_schema):
        """测试获取工具元数据"""
        registry.register_tool(
            name="metadata_tool",
            description="元数据工具",
            json_schema=valid_schema,
            handler=dummy_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=60,
            requires_permission=False
        )

        metadata = registry.get_tools_metadata()

        assert len(metadata) == 1
        assert metadata[0]["name"] == "metadata_tool"
        assert metadata[0]["description"] == "元数据工具"
        assert metadata[0]["category"] == ToolCategory.DATA_QUERY.value
        assert metadata[0]["timeout"] == 60
        assert metadata[0]["requires_permission"] is False
        assert "json_schema" in metadata[0]
        assert "created_at" in metadata[0]
        assert "updated_at" in metadata[0]

    def test_get_tools_metadata_empty(self, registry):
        """测试空注册表获取元数据"""
        metadata = registry.get_tools_metadata()
        assert metadata == []

    # ============================================================================
    # 清理测试
    # ============================================================================

    def test_clear_registry(self, registry, dummy_handler, valid_schema):
        """测试清空注册表"""
        for i in range(3):
            registry.register_tool(
                name=f"tool_{i}",
                description=f"工具{i}",
                json_schema=valid_schema,
                handler=dummy_handler
            )

        assert registry.get_tool_count() == 3

        registry.clear()

        assert registry.get_tool_count() == 0
        assert registry.list_tools() == []


# ============================================================================
# 异步工具执行测试（可选）
# ============================================================================

# 注意：异步测试需要 pytest-asyncio 插件正确配置
# 这些测试展示了如何测试异步 handler，但实际执行依赖于测试环境配置
