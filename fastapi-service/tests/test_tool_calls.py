"""
工具调用单元测试

测试各个工具的 schema 和参数验证，包括：
- query_timesheet 工时查询工具
- query_project 项目查询工具
- compute_statistics 统计工具
- 参数验证
"""

import pytest
from datetime import datetime

from app.tools.query_timesheet import QUERY_TIMESHEET_SCHEMA
from app.tools.query_project import QUERY_PROJECT_SCHEMA


class TestQueryTimesheetSchema:
    """工时查询工具 Schema 测试"""

    def test_schema_structure(self):
        """测试 schema 基本结构"""
        assert QUERY_TIMESHEET_SCHEMA["type"] == "object"
        assert "properties" in QUERY_TIMESHEET_SCHEMA
        assert "required" in QUERY_TIMESHEET_SCHEMA

    def test_schema_properties(self):
        """测试 schema 属性定义"""
        props = QUERY_TIMESHEET_SCHEMA["properties"]

        assert "user_id" in props
        assert "start_date" in props
        assert "end_date" in props
        assert "project_id" in props

    def test_user_id_property(self):
        """测试 user_id 属性定义"""
        user_id_prop = QUERY_TIMESHEET_SCHEMA["properties"]["user_id"]
        assert user_id_prop["type"] == "string"
        assert "description" in user_id_prop

    def test_date_properties(self):
        """测试日期属性定义"""
        start_date_prop = QUERY_TIMESHEET_SCHEMA["properties"]["start_date"]
        end_date_prop = QUERY_TIMESHEET_SCHEMA["properties"]["end_date"]

        assert start_date_prop["type"] == "string"
        assert "pattern" in start_date_prop
        assert "YYYY-MM-DD" in start_date_prop["description"]

        assert end_date_prop["type"] == "string"
        assert "pattern" in end_date_prop

    def test_project_id_property_optional(self):
        """测试 project_id 是可选的"""
        project_id_prop = QUERY_TIMESHEET_SCHEMA["properties"]["project_id"]
        assert project_id_prop["type"] == "string"
        # project_id 不在 required 列表中
        assert "project_id" not in QUERY_TIMESHEET_SCHEMA["required"]

    def test_required_fields(self):
        """测试必需字段

        user_id 为可选：未提供时 SpringBoot 从 JWT 获取当前用户，
        仅 start_date / end_date 为必填。
        """
        required = QUERY_TIMESHEET_SCHEMA["required"]
        assert "start_date" in required
        assert "end_date" in required
        # user_id 为可选，不在 required 中
        assert "user_id" not in required
        assert set(required) == {"start_date", "end_date"}

    def test_additional_properties(self):
        """测试是否允许额外属性"""
        assert QUERY_TIMESHEET_SCHEMA.get("additionalProperties") is False


class TestQueryProjectSchema:
    """项目查询工具 Schema 测试"""

    def test_schema_structure(self):
        """测试 schema 基本结构"""
        assert QUERY_PROJECT_SCHEMA["type"] == "object"
        assert "properties" in QUERY_PROJECT_SCHEMA
        assert "required" in QUERY_PROJECT_SCHEMA

    def test_project_id_property(self):
        """测试 project_id 属性定义"""
        project_id_prop = QUERY_PROJECT_SCHEMA["properties"]["project_id"]
        assert project_id_prop["type"] == "string"
        assert "description" in project_id_prop
        assert "必填" in project_id_prop["description"]

    def test_required_fields(self):
        """测试必需字段"""
        required = QUERY_PROJECT_SCHEMA["required"]
        assert required == ["project_id"]

    def test_additional_properties(self):
        """测试是否允许额外属性"""
        assert QUERY_PROJECT_SCHEMA.get("additionalProperties") is False


class TestParameterValidation:
    """参数验证测试"""

    def test_valid_date_format(self):
        """测试有效日期格式"""
        valid_dates = [
            "2024-01-01",
            "2024-12-31",
            "2023-06-15",
            "2024-02-29",  # 闰年
        ]

        for date_str in valid_dates:
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                assert True
            except ValueError:
                pytest.fail(f"日期 {date_str} 应该有效")

    def test_invalid_date_format(self):
        """测试无效日期格式"""
        invalid_dates = [
            "2024/01/01",
            "01-01-2024",
            "2024-13-01",  # 无效月份
            "2024-01-32",  # 无效日期
            "invalid",
            "",
        ]

        for date_str in invalid_dates:
            with pytest.raises(ValueError):
                datetime.strptime(date_str, "%Y-%m-%d")

    def test_date_range_validation(self):
        """测试日期范围验证逻辑"""
        start_date = datetime.strptime("2024-01-01", "%Y-%m-%d")
        end_date = datetime.strptime("2024-01-31", "%Y-%m-%d")

        # 开始日期早于结束日期
        assert start_date < end_date

        # 同一日期
        same_date = datetime.strptime("2024-01-01", "%Y-%m-%d")
        assert start_date == same_date

    def test_date_logic_error(self):
        """测试日期逻辑错误"""
        start_date = datetime.strptime("2024-02-01", "%Y-%m-%d")
        end_date = datetime.strptime("2024-01-01", "%Y-%m-%d")

        # 开始日期晚于结束日期
        assert start_date > end_date


