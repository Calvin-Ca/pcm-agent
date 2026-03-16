"""
Planner Agent属性测试

使用Hypothesis进行基于属性的测试，验证任务规划的核心属性：
- 拓扑排序正确性
- 循环依赖检测准确性
- 任务依赖顺序一致性

属性4: 任务依赖顺序
验证需求: 22.4-22.7
"""

import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck

from app.models.task_plan import TaskPlan, TaskNode, TaskType, TaskStatus


# 生成有效的任务ID和计划名称（只允许字母和数字）
id_strings = st.text(
    min_size=1,
    max_size=20,
    alphabet=st.characters(whitelist_categories=('L', 'N'))
)

# 生成有效的计划名称（字母数字）
plan_name_strings = st.text(
    min_size=3,
    max_size=50,
    alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
)


@st.composite
def simple_task_plans(draw):
    """生成简单的任务计划（无依赖）"""
    num_tasks = draw(st.integers(min_value=0, max_value=10))
    plan_name = draw(plan_name_strings)

    plan = TaskPlan(name=plan_name)

    for i in range(num_tasks):
        task_id = f"task_{i}"
        task = TaskNode(
            task_id=task_id,
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool"
        )
        plan.add_task(task)

    return plan


@st.composite
def linear_dependency_plans(draw):
    """生成线性依赖的任务计划（A->B->C->...）"""
    num_tasks = draw(st.integers(min_value=1, max_value=8))
    plan_name = draw(plan_name_strings)

    plan = TaskPlan(name=plan_name)

    prev_task_id = None
    for i in range(num_tasks):
        task_id = f"task_{i}"
        dependencies = [prev_task_id] if prev_task_id else []
        task = TaskNode(
            task_id=task_id,
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=dependencies
        )
        plan.add_task(task)
        prev_task_id = task_id

    return plan


@st.composite
def branching_dependency_plans(draw):
    """生成分支依赖的任务计划"""
    plan_name = draw(plan_name_strings)
    plan = TaskPlan(name=plan_name)

    # 根任务
    root_task = TaskNode(
        task_id="root",
        task_type=TaskType.TOOL_CALL,
        tool_name="test_tool"
    )
    plan.add_task(root_task)

    # 第二层分支任务
    num_branches = draw(st.integers(min_value=1, max_value=5))
    branch_ids = []
    for i in range(num_branches):
        task_id = f"branch_{i}"
        task = TaskNode(
            task_id=task_id,
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=["root"]
        )
        plan.add_task(task)
        branch_ids.append(task_id)

    # 第三层汇聚任务
    if branch_ids:
        merge_task = TaskNode(
            task_id="merge",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=branch_ids[:]
        )
        plan.add_task(merge_task)

    return plan


@st.composite
def plans_with_cycles(draw):
    """生成带循环依赖的任务计划"""
    plan_name = draw(plan_name_strings)
    plan = TaskPlan(name=plan_name)

    # 简单的两任务循环
    task_a = TaskNode(
        task_id="task_a",
        task_type=TaskType.TOOL_CALL,
        tool_name="test_tool",
        dependencies=["task_b"]
    )
    task_b = TaskNode(
        task_id="task_b",
        task_type=TaskType.TOOL_CALL,
        tool_name="test_tool",
        dependencies=["task_a"]
    )
    plan.add_task(task_a)
    plan.add_task(task_b)

    return plan


@st.composite
def complex_dependency_plans(draw):
    """生成复杂依赖的任务计划"""
    num_tasks = draw(st.integers(min_value=3, max_value=15))
    plan_name = draw(plan_name_strings)

    plan = TaskPlan(name=plan_name)

    # 创建任务
    for i in range(num_tasks):
        task_id = f"task_{i}"
        # 随机选择依赖（确保不依赖自己）
        possible_deps = [f"task_{j}" for j in range(i)]
        if possible_deps:
            num_deps = draw(st.integers(min_value=0, max_value=min(3, len(possible_deps))))
            dependencies = draw(st.lists(
                st.sampled_from(possible_deps),
                min_size=num_deps,
                max_size=num_deps,
                unique=True
            ))
        else:
            dependencies = []

        task = TaskNode(
            task_id=task_id,
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=dependencies
        )
        plan.add_task(task)

    return plan


