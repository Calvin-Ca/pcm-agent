"""
Task Plan Models - 任务规划数据模型

定义任务规划相关的数据模型，包括任务节点、任务计划等。
"""

from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from pydantic import BaseModel, Field, validator
from enum import Enum
import uuid


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"  # 待执行
    RUNNING = "running"  # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 执行失败
    SKIPPED = "skipped"  # 已跳过


class TaskType(str, Enum):
    """任务类型枚举"""
    TOOL_CALL = "tool_call"  # 工具调用
    GENERAL_CHAT = "general_chat"  # 通用聊天
    DATA_PROCESSING = "data_processing"  # 数据处理
    CONDITION_CHECK = "condition_check"  # 条件检查
    PARALLEL_GROUP = "parallel_group"  # 并行组
    SEQUENTIAL_GROUP = "sequential_group"  # 顺序组


class TaskNode(BaseModel):
    """任务节点"""
    task_id: str = Field(..., description="任务ID，唯一标识")
    task_type: TaskType = Field(..., description="任务类型")
    tool_name: Optional[str] = Field(None, description="工具名称（工具调用任务）")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    dependencies: List[str] = Field(default_factory=list, description="依赖的任务ID列表")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    result: Optional[Dict[str, Any]] = Field(None, description="任务执行结果")
    error: Optional[str] = Field(None, description="错误信息")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    started_at: Optional[datetime] = Field(None, description="开始执行时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    execution_time: Optional[float] = Field(None, description="执行时间（秒）")
    retry_count: int = Field(default=0, description="重试次数")
    max_retries: int = Field(default=3, description="最大重试次数")
    timeout: int = Field(default=60, description="超时时间（秒）")
    priority: int = Field(default=0, description="优先级（数字越大优先级越高）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    @validator('task_id')
    def validate_task_id(cls, v):
        """验证任务ID格式"""
        if not v or not v.strip():
            raise ValueError("任务ID不能为空")
        return v.strip()
    
    @validator('dependencies')
    def validate_dependencies(cls, v):
        """验证依赖关系"""
        # 去重并过滤空值
        return list(set(filter(None, v)))
    
    def is_ready_to_execute(self, completed_tasks: Set[str]) -> bool:
        """
        检查任务是否准备好执行
        
        Args:
            completed_tasks: 已完成的任务ID集合
            
        Returns:
            bool: 是否准备好执行
        """
        if self.status != TaskStatus.PENDING:
            return False
        
        # 检查所有依赖是否已完成
        for dep_id in self.dependencies:
            if dep_id not in completed_tasks:
                return False
        
        return True
    
    def start_execution(self):
        """开始执行任务"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
    
    def complete_execution(self, result: Dict[str, Any]):
        """完成任务执行"""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.result = result
        if self.started_at:
            self.execution_time = (self.completed_at - self.started_at).total_seconds()
    
    def fail_execution(self, error: str):
        """任务执行失败"""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now()
        self.error = error
        if self.started_at:
            self.execution_time = (self.completed_at - self.started_at).total_seconds()
    
    def skip_execution(self, reason: str):
        """跳过任务执行"""
        self.status = TaskStatus.SKIPPED
        self.completed_at = datetime.now()
        self.error = reason
    
    def can_retry(self) -> bool:
        """检查是否可以重试"""
        return self.status == TaskStatus.FAILED and self.retry_count < self.max_retries
    
    def reset_for_retry(self):
        """重置任务状态以便重试"""
        if self.can_retry():
            self.status = TaskStatus.PENDING
            self.started_at = None
            self.completed_at = None
            self.execution_time = None
            self.retry_count += 1
            self.error = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TaskPlan(BaseModel):
    """任务计划"""
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="计划ID")
    name: str = Field(..., description="计划名称")
    description: str = Field(default="", description="计划描述")
    user_request: str = Field(default="", description="原始用户请求")
    tasks: Dict[str, TaskNode] = Field(default_factory=dict, description="任务节点字典")
    execution_order: List[List[str]] = Field(default_factory=list, description="执行顺序（按层级）")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="计划状态")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    started_at: Optional[datetime] = Field(None, description="开始执行时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    total_execution_time: Optional[float] = Field(None, description="总执行时间（秒）")
    success_count: int = Field(default=0, description="成功任务数")
    failed_count: int = Field(default=0, description="失败任务数")
    skipped_count: int = Field(default=0, description="跳过任务数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    @validator('name')
    def validate_name(cls, v):
        """验证计划名称"""
        if not v or not v.strip():
            raise ValueError("计划名称不能为空")
        return v.strip()
    
    def add_task(self, task: TaskNode) -> bool:
        """
        添加任务到计划中
        
        Args:
            task: 任务节点
            
        Returns:
            bool: 是否添加成功
        """
        if task.task_id in self.tasks:
            return False
        
        self.tasks[task.task_id] = task
        return True
    
    def remove_task(self, task_id: str) -> bool:
        """
        从计划中移除任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            bool: 是否移除成功
        """
        if task_id not in self.tasks:
            return False
        
        # 检查是否有其他任务依赖此任务
        for task in self.tasks.values():
            if task_id in task.dependencies:
                return False  # 有依赖，不能移除
        
        del self.tasks[task_id]
        return True
    
    def get_task(self, task_id: str) -> Optional[TaskNode]:
        """获取任务节点"""
        return self.tasks.get(task_id)
    
    def get_ready_tasks(self) -> List[TaskNode]:
        """
        获取准备好执行的任务
        
        Returns:
            List[TaskNode]: 准备好执行的任务列表
        """
        completed_tasks = {
            task_id for task_id, task in self.tasks.items()
            if task.status == TaskStatus.COMPLETED
        }
        
        ready_tasks = []
        for task in self.tasks.values():
            if task.is_ready_to_execute(completed_tasks):
                ready_tasks.append(task)
        
        # 按优先级排序
        ready_tasks.sort(key=lambda t: t.priority, reverse=True)
        return ready_tasks
    
    def get_running_tasks(self) -> List[TaskNode]:
        """获取正在执行的任务"""
        return [task for task in self.tasks.values() if task.status == TaskStatus.RUNNING]
    
    def get_completed_tasks(self) -> List[TaskNode]:
        """获取已完成的任务"""
        return [task for task in self.tasks.values() if task.status == TaskStatus.COMPLETED]
    
    def get_failed_tasks(self) -> List[TaskNode]:
        """获取失败的任务"""
        return [task for task in self.tasks.values() if task.status == TaskStatus.FAILED]
    
    def is_completed(self) -> bool:
        """检查计划是否已完成"""
        if not self.tasks:
            return True
        
        for task in self.tasks.values():
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                return False
        
        return True
    
    def has_failed_tasks(self) -> bool:
        """检查是否有失败的任务"""
        return any(task.status == TaskStatus.FAILED for task in self.tasks.values())
    
    def get_progress(self) -> Dict[str, Any]:
        """
        获取执行进度
        
        Returns:
            Dict[str, Any]: 进度信息
        """
        total_tasks = len(self.tasks)
        if total_tasks == 0:
            return {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "running": 0,
                "pending": 0,
                "skipped": 0,
                "progress_percentage": 100.0
            }
        
        completed = len(self.get_completed_tasks())
        failed = len(self.get_failed_tasks())
        running = len(self.get_running_tasks())
        skipped = len([t for t in self.tasks.values() if t.status == TaskStatus.SKIPPED])
        pending = total_tasks - completed - failed - running - skipped
        
        progress_percentage = (completed + failed + skipped) / total_tasks * 100
        
        return {
            "total": total_tasks,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "skipped": skipped,
            "progress_percentage": round(progress_percentage, 2)
        }
    
    def start_execution(self):
        """开始执行计划"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
    
    def complete_execution(self):
        """完成计划执行"""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        if self.started_at:
            self.total_execution_time = (self.completed_at - self.started_at).total_seconds()
        
        # 更新统计信息
        self.success_count = len(self.get_completed_tasks())
        self.failed_count = len(self.get_failed_tasks())
        self.skipped_count = len([t for t in self.tasks.values() if t.status == TaskStatus.SKIPPED])
    
    def fail_execution(self, error: str):
        """计划执行失败"""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now()
        if self.started_at:
            self.total_execution_time = (self.completed_at - self.started_at).total_seconds()
        
        # 更新统计信息
        self.success_count = len(self.get_completed_tasks())
        self.failed_count = len(self.get_failed_tasks())
        self.skipped_count = len([t for t in self.tasks.values() if t.status == TaskStatus.SKIPPED])
    
    def has_circular_dependency(self) -> bool:
        """
        检查任务计划是否存在循环依赖
        
        Returns:
            bool: 是否存在循环依赖
        """
        # 使用DFS检测循环依赖
        visited = set()
        rec_stack = set()
        
        def dfs(task_id: str) -> bool:
            if task_id in rec_stack:
                return True  # 发现循环
            if task_id in visited:
                return False
            
            visited.add(task_id)
            rec_stack.add(task_id)
            
            task = self.get_task(task_id)
            if task:
                for dep in task.dependencies:
                    if dfs(dep):
                        return True
            
            rec_stack.remove(task_id)
            return False
        
        for task_id in self.tasks:
            if task_id not in visited:
                if dfs(task_id):
                    return True
        
        return False
    
    def topological_sort(self) -> List[List[TaskNode]]:
        """
        对任务进行拓扑排序，返回按依赖层级分组的任务
        使用BFS算法实现
        
        Returns:
            List[List[TaskNode]]: 按依赖层级分组的任务列表
        """
        if not self.tasks:
            return []
        
        # 计算每个任务的入度
        in_degree = {}
        for task_id in self.tasks:
            in_degree[task_id] = 0
        
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep in in_degree:
                    in_degree[task.task_id] += 1
        
        # BFS拓扑排序
        levels = []
        current_level = []
        
        # 找到入度为0的任务作为第一层
        for task_id, degree in in_degree.items():
            if degree == 0:
                current_level.append(self.tasks[task_id])
        
        while current_level:
            levels.append(current_level[:])  # 复制当前层
            next_level = []
            
            # 处理当前层的任务
            for task in current_level:
                # 减少依赖此任务的其他任务的入度
                for other_task in self.tasks.values():
                    if task.task_id in other_task.dependencies:
                        in_degree[other_task.task_id] -= 1
                        if in_degree[other_task.task_id] == 0:
                            next_level.append(other_task)
            
            current_level = next_level
        
        # 更新执行顺序
        self.execution_order = [[task.task_id for task in level] for level in levels]
        
        return levels
    
    def group_by_dependency_level(self) -> Dict[int, List[TaskNode]]:
        """
        按依赖层级对任务进行分组
        
        Returns:
            Dict[int, List[TaskNode]]: 层级到任务列表的映射
        """
        levels = self.topological_sort()
        return {i: level for i, level in enumerate(levels)}
    
    def validate_dependencies(self) -> bool:
        """
        验证任务依赖关系的有效性
        
        Returns:
            bool: 依赖关系是否有效
            
        Raises:
            ValueError: 如果依赖关系无效
        """
        if not self.tasks:
            return True
        
        # 检查循环依赖
        if self.has_circular_dependency():
            raise ValueError("任务计划存在循环依赖")
        
        # 检查依赖任务是否存在
        task_ids = set(self.tasks.keys())
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"任务 {task.task_id} 依赖的任务 {dep} 不存在")
        
        return True
    
    def to_summary(self) -> Dict[str, Any]:
        """
        生成计划摘要
        
        Returns:
            Dict[str, Any]: 计划摘要
        """
        progress = self.get_progress()
        
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "progress": progress,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_execution_time": self.total_execution_time,
            "task_count": len(self.tasks),
            "execution_order_levels": len(self.execution_order)
        }

    def has_circular_dependency(self) -> bool:
        """
        检查任务计划是否存在循环依赖

        Returns:
            bool: 是否存在循环依赖
        """
        # 使用DFS检测循环依赖
        visited = set()
        rec_stack = set()

        def dfs(task_id: str) -> bool:
            if task_id in rec_stack:
                return True  # 发现循环
            if task_id in visited:
                return False

            visited.add(task_id)
            rec_stack.add(task_id)

            task = self.get_task(task_id)
            if task:
                for dep in task.dependencies:
                    if dfs(dep):
                        return True

            rec_stack.remove(task_id)
            return False

        for task_id in self.tasks:
            if task_id not in visited:
                if dfs(task_id):
                    return True

        return False

    def topological_sort(self) -> List[List[TaskNode]]:
        """
        对任务进行拓扑排序，返回按依赖层级分组的任务
        使用BFS算法实现

        Returns:
            List[List[TaskNode]]: 按依赖层级分组的任务列表
        """
        if not self.tasks:
            return []

        # 计算每个任务的入度
        in_degree = {}
        for task_id in self.tasks:
            in_degree[task_id] = 0

        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep in in_degree:
                    in_degree[task.task_id] += 1

        # BFS拓扑排序
        levels = []
        current_level = []

        # 找到入度为0的任务作为第一层
        for task_id, degree in in_degree.items():
            if degree == 0:
                current_level.append(self.tasks[task_id])

        while current_level:
            levels.append(current_level[:])  # 复制当前层
            next_level = []

            # 处理当前层的任务
            for task in current_level:
                # 减少依赖此任务的其他任务的入度
                for other_task in self.tasks.values():
                    if task.task_id in other_task.dependencies:
                        in_degree[other_task.task_id] -= 1
                        if in_degree[other_task.task_id] == 0:
                            next_level.append(other_task)

            current_level = next_level

        # 更新执行顺序
        self.execution_order = [[task.task_id for task in level] for level in levels]

        return levels

    def group_by_dependency_level(self) -> Dict[int, List[TaskNode]]:
        """
        按依赖层级对任务进行分组

        Returns:
            Dict[int, List[TaskNode]]: 层级到任务列表的映射
        """
        levels = self.topological_sort()
        return {i: level for i, level in enumerate(levels)}

    def validate_dependencies(self) -> bool:
        """
        验证任务依赖关系的有效性

        Returns:
            bool: 依赖关系是否有效

        Raises:
            ValueError: 如果依赖关系无效
        """
        if not self.tasks:
            return True

        # 检查循环依赖
        if self.has_circular_dependency():
            raise ValueError("任务计划存在循环依赖")

        # 检查依赖任务是否存在
        task_ids = set(self.tasks.keys())
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"任务 {task.task_id} 依赖的任务 {dep} 不存在")

        return True
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }



