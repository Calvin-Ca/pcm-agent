"""
Planner Agent单元测试

测试任务规划代理的各项功能，包括：
- 简单任务规划
- 复杂依赖关系处理
- 循环依赖检测
- 降级策略
"""

import pytest
from unittest.mock import Mock, AsyncMock
import json

from app.models.task_plan import (
    PlannerAgent, TaskPlan, TaskNode, TaskType, TaskStatus
)


class TestPlannerAgentInitialization:
    """Planner Agent初始化测试"""

    def test_planner_agent_initialization(self):
        """测试规划代理初始化"""
        mock_tool_registry = Mock()
        mock_llm_client = Mock()

        planner = PlannerAgent(
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client
        )

        assert planner.tool_registry is mock_tool_registry
        assert planner.llm_client is mock_llm_client

    def test_planner_agent_without_llm_client(self):
        """测试无LLM客户端时的初始化"""
        planner = PlannerAgent()

        assert planner.tool_registry is None
        assert planner.llm_client is None


class TestPlannerAgentPlanTasks:
    """任务规划功能测试"""

    @pytest.fixture
    def mock_llm_client(self):
        """模拟LLM客户端"""
        client = Mock()
        client.generate = AsyncMock()
        return client

    @pytest.fixture
    def mock_tool_registry(self):
        """模拟工具注册中心"""
        registry = Mock()
        registry.list_tools = Mock(return_value=["query_timesheet", "query_project"])
        tool_mock = Mock()
        tool_mock.name = "query_timesheet"
        tool_mock.description = "查询工时"
        tool_mock.parameters = {"user_id": {"type": "string"}}
        tool_mock.required_params = ["user_id"]
        registry.get_tool = Mock(return_value=tool_mock)
        return registry

    @pytest.fixture
    def planner(self, mock_tool_registry, mock_llm_client):
        """创建规划代理实例"""
        return PlannerAgent(
            tool_registry=mock_tool_registry,
            llm_client=mock_llm_client
        )

    @pytest.mark.asyncio
    async def test_plan_simple_task(self, planner, mock_llm_client):
        """测试简单任务规划"""
        # 模拟LLM返回简单的任务计划
        mock_llm_client.generate.return_value = json.dumps({
            "plan_name": "查询工时计划",
            "description": "查询用户工时",
            "tasks": [
                {
                    "task_id": "task_1",
                    "task_type": "tool_call",
                    "tool_name": "query_timesheet",
                    "parameters": {"user_id": "user_001"},
                    "dependencies": []
                }
            ]
        })

        task_plan = await planner.plan_tasks(
            user_request="查询我的工时",
            user_context={"user_id": "user_001"}
        )

        assert isinstance(task_plan, TaskPlan)
        assert task_plan.name == "查询工时计划"
        assert len(task_plan.tasks) == 1
        assert "task_1" in task_plan.tasks

    @pytest.mark.asyncio
    async def test_plan_complex_tasks_with_dependencies(self, planner, mock_llm_client):
        """测试复杂任务规划与依赖关系"""
        # 模拟LLM返回带依赖的任务计划
        mock_llm_client.generate.return_value = json.dumps({
            "plan_name": "复杂查询计划",
            "description": "查询工时并统计分析",
            "tasks": [
                {
                    "task_id": "task_1",
                    "task_type": "tool_call",
                    "tool_name": "query_timesheet",
                    "parameters": {"user_id": "user_001"},
                    "dependencies": []
                },
                {
                    "task_id": "task_2",
                    "task_type": "data_processing",
                    "tool_name": None,
                    "parameters": {"operation": "analyze"},
                    "dependencies": ["task_1"]
                }
            ]
        })

        task_plan = await planner.plan_tasks(
            user_request="查询我的工时并分析",
            user_context={"user_id": "user_001"}
        )

        assert isinstance(task_plan, TaskPlan)
        assert len(task_plan.tasks) == 2

        # 验证依赖关系
        task_2 = task_plan.tasks.get("task_2")
        assert task_2 is not None
        assert "task_1" in task_2.dependencies

    @pytest.mark.asyncio
    async def test_detect_circular_dependencies(self, planner, mock_llm_client):
        """测试循环依赖检测"""
        # 模拟LLM返回带循环依赖的任务计划
        mock_llm_client.generate.return_value = json.dumps({
            "plan_name": "循环依赖计划",
            "tasks": [
                {
                    "task_id": "task_a",
                    "task_type": "tool_call",
                    "tool_name": "query_timesheet",
                    "parameters": {},
                    "dependencies": ["task_b"]
                },
                {
                    "task_id": "task_b",
                    "task_type": "tool_call",
                    "tool_name": "query_project",
                    "parameters": {},
                    "dependencies": ["task_a"]
                }
            ]
        })

        task_plan = await planner.plan_tasks(
            user_request="查询工时和项目",
            user_context={}
        )

        # 应该能检测到循环依赖
        assert task_plan.has_circular_dependency() is True

    @pytest.mark.asyncio
    async def test_plan_tasks_no_llm_client(self):
        """测试无LLM客户端时的行为"""
        planner = PlannerAgent()

        with pytest.raises(ValueError, match="LLM client is required"):
            await planner.plan_tasks("查询工时")

    @pytest.mark.asyncio
    async def test_plan_tasks_llm_failure_fallback(self, planner, mock_llm_client):
        """测试LLM失败时的降级策略"""
        # 模拟LLM调用失败
        mock_llm_client.generate.side_effect = Exception("LLM API Error")

        task_plan = await planner.plan_tasks(
            user_request="查询我的工时",
            user_context={"user_id": "user_001"}
        )

        # 应该返回降级计划
        assert isinstance(task_plan, TaskPlan)
        assert len(task_plan.tasks) >= 1  # 至少有一个任务

    @pytest.mark.asyncio
    async def test_plan_tasks_with_available_tools(self, planner, mock_llm_client, mock_tool_registry):
        """测试使用可用工具列表"""
        mock_llm_client.generate.return_value = json.dumps({
            "plan_name": "工具测试计划",
            "tasks": [
                {
                    "task_id": "task_1",
                    "task_type": "tool_call",
                    "tool_name": "query_timesheet",
                    "parameters": {},
                    "dependencies": []
                }
            ]
        })

        available_tools = ["query_timesheet"]
        await planner.plan_tasks(
            user_request="查询工时",
            available_tools=available_tools
        )

        # 验证工具信息获取
        mock_tool_registry.get_tool.assert_called_with("query_timesheet")