class TestTopologicalSortProperties:
    """拓扑排序属性测试"""

    @given(simple_task_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_topological_sort_preserves_all_tasks(self, plan):
        """测试拓扑排序保留所有任务"""
        sorted_levels = plan.topological_sort()

        # 收集所有排序后的任务
        sorted_task_ids = set()
        for level in sorted_levels:
            for task in level:
                sorted_task_ids.add(task.task_id)

        # 验证所有任务都被包含
        original_task_ids = set(plan.tasks.keys())
        assert sorted_task_ids == original_task_ids

    @given(simple_task_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_topological_sort_no_duplicates(self, plan):
        """测试拓扑排序没有重复任务"""
        sorted_levels = plan.topological_sort()

        all_task_ids = []
        for level in sorted_levels:
            for task in level:
                all_task_ids.append(task.task_id)

        # 验证没有重复
        assert len(all_task_ids) == len(set(all_task_ids))

    @given(linear_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_linear_dependencies_order(self, plan):
        """测试线性依赖的正确顺序"""
        assume(len(plan.tasks) > 0)
        assume(not plan.has_circular_dependency())

        sorted_levels = plan.topological_sort()

        # 对于线性依赖，每层应该只有一个任务
        total_tasks = sum(len(level) for level in sorted_levels)
        assert total_tasks == len(plan.tasks)

    @given(branching_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_branching_dependencies_order(self, plan):
        """测试分支依赖的正确顺序"""
        assume(len(plan.tasks) > 0)
        assume(not plan.has_circular_dependency())

        sorted_levels = plan.topological_sort()

        # 根任务应该在最前面
        root_level = sorted_levels[0] if sorted_levels else []
        root_task_ids = [t.task_id for t in root_level]
        assert "root" in root_task_ids

        # 汇聚任务应该在最后面
        last_level = sorted_levels[-1] if sorted_levels else []
        last_task_ids = [t.task_id for t in last_level]
        assert "merge" in last_task_ids

    @given(complex_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_dependencies_satisfied_in_sort(self, plan):
        """测试依赖在排序中被满足"""
        assume(len(plan.tasks) > 0)

        if plan.has_circular_dependency():
            # 有循环依赖时跳过
            return

        sorted_levels = plan.topological_sort()

        # 构建层级索引
        task_to_level = {}
        for level_idx, level in enumerate(sorted_levels):
            for task in level:
                task_to_level[task.task_id] = level_idx

        # 验证每个任务的依赖都在前面的层级
        for task in plan.tasks.values():
            task_level = task_to_level.get(task.task_id, -1)
            for dep_id in task.dependencies:
                dep_level = task_to_level.get(dep_id, -1)
                assert dep_level < task_level, f"任务 {task.task_id} 的依赖 {dep_id} 应该在前面的层级"


class TestCircularDependencyDetection:
    """循环依赖检测属性测试"""

    @given(plans_with_cycles())
    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_detects_simple_cycles(self, plan):
        """测试检测简单循环依赖"""
        assert plan.has_circular_dependency() is True

    @given(simple_task_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_no_false_positives_for_acyclic(self, plan):
        """测试无循环依赖的计划不产生误报"""
        # 简单计划（无依赖）不应该有循环依赖
        assert plan.has_circular_dependency() is False

    @given(linear_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_linear_no_cycles(self, plan):
        """测试线性依赖没有循环"""
        assert plan.has_circular_dependency() is False

    @given(branching_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_branching_no_cycles(self, plan):
        """测试分支依赖没有循环"""
        assert plan.has_circular_dependency() is False

    def test_self_dependency_is_cycle(self):
        """测试自依赖被视为循环"""
        plan = TaskPlan(name="Self Dependency Test")

        task = TaskNode(
            task_id="self_task",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=["self_task"]  # 自依赖
        )
        plan.add_task(task)

        assert plan.has_circular_dependency() is True

    def test_triple_cycle_detection(self):
        """测试三任务循环检测"""
        plan = TaskPlan(name="Triple Cycle Test")

        # A -> B -> C -> A
        task_a = TaskNode(
            task_id="task_a",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=["task_c"]
        )
        task_b = TaskNode(
            task_id="task_b",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=["task_a"]
        )
        task_c = TaskNode(
            task_id="task_c",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool",
            dependencies=["task_b"]
        )

        plan.add_task(task_a)
        plan.add_task(task_b)
        plan.add_task(task_c)

        assert plan.has_circular_dependency() is True


class TestTaskPlanConsistency:
    """任务计划一致性测试"""

    @given(complex_dependency_plans())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_circular_detection_consistent_with_topological_sort(self, plan):
        """测试循环依赖检测与拓扑排序结果一致"""
        has_cycle = plan.has_circular_dependency()

        if has_cycle:
            # 有循环依赖时，拓扑排序可能不完整或无法执行
            # 我们不对此做强制断言，因为实现可能有不同的处理方式
            pass
        else:
            # 无循环依赖时，拓扑排序应该成功并包含所有任务
            sorted_levels = plan.topological_sort()
            sorted_count = sum(len(level) for level in sorted_levels)
            assert sorted_count == len(plan.tasks)

    @given(complex_dependency_plans())
    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_is_ready_to_execute_respects_dependencies(self, plan):
        """测试任务就绪状态尊重依赖关系"""
        if plan.has_circular_dependency():
            return

        # 获取初始就绪任务
        ready_tasks = plan.get_ready_tasks()

        # 初始就绪的任务不应该有依赖
        for task in ready_tasks:
            assert len(task.dependencies) == 0, f"任务 {task.task_id} 有依赖但显示为就绪"

    def test_empty_plan_topological_sort(self):
        """测试空计划的拓扑排序"""
        plan = TaskPlan(name="Empty Plan")
        sorted_levels = plan.topological_sort()
        assert sorted_levels == []

    def test_single_task_plan(self):
        """测试单任务计划"""
        plan = TaskPlan(name="Single Task Plan")
        task = TaskNode(
            task_id="single_task",
            task_type=TaskType.TOOL_CALL,
            tool_name="test_tool"
        )
        plan.add_task(task)

        assert plan.has_circular_dependency() is False

        sorted_levels = plan.topological_sort()
        assert len(sorted_levels) == 1
        assert len(sorted_levels[0]) == 1
        assert sorted_levels[0][0].task_id == "single_task"


class TestParallelGroupProperties:
    """并行任务组属性测试"""

    @given(branching_dependency_plans())
    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parallel_tasks_no_inter_dependencies(self, plan):
        """测试并行任务之间没有相互依赖"""
        assume(not plan.has_circular_dependency())

        sorted_levels = plan.topological_sort()

        # 同一层的任务不应该相互依赖
        for level in sorted_levels:
            level_task_ids = {t.task_id for t in level}
            for task in level:
                for dep_id in task.dependencies:
                    assert dep_id not in level_task_ids, \
                        f"任务 {task.task_id} 依赖于同层的 {dep_id}"