class PlannerAgent:
    """
    任务规划代理，使用ReAct框架生成任务执行计划
    """

    def __init__(self, tool_registry=None, llm_client=None):
        """
        初始化规划代理

        Args:
            tool_registry: 工具注册中心实例
            llm_client: LLM客户端实例
        """
        self.tool_registry = tool_registry
        self.llm_client = llm_client

    async def plan_tasks(
        self,
        user_request: str,
        user_context: Dict[str, Any] = None,
        available_tools: List[str] = None
    ) -> TaskPlan:
        """
        基于用户请求生成任务执行计划

        Args:
            user_request: 用户请求描述
            user_context: 用户上下文信息（权限、部门等）
            available_tools: 可用工具列表

        Returns:
            TaskPlan: 生成的任务计划
        """
        if not self.llm_client:
            raise ValueError("LLM client is required for task planning")

        # 获取可用工具信息
        tools_info = self._get_tools_info(available_tools)

        # 构建规划提示词
        planning_prompt = self._build_planning_prompt(
            user_request, user_context, tools_info
        )

        # 调用LLM生成任务计划
        try:
            response = await self.llm_client.generate(
                prompt=planning_prompt,
                max_tokens=2000,
                temperature=0.1
            )

            # 解析LLM响应生成任务计划
            task_plan = self._parse_llm_response(response, user_request)

            # 验证任务计划的有效性
            self._validate_task_plan(task_plan)

            return task_plan

        except Exception as e:
            # 如果LLM规划失败，生成简单的单任务计划
            return self._create_fallback_plan(user_request, user_context)

    def _get_tools_info(self, available_tools: List[str] = None) -> List[Dict[str, Any]]:
        """
        获取可用工具的详细信息

        Args:
            available_tools: 可用工具名称列表

        Returns:
            List[Dict]: 工具信息列表
        """
        if not self.tool_registry:
            return []

        tools_info = []
        tool_names = available_tools or self.tool_registry.list_tools()

        for tool_name in tool_names:
            try:
                tool_def = self.tool_registry.get_tool(tool_name)
                if tool_def:
                    tools_info.append({
                        "name": tool_def.name,
                        "description": tool_def.description,
                        "parameters": tool_def.json_schema.get("properties", {}),
                        "required": tool_def.json_schema.get("required", []),
                    })
            except Exception:
                continue

        return tools_info

    def _build_planning_prompt(
        self,
        user_request: str,
        user_context: Dict[str, Any] = None,
        tools_info: List[Dict[str, Any]] = None
    ) -> str:
        """
        构建任务规划的提示词模板

        Args:
            user_request: 用户请求
            user_context: 用户上下文
            tools_info: 工具信息

        Returns:
            str: 构建的提示词
        """
        context_str = ""
        if user_context:
            context_str = f"""
用户上下文信息：
- 用户ID: {user_context.get('user_id', 'unknown')}
- 用户角色: {user_context.get('entity_type', 'unknown')}
- 部门ID: {user_context.get('department_id', 'unknown')}
"""

        tools_str = ""
        if tools_info:
            tools_str = "可用工具列表：\n"
            for tool in tools_info:
                # 处理不同格式的parameters（字典或列表）
                params = tool.get('parameters', {})
                if isinstance(params, dict):
                    # 字典格式: {"param_name": {"type": "string"}}
                    params_str = ", ".join([
                        f"{name}({info.get('type', 'any') if isinstance(info, dict) else 'any'})"
                        for name, info in params.items()
                    ])
                elif isinstance(params, list):
                    # 列表格式: [{"name": "param_name", "type": "string"}]
                    params_str = ", ".join([
                        f"{param.get('name', 'unknown')}({param.get('type', 'any')})"
                        for param in params
                    ])
                else:
                    params_str = ""
                tools_str += f"- {tool['name']}: {tool['description']} [参数: {params_str}]\n"

        prompt = f"""你是一个智能任务规划助手，需要将用户的复杂请求分解为可执行的任务序列。

{context_str}

{tools_str}

用户请求：{user_request}

请按照以下JSON格式生成任务执行计划：

PLAN_START
{{
  "plan_name": "任务计划名称",
  "description": "计划描述",
  "tasks": [
    {{
      "task_id": "task_1",
      "task_type": "tool_call",
      "tool_name": "工具名称",
      "description": "任务描述",
      "parameters": {{
        "param1": "value1",
        "param2": "value2"
      }},
      "dependencies": []
    }},
    {{
      "task_id": "task_2",
      "task_type": "tool_call",
      "tool_name": "工具名称",
      "description": "任务描述",
      "parameters": {{
        "param1": "{{task_1.result.field}}"
      }},
      "dependencies": ["task_1"]
    }}
  ]
}}
PLAN_END

规划原则：
1. 将复杂请求分解为简单的工具调用任务
2. 明确任务之间的依赖关系
3. 参数可以引用前置任务的结果，格式为 {{task_id.result.field}}
4. 每个任务都要有唯一的task_id
5. 优先使用可用工具完成任务
6. 如果没有合适的工具，创建general_chat类型的任务

请生成任务计划："""

        return prompt

    def _parse_llm_response(self, response: str, user_request: str) -> TaskPlan:
        """
        解析LLM响应生成TaskPlan对象

        Args:
            response: LLM响应文本
            user_request: 原始用户请求

        Returns:
            TaskPlan: 解析生成的任务计划
        """
        import json
        import re

        # 提取JSON部分（支持PLAN_START/PLAN_END标记或纯JSON）
        plan_match = re.search(r'PLAN_START\s*(.*?)\s*PLAN_END', response, re.DOTALL)

        try:
            if plan_match:
                # 从标记中提取JSON
                plan_json = json.loads(plan_match.group(1))
            else:
                # 尝试直接解析纯JSON
                plan_json = json.loads(response)
        except json.JSONDecodeError:
            # JSON解析失败，返回降级计划
            return self._create_fallback_plan(user_request)

        # 创建TaskPlan对象
        plan_name = plan_json.get("plan_name", f"Plan for: {user_request[:50]}")
        description = plan_json.get("description", "Auto-generated task plan")

        task_plan = TaskPlan(
            name=plan_name,
            description=description,
            user_request=user_request
        )

        # 添加任务节点
        for task_data in plan_json.get("tasks", []):
            task_type_str = task_data.get("task_type", "tool_call")
            task_type = TaskType.TOOL_CALL if task_type_str == "tool_call" else TaskType.GENERAL_CHAT

            task_node = TaskNode(
                task_id=task_data["task_id"],
                task_type=task_type,
                tool_name=task_data.get("tool_name"),
                description=task_data.get("description", ""),
                parameters=task_data.get("parameters", {}),
                dependencies=task_data.get("dependencies", [])
            )

            task_plan.add_task(task_node)

        return task_plan

    def _validate_task_plan(self, task_plan: TaskPlan):
        """
        验证任务计划的有效性

        Args:
            task_plan: 要验证的任务计划

        Raises:
            ValueError: 如果任务计划无效
        """
        if not task_plan.tasks:
            raise ValueError("任务计划不能为空")

        # 检查依赖任务是否存在
        task_ids = set(task_plan.tasks.keys())
        for task in task_plan.tasks.values():
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"任务 {task.task_id} 依赖的任务 {dep} 不存在")

    def _has_circular_dependency(self, task_plan: TaskPlan) -> bool:
        """
        检查任务计划是否存在循环依赖

        Args:
            task_plan: 要检查的任务计划

        Returns:
            bool: 是否存在循环依赖
        """
        # 使用DFS检测循环依赖
        visited = set()
        rec_stack = set()

        def dfs(task_id: str) -> bool:
            if task_id in rec_stack:
                return True  # 发现循环
            if task_id in visited:
                return False

            visited.add(task_id)
            rec_stack.add(task_id)

            task = task_plan.get_task(task_id)
            if task:
                for dep in task.dependencies:
                    if dfs(dep):
                        return True

            rec_stack.remove(task_id)
            return False

        for task_id in task_plan.tasks:
            if task_id not in visited:
                if dfs(task_id):
                    return True

        return False

    def _create_fallback_plan(
        self,
        user_request: str,
        user_context: Dict[str, Any] = None
    ) -> TaskPlan:
        """
        创建备用的简单任务计划

        Args:
            user_request: 用户请求
            user_context: 用户上下文

        Returns:
            TaskPlan: 简单的单任务计划
        """
        task_plan = TaskPlan(
            name="Fallback Plan",
            description="Fallback single-task plan",
            user_request=user_request
        )

        # 创建一个通用聊天任务
        fallback_task = TaskNode(
            task_id="fallback_task",
            task_type=TaskType.GENERAL_CHAT,
            description=f"Handle user request: {user_request}",
            parameters={"message": user_request}
        )

        task_plan.add_task(fallback_task)
        return task_plan

    def topological_sort(self, task_plan: TaskPlan) -> List[List[TaskNode]]:
        """
        对任务进行拓扑排序，返回按依赖层级分组的任务

        Args:
            task_plan: 任务计划

        Returns:
            List[List[TaskNode]]: 按依赖层级分组的任务列表
        """
        if not task_plan.tasks:
            return []

        # 计算每个任务的入度
        in_degree = {}
        task_map = task_plan.tasks  # tasks is already a dict

        for task_id in task_map:
            in_degree[task_id] = 0

        for task in task_map.values():
            for dep in task.dependencies:
                if dep in in_degree:
                    in_degree[task.task_id] += 1

        # BFS拓扑排序
        levels = []
        current_level = []

        # 找到入度为0的任务作为第一层
        for task_id, degree in in_degree.items():
            if degree == 0:
                current_level.append(task_map[task_id])

        while current_level:
            levels.append(current_level[:])  # 复制当前层
            next_level = []

            # 处理当前层的任务
            for task in current_level:
                # 减少依赖此任务的其他任务的入度
                for other_task in task_map.values():
                    if task.task_id in other_task.dependencies:
                        in_degree[other_task.task_id] -= 1
                        if in_degree[other_task.task_id] == 0:
                            next_level.append(other_task)

            current_level = next_level

        return levels

    def group_by_dependency_level(self, task_plan: TaskPlan) -> Dict[int, List[TaskNode]]:
        """
        按依赖层级对任务进行分组

        Args:
            task_plan: 任务计划

        Returns:
            Dict[int, List[TaskNode]]: 层级到任务列表的映射
        """
        levels = self.topological_sort(task_plan)
        return {i: level for i, level in enumerate(levels)}