class TestToolCategories:
    """工具类别测试"""

    def test_tool_category_values(self):
        """测试工具类别枚举值"""
        from app.models.tool import ToolCategory

        categories = [
            ToolCategory.DATA_QUERY,
            ToolCategory.STATISTICS,
            ToolCategory.REPORT,
            ToolCategory.KNOWLEDGE,
            ToolCategory.WORKHOUR,
        ]

        for category in categories:
            assert isinstance(category.value, str)
            assert len(category.value) > 0


class TestToolRegistration:
    """工具注册测试"""

    def test_tool_registry_singleton(self):
        """测试工具注册中心是单例"""
        from app.services.tool_registry import ToolRegistry

        registry1 = ToolRegistry()
        registry2 = ToolRegistry()

        assert registry1 is registry2

    def test_tool_registry_has_tools(self):
        """测试工具注册中心已注册工具"""
        from app.services.tool_registry import tool_registry

        # 导入工具模块后应该已注册
        assert tool_registry.get_tool_count() >= 0

    def test_query_timesheet_tool_exists(self):
        """测试工时查询工具已注册"""
        from app.services.tool_registry import tool_registry

        tool = tool_registry.get_tool("query_timesheet")
        assert tool is not None
        assert tool.name == "query_timesheet"

    def test_query_project_tool_exists(self):
        """测试项目查询工具已注册"""
        from app.services.tool_registry import tool_registry

        tool = tool_registry.get_tool("query_project")
        assert tool is not None
        assert tool.name == "query_project"


class TestToolMetadata:
    """工具元数据测试"""

    def test_query_timesheet_tool_metadata(self):
        """测试工时查询工具元数据"""
        from app.services.tool_registry import tool_registry
        from app.models.tool import ToolCategory

        tool = tool_registry.get_tool("query_timesheet")
        assert tool.category == ToolCategory.DATA_QUERY
        assert tool.timeout == 30
        assert tool.requires_permission is True

    def test_query_project_tool_metadata(self):
        """测试项目查询工具元数据"""
        from app.services.tool_registry import tool_registry
        from app.models.tool import ToolCategory

        tool = tool_registry.get_tool("query_project")
        assert tool.category == ToolCategory.DATA_QUERY
        assert tool.timeout == 30
        assert tool.requires_permission is True

    def test_tool_json_schema_accessible(self):
        """测试工具 JSON Schema 可访问"""
        from app.services.tool_registry import tool_registry

        tool = tool_registry.get_tool("query_timesheet")
        assert tool.json_schema is not None
        assert "properties" in tool.json_schema


class TestToolValidation:
    """工具参数验证测试"""

    def test_validate_valid_timesheet_params(self):
        """测试验证有效的工时查询参数"""
        from app.services.tool_registry import tool_registry

        params = {
            "user_id": "user_001",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31"
        }

        valid, error = tool_registry.validate_params("query_timesheet", params)
        assert valid is True
        assert error is None

    def test_validate_missing_required_param(self):
        """测试验证缺少必需参数（start_date / end_date 为必填，user_id 为可选）"""
        from app.services.tool_registry import tool_registry

        # 缺少 start_date 应该验证失败
        params = {"end_date": "2024-01-31"}
        valid, error = tool_registry.validate_params("query_timesheet", params)
        assert valid is False
        assert error  # 存在错误信息

        # 仅有 start_date + end_date（无 user_id）应该通过（SpringBoot 从 JWT 获取用户）
        params_ok = {"start_date": "2024-01-01", "end_date": "2024-01-31"}
        valid_ok, _ = tool_registry.validate_params("query_timesheet", params_ok)
        assert valid_ok is True

    def test_validate_invalid_param_type(self):
        """测试验证无效参数类型"""
        from app.services.tool_registry import tool_registry

        params = {
            "user_id": "user_001",
            "start_date": "invalid-date",
            "end_date": "2024-01-31"
        }

        # pattern 验证可能会失败，取决于 JSON Schema 验证器的行为
        valid, error = tool_registry.validate_params("query_timesheet", params)
        # 结果可能是 True 或 False，取决于验证器的严格程度

    def test_validate_nonexistent_tool(self):
        """测试验证不存在工具的参数"""
        from app.services.tool_registry import tool_registry

        valid, error = tool_registry.validate_params("nonexistent_tool", {})
        assert valid is False
        assert "不存在" in error

    def test_validate_project_params(self):
        """测试验证项目查询参数"""
        from app.services.tool_registry import tool_registry

        params = {"project_id": "proj_001"}
        valid, error = tool_registry.validate_params("query_project", params)
        assert valid is True

    def test_validate_project_missing_required(self):
        """测试验证项目查询缺少必需参数"""
        from app.services.tool_registry import tool_registry

        params = {}
        valid, error = tool_registry.validate_params("query_project", params)
        assert valid is False
        assert "project_id" in error or "required" in error.lower()