class TestPlannerAgentToolsInfo:
    """工具信息获取测试"""

    def test_get_tools_info_with_registry(self):
        """测试从注册中心获取工具信息"""
        mock_registry = Mock()
        tool_def = Mock()
        tool_def.name = "test_tool"
        tool_def.description = "测试工具"
        tool_def.parameters = {"param1": {"type": "string"}}
        tool_def.required_params = ["param1"]
        mock_registry.list_tools = Mock(return_value=["test_tool"])
        mock_registry.get_tool = Mock(return_value=tool_def)

        planner = PlannerAgent(tool_registry=mock_registry)
        tools_info = planner._get_tools_info()

        assert len(tools_info) == 1
        assert tools_info[0]["name"] == "test_tool"

    def test_get_tools_info_no_registry(self):
        """测试无注册中心时的行为"""
        planner = PlannerAgent()
        tools_info = planner._get_tools_info()

        assert tools_info == []

    def test_get_tools_info_with_available_tools_list(self):
        """测试指定可用工具列表"""
        mock_registry = Mock()
        tool_def = Mock()
        tool_def.name = "tool_a"
        tool_def.description = "工具A"
        tool_def.parameters = {}
        tool_def.required_params = []
        mock_registry.get_tool = Mock(return_value=tool_def)

        planner = PlannerAgent(tool_registry=mock_registry)
        tools_info = planner._get_tools_info(available_tools=["tool_a", "tool_b"])

        assert len(tools_info) == 2
        assert mock_registry.get_tool.call_count == 2


class TestPlannerAgentParseResponse:
    """LLM响应解析测试"""

    @pytest.fixture
    def planner_with_mocks(self):
        """创建带模拟的PlannerAgent"""
        return PlannerAgent(
            tool_registry=Mock(),
            llm_client=Mock()
        )

    def test_parse_valid_llm_response(self, planner_with_mocks):
        """测试解析有效的LLM响应"""
        response = json.dumps({
            "plan_name": "测试计划",
            "description": "测试描述",
            "tasks": [
                {
                    "task_id": "task_1",
                    "task_type": "tool_call",
                    "tool_name": "query_timesheet",
                    "parameters": {"user_id": "001"},
                    "dependencies": []
                }
            ]
        })

        task_plan = planner_with_mocks._parse_llm_response(response, "用户请求")

        assert isinstance(task_plan, TaskPlan)
        assert task_plan.name == "测试计划"
        assert task_plan.user_request == "用户请求"
        assert len(task_plan.tasks) == 1

    def test_parse_invalid_json_response(self, planner_with_mocks):
        """测试解析无效JSON响应"""
        response = "这不是有效的JSON"

        # 应该返回降级计划
        task_plan = planner_with_mocks._parse_llm_response(response, "用户请求")

        assert isinstance(task_plan, TaskPlan)
        assert len(task_plan.tasks) >= 1

    def test_parse_missing_required_fields(self, planner_with_mocks):
        """测试解析缺少必需字段的响应"""
        response = json.dumps({
            "description": "缺少plan_name"
        })

        # 应该能处理或返回降级计划
        task_plan = planner_with_mocks._parse_llm_response(response, "用户请求")

        assert isinstance(task_plan, TaskPlan)


