"""
Task Executor单元测试

测试任务执行器的各项功能，包括：
- 任务执行器初始化
- 任务结果存储
- 依赖注入
- 执行摘要构建
"""

import pytest
from unittest.mock import Mock

from app.services.task_executor import TaskExecutor
from app.services.tool_registry import ToolRegistry
from app.services.permission_validator import PermissionValidator
from app.models.task_plan import TaskPlan, TaskNode, TaskStatus, TaskType


class TestTaskExecutorInitialization:
    """任务执行器初始化测试"""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """重置工具注册中心"""
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    def test_task_executor_initialization(self):
        """测试任务执行器初始化"""
        registry = ToolRegistry()
        executor = TaskExecutor(tool_registry=registry)

        assert executor.tool_registry is registry
        assert executor.permission_validator is None
        assert executor.llm_client is None
        assert executor.execution_results == {}

    def test_task_executor_with_optional_params(self):
        """测试带可选参数的任务执行器初始化"""
        registry = ToolRegistry()
        validator = PermissionValidator()
        llm_client = Mock()

        executor = TaskExecutor(
            tool_registry=registry,
            permission_validator=validator,
            llm_client=llm_client
        )

        assert executor.permission_validator is validator
        assert executor.llm_client is llm_client


class TestTaskPlanStructure:
    """任务计划结构测试"""

    def test_task_plan_creation(self):
        """测试任务计划创建"""
        plan = TaskPlan(name="测试计划", description="测试描述")

        assert plan.name == "测试计划"
        assert plan.description == "测试描述"
        assert plan.status == TaskStatus.PENDING

    def test_task_node_creation(self):
        """测试任务节点创建"""
        task = TaskNode(
            task_id="task_001",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            parameters={"key": "value"}
        )

        assert task.task_id == "task_001"
        assert task.task_type == TaskType.TOOL_CALL
        assert task.tool_name == "test_tool"
        assert task.parameters == {"key": "value"}
        assert task.status == TaskStatus.PENDING

    def test_task_with_dependencies(self):
        """测试带依赖的任务"""
        task = TaskNode(
            task_id="task_002",
            task_type=TaskType.TOOL_CALL,
            tool_name="tool2",
            dependencies=["task_001"]
        )

        assert "task_001" in task.dependencies

    def test_task_plan_status_transitions(self):
        """测试任务计划状态转换"""
        plan = TaskPlan(name="测试计划")

        assert plan.status == TaskStatus.PENDING

        plan.start_execution()
        assert plan.status == TaskStatus.RUNNING

        plan.complete_execution()
        assert plan.status == TaskStatus.COMPLETED

    def test_task_plan_fail_execution(self):
        """测试任务计划失败"""
        plan = TaskPlan(name="测试计划")

        plan.start_execution()
        plan.fail_execution("执行失败")

        assert plan.status == TaskStatus.FAILED


class TestTaskResults:
    """任务结果测试"""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """重置工具注册中心"""
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    @pytest.fixture
    def task_executor(self):
        """创建任务执行器"""
        registry = ToolRegistry()
        return TaskExecutor(tool_registry=registry)

    def test_get_task_result_nonexistent(self, task_executor):
        """测试获取不存在任务的结果"""
        result = task_executor.get_task_result("nonexistent_task")
        assert result is None

    def test_task_result_storage(self, task_executor):
        """测试任务结果存储"""
        task_executor.execution_results["task_1"] = {"result": "test_output"}

        result = task_executor.get_task_result("task_1")
        assert result == {"result": "test_output"}

    def test_clear_results(self, task_executor):
        """测试清除结果"""
        task_executor.execution_results["task_1"] = {"result": "test"}
        task_executor.execution_results["task_2"] = {"result": "test2"}

        task_executor.clear_results()

        assert len(task_executor.execution_results) == 0