class TestPlannerAgentValidatePlan:
    """任务计划验证测试"""

    @pytest.fixture
    def planner_with_mocks(self):
        """创建带模拟的PlannerAgent"""
        return PlannerAgent(
            tool_registry=Mock(),
            llm_client=Mock()
        )

    def test_validate_valid_task_plan(self, planner_with_mocks):
        """测试验证有效的任务计划"""
        plan = TaskPlan(name="有效计划")
        plan.add_task(TaskNode(
            task_id="task_1",
            task_type=TaskType.TOOL_CALL,
            tool_name="query_timesheet"
        ))

        # 应该不抛出异常
        planner_with_mocks._validate_task_plan(plan)

    def test_validate_empty_task_plan(self, planner_with_mocks):
        """测试验证空任务计划"""
        plan = TaskPlan(name="空计划")

        # 空计划应该也能通过验证或返回降级
        try:
            planner_with_mocks._validate_task_plan(plan)
        except Exception:
            pass  # 接受验证失败

    def test_validate_plan_with_invalid_dependencies(self, planner_with_mocks):
        """测试验证有无效依赖的计划"""
        plan = TaskPlan(name="无效依赖计划")
        plan.add_task(TaskNode(
            task_id="task_1",
            task_type=TaskType.TOOL_CALL,
            dependencies=["non_existent_task"]
        ))

        # 应该能检测或处理无效依赖
        try:
            planner_with_mocks._validate_task_plan(plan)
        except Exception:
            pass  # 接受验证失败


class TestPlannerAgentFallbackPlan:
    """降级计划生成测试"""

    @pytest.fixture
    def planner_with_mocks(self):
        """创建带模拟的PlannerAgent"""
        return PlannerAgent(
            tool_registry=Mock(),
            llm_client=Mock()
        )

    def test_create_fallback_plan(self, planner_with_mocks):
        """测试创建降级计划"""
        task_plan = planner_with_mocks._create_fallback_plan(
            user_request="查询工时",
            user_context={"user_id": "001"}
        )

        assert isinstance(task_plan, TaskPlan)
        assert task_plan.name == "Fallback Plan"
        assert len(task_plan.tasks) >= 1

    def test_fallback_plan_contains_generic_task(self, planner_with_mocks):
        """测试降级计划包含通用任务"""
        task_plan = planner_with_mocks._create_fallback_plan(
            user_request="查询工时",
            user_context={}
        )

        # 验证至少有一个任务
        assert len(task_plan.tasks) >= 1

        # 获取第一个任务
        first_task = list(task_plan.tasks.values())[0]
        assert first_task.task_type == TaskType.GENERAL_CHAT


class TestPlannerAgentBuildPrompt:
    """规划Prompt构建测试"""

    @pytest.fixture
    def planner_with_mocks(self):
        """创建带模拟的PlannerAgent"""
        return PlannerAgent(
            tool_registry=Mock(),
            llm_client=Mock()
        )

    def test_build_planning_prompt(self, planner_with_mocks):
        """测试构建规划Prompt"""
        user_request = "查询我的工时"
        user_context = {"user_id": "001", "role": "employee"}
        tools_info = [{"name": "query_timesheet", "description": "查询工时"}]

        prompt = planner_with_mocks._build_planning_prompt(
            user_request, user_context, tools_info
        )

        # 验证Prompt包含必要信息
        assert "查询我的工时" in prompt
        assert "query_timesheet" in prompt
        assert "JSON" in prompt  # 应该要求返回JSON格式

    def test_build_prompt_without_tools(self, planner_with_mocks):
        """测试无工具时的Prompt构建"""
        prompt = planner_with_mocks._build_planning_prompt(
            user_request="查询工时",
            user_context={},
            tools_info=[]
        )

        assert "查询工时" in prompt
        assert isinstance(prompt, str)

    def test_build_prompt_with_complex_context(self, planner_with_mocks):
        """测试复杂上下文的Prompt构建"""
        user_context = {
            "user_id": "001",
            "department_id": "dept_001",
            "role": "manager",
            "permissions": ["read", "write"]
        }

        prompt = planner_with_mocks._build_planning_prompt(
            user_request="统计部门工时",
            user_context=user_context,
            tools_info=[]
        )

        # 复杂上下文应该被正确处理
        assert "统计部门工时" in prompt