class TestDependencyInjection:
    """依赖注入测试"""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """重置工具注册中心"""
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    @pytest.fixture
    def task_executor(self):
        """创建任务执行器"""
        registry = ToolRegistry()
        return TaskExecutor(tool_registry=registry)

    def test_inject_dependency_results(self, task_executor):
        """测试依赖结果注入"""
        # 预设置前置任务结果（与 TaskExecutor 存储格式一致）
        task_executor.execution_results["task_1"] = {
            "tool_name": "tool1",
            "parameters": {},
            "result": {"output": "task1_output"},
            "execution_time": 0.1,
            "success": True
        }

        # 测试参数注入 - 格式: {task_id.result.field}
        parameters = {
            "input": "{task_1.result.output}",
            "normal_param": "normal_value"
        }

        processed = task_executor._inject_dependency_results(parameters)

        assert processed["input"] == "task1_output"
        assert processed["normal_param"] == "normal_value"

    def test_inject_nonexistent_dependency(self, task_executor):
        """测试注入不存在的依赖"""
        parameters = {
            "input": "{nonexistent_task.result.output}"
        }

        processed = task_executor._inject_dependency_results(parameters)

        # 不存在的依赖保持原样
        assert processed["input"] == "{nonexistent_task.result.output}"

    def test_inject_no_dependencies(self, task_executor):
        """测试无依赖的参数"""
        parameters = {
            "param1": "value1",
            "param2": 123
        }

        processed = task_executor._inject_dependency_results(parameters)

        assert processed == parameters

    def test_inject_complex_path(self, task_executor):
        """测试复杂路径注入"""
        task_executor.execution_results["task_1"] = {
            "tool_name": "tool1",
            "result": {
                "data": {
                    "nested": {
                        "value": "deep_value"
                    }
                }
            },
            "success": True
        }

        parameters = {
            "deep": "{task_1.result.data.nested.value}"
        }

        processed = task_executor._inject_dependency_results(parameters)

        assert processed["deep"] == "deep_value"


class TestExecutionSummary:
    """执行摘要测试"""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """重置工具注册中心"""
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    @pytest.fixture
    def task_executor(self):
        """创建任务执行器"""
        registry = ToolRegistry()
        return TaskExecutor(tool_registry=registry)

    def test_build_execution_summary_success(self, task_executor):
        """测试构建成功执行摘要"""
        plan = TaskPlan(name="测试计划")
        plan.start_execution()
        plan.complete_execution()

        summary = task_executor._build_execution_summary(plan)

        assert summary["plan_id"] == plan.plan_id
        assert summary["plan_name"] == "测试计划"
        assert summary["status"] == TaskStatus.COMPLETED
        assert summary["success"] is True
        assert "progress" in summary

    def test_build_execution_summary_with_error(self, task_executor):
        """测试构建含错误的执行摘要"""
        plan = TaskPlan(name="测试计划")
        plan.start_execution()
        plan.fail_execution("执行失败")

        summary = task_executor._build_execution_summary(plan, error="错误信息")

        assert summary["success"] is False
        assert "error" in summary
        assert summary["error"] == "错误信息"

    def test_build_execution_summary_with_results(self, task_executor):
        """测试构建包含结果的执行摘要"""
        plan = TaskPlan(name="测试计划")
        plan.start_execution()

        # 添加一些执行结果
        task_executor.execution_results["task_1"] = {"result": "data1"}
        task_executor.execution_results["task_2"] = {"result": "data2"}

        plan.complete_execution()
        summary = task_executor._build_execution_summary(plan)

        assert "task_results" in summary
        assert summary["task_results"]["task_1"] == {"result": "data1"}
        assert summary["task_results"]["task_2"] == {"result": "data2"}


class TestTaskPlanHelpers:
    """任务计划辅助方法测试"""

    def test_task_plan_get_task(self):
        """测试获取任务"""
        plan = TaskPlan(name="测试计划")
        task = TaskNode(task_id="task_001", task_type=TaskType.TOOL_CALL, tool_name="test")
        plan.add_task(task)

        found_task = plan.get_task("task_001")

        assert found_task is not None
        assert found_task.task_id == "task_001"

    def test_task_plan_get_task_nonexistent(self):
        """测试获取不存在的任务"""
        plan = TaskPlan(name="测试计划")

        found_task = plan.get_task("nonexistent")

        assert found_task is None

    def test_task_plan_progress(self):
        """测试任务计划进度"""
        plan = TaskPlan(name="测试计划")

        # 添加3个任务
        plan.add_task(TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1"))
        plan.add_task(TaskNode(task_id="t2", task_type=TaskType.TOOL_CALL, tool_name="tool2"))
        plan.add_task(TaskNode(task_id="t3", task_type=TaskType.TOOL_CALL, tool_name="tool3"))

        progress = plan.get_progress()

        assert progress["total"] == 3
        assert progress["pending"] == 3
        assert progress["completed"] == 0

    def test_task_plan_topological_sort(self):
        """测试任务计划拓扑排序"""
        plan = TaskPlan(name="测试计划")

        # task1 -> task2 -> task3
        task1 = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1")
        task2 = TaskNode(task_id="t2", task_type=TaskType.TOOL_CALL, tool_name="tool2", dependencies=["t1"])
        task3 = TaskNode(task_id="t3", task_type=TaskType.TOOL_CALL, tool_name="tool3", dependencies=["t2"])

        plan.add_task(task1)
        plan.add_task(task2)
        plan.add_task(task3)

        sorted_levels = plan.topological_sort()

        # 应该返回3层
        assert len(sorted_levels) == 3
        assert sorted_levels[0][0].task_id == "t1"
        assert sorted_levels[1][0].task_id == "t2"
        assert sorted_levels[2][0].task_id == "t3"

    def test_task_plan_circular_dependency_detection(self):
        """测试循环依赖检测"""
        plan = TaskPlan(name="测试计划")

        # t1 -> t2 -> t3 -> t1 (循环)
        task1 = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1", dependencies=["t3"])
        task2 = TaskNode(task_id="t2", task_type=TaskType.TOOL_CALL, tool_name="tool2", dependencies=["t1"])
        task3 = TaskNode(task_id="t3", task_type=TaskType.TOOL_CALL, tool_name="tool3", dependencies=["t2"])

        plan.add_task(task1)
        plan.add_task(task2)
        plan.add_task(task3)

        has_cycle = plan.has_circular_dependency()

        assert has_cycle is True

    def test_task_plan_validate_dependencies(self):
        """测试依赖验证"""
        plan = TaskPlan(name="测试计划")

        # t2 依赖不存在的任务
        task1 = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1")
        task2 = TaskNode(task_id="t2", task_type=TaskType.TOOL_CALL, tool_name="tool2", dependencies=["nonexistent"])

        plan.add_task(task1)
        plan.add_task(task2)

        with pytest.raises(ValueError) as exc_info:
            plan.validate_dependencies()

        assert "不存在" in str(exc_info.value)


class TestTaskStatusManagement:
    """任务状态管理测试"""

    def test_task_status_transitions(self):
        """测试任务状态转换"""
        task = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1")

        assert task.status == TaskStatus.PENDING

        task.start_execution()
        assert task.status == TaskStatus.RUNNING

        task.complete_execution({"result": "test"})
        assert task.status == TaskStatus.COMPLETED
        assert task.result is not None

    def test_task_fail_execution(self):
        """测试任务失败"""
        task = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1")

        task.start_execution()
        task.fail_execution("执行出错")

        assert task.status == TaskStatus.FAILED
        assert task.error == "执行出错"

    def test_task_execution_time_tracking(self):
        """测试任务执行时间跟踪"""
        task = TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="tool1")

        task.start_execution()
        task.complete_execution({"result": "test"})

        assert task.execution_time is not None
        assert task.execution_time >= 0
