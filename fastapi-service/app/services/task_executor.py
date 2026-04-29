"""
Task Executor - 任务执行器

负责执行任务计划中的任务，支持按拓扑顺序执行、并行执行同层级任务、依赖结果注入等功能。
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

from ..models.task_plan import TaskPlan, TaskNode, TaskStatus, TaskType
from .tool_registry import ToolRegistry
from .permission_validator import PermissionValidator, PermissionContext


logger = logging.getLogger(__name__)


class TaskExecutor:
    """
    任务执行器，负责执行任务计划中的任务
    """
    
    def __init__(
        self, 
        tool_registry: ToolRegistry,
        permission_validator: PermissionValidator = None,
        llm_client=None
    ):
        """
        初始化任务执行器
        
        Args:
            tool_registry: 工具注册中心实例
            permission_validator: 权限验证器实例
            llm_client: LLM客户端实例
        """
        self.tool_registry = tool_registry
        self.permission_validator = permission_validator
        self.llm_client = llm_client
        self.execution_results = {}  # 存储任务执行结果
        
    async def execute_plan(
        self, 
        task_plan: TaskPlan, 
        permission_context: PermissionContext = None,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """
        执行任务计划（按拓扑顺序执行）
        
        Args:
            task_plan: 要执行的任务计划
            permission_context: 权限上下文
            timeout: 总超时时间（秒）
            
        Returns:
            Dict[str, Any]: 执行结果摘要
        """
        logger.info(f"开始执行任务计划: {task_plan.name}")
        
        # 验证任务计划
        try:
            task_plan.validate_dependencies()
        except ValueError as e:
            logger.error(f"任务计划验证失败: {e}")
            task_plan.fail_execution(str(e))
            return self._build_execution_summary(task_plan, error=str(e))
        
        # 开始执行
        task_plan.start_execution()
        
        try:
            # 获取拓扑排序后的任务层级
            task_levels = task_plan.topological_sort()
            
            # 按层级顺序执行任务
            for level_index, level_tasks in enumerate(task_levels):
                logger.info(f"执行第 {level_index + 1} 层任务，共 {len(level_tasks)} 个任务")
                
                # 并行执行同层级任务
                await self._execute_level_tasks(
                    level_tasks, 
                    permission_context, 
                    timeout
                )
                
                # 检查是否有任务失败
                failed_tasks = [task for task in level_tasks if task.status == TaskStatus.FAILED]
                if failed_tasks:
                    error_msg = f"第 {level_index + 1} 层有 {len(failed_tasks)} 个任务失败"
                    logger.error(error_msg)
                    task_plan.fail_execution(error_msg)
                    return self._build_execution_summary(task_plan, error=error_msg)
            
            # 所有任务执行完成
            task_plan.complete_execution()
            logger.info(f"任务计划执行完成: {task_plan.name}")
            
            return self._build_execution_summary(task_plan)
            
        except Exception as e:
            error_msg = f"任务计划执行异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            task_plan.fail_execution(error_msg)
            return self._build_execution_summary(task_plan, error=error_msg)
    
    async def _execute_level_tasks(
        self, 
        tasks: List[TaskNode], 
        permission_context: PermissionContext = None,
        timeout: int = 60
    ):
        """
        并行执行同层级任务
        
        Args:
            tasks: 同层级的任务列表
            permission_context: 权限上下文
            timeout: 单个任务超时时间（秒）
        """
        if not tasks:
            return
        
        # 创建并发任务
        concurrent_tasks = []
        for task in tasks:
            if task.status == TaskStatus.PENDING:
                concurrent_task = asyncio.create_task(
                    self.execute_single_task(task, permission_context, timeout)
                )
                concurrent_tasks.append(concurrent_task)
        
        # 等待所有任务完成
        if concurrent_tasks:
            await asyncio.gather(*concurrent_tasks, return_exceptions=True)
    
    async def execute_single_task(
        self, 
        task: TaskNode, 
        permission_context: PermissionContext = None,
        timeout: int = 60
    ) -> Dict[str, Any]:
        """
        执行单个任务
        
        Args:
            task: 要执行的任务
            permission_context: 权限上下文
            timeout: 超时时间（秒）
            
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        logger.info(f"开始执行任务: {task.task_id} ({task.task_type})")
        
        # 开始执行
        task.start_execution()
        
        try:
            # 使用超时控制
            result = await asyncio.wait_for(
                self._execute_task_by_type(task, permission_context),
                timeout=timeout
            )
            
            # 执行成功
            task.complete_execution(result)
            self.execution_results[task.task_id] = result
            
            logger.info(f"任务执行成功: {task.task_id}")
            return result
            
        except asyncio.TimeoutError:
            error_msg = f"任务执行超时 ({timeout}秒)"
            logger.error(f"任务 {task.task_id} 执行超时")
            task.fail_execution(error_msg)
            return {"success": False, "error": error_msg}

        except PermissionError as e:
            error_msg = str(e)
            logger.warning(f"任务 {task.task_id} 权限拒绝: {error_msg}")
            task.fail_execution(error_msg)
            return {"success": False, "error": error_msg}

        except Exception as e:
            error_msg = f"任务执行异常: {str(e)}"
            logger.error(f"任务 {task.task_id} 执行异常: {e}", exc_info=True)
            task.fail_execution(error_msg)
            return {"success": False, "error": error_msg}
    
    async def _execute_task_by_type(
        self, 
        task: TaskNode, 
        permission_context: PermissionContext = None
    ) -> Dict[str, Any]:
        """
        根据任务类型执行任务
        
        Args:
            task: 要执行的任务
            permission_context: 权限上下文
            
        Returns:
            Dict[str, Any]: 执行结果
        """
        if task.task_type == TaskType.TOOL_CALL:
            return await self._execute_tool_call(task, permission_context)
        elif task.task_type == TaskType.GENERAL_CHAT:
            return await self._execute_general_chat(task, permission_context)
        else:
            raise ValueError(f"不支持的任务类型: {task.task_type}")
    
    async def _execute_tool_call(
        self, 
        task: TaskNode, 
        permission_context: PermissionContext = None
    ) -> Dict[str, Any]:
        """
        执行工具调用任务
        
        Args:
            task: 工具调用任务
            permission_context: 权限上下文
            
        Returns:
            Dict[str, Any]: 工具执行结果
        """
        if not task.tool_name:
            raise ValueError("工具调用任务必须指定工具名称")
        
        # 获取工具定义和处理器
        tool_def = self.tool_registry.get_tool(task.tool_name)
        if not tool_def:
            raise ValueError(f"工具不存在: {task.tool_name}")
        handler = self.tool_registry.get_handler(task.tool_name)
        if not handler:
            raise ValueError(f"工具处理器不存在: {task.tool_name}")
        
        # 处理参数中的依赖结果注入
        processed_params = self._inject_dependency_results(task.parameters)
        
        # 权限验证
        if self.permission_validator and permission_context:
            # 根据工具类型进行权限验证
            if task.tool_name in ['query_timesheet', 'query_project']:
                # 数据查询工具需要验证数据访问权限
                user_id = processed_params.get('userId') or processed_params.get('user_id')
                member_name = processed_params.get('member_name')
                project_id = processed_params.get('projectId') or processed_params.get('project_id')

                # member_name 参数表示查他人工时，员工角色无此权限
                if member_name and permission_context.entity_type in ('employee', 'deptSubAdmin'):
                    logger.warning(f"权限拒绝: member_name={member_name}, entity_type={permission_context.entity_type}")
                    raise PermissionError(f"抱歉，您的角色（{permission_context.entity_type}）没有权限查询他人（{member_name}）的工时记录")

                if user_id:
                    result = self.permission_validator.can_access_user_data(permission_context, user_id)
                    if not result.allowed:
                        logger.warning(f"权限拒绝: user_id={user_id}, reason={result.reason}")
                        raise PermissionError("您没有权限查询该用户的数据")

                if project_id:
                    result = self.permission_validator.can_access_project_data(permission_context, project_id)
                    if not result.allowed:
                        logger.warning(f"权限拒绝: project_id={project_id}, reason={result.reason}")
                        raise PermissionError("您没有权限访问该项目的数据")

            elif task.tool_name == 'compute_statistics':
                # 统计工具需要验证统计范围权限
                filters = processed_params.get('filters', {})
                user_ids = filters.get('user_ids', [])
                project_ids = filters.get('project_ids', [])
                department_ids = filters.get('department_ids', [])

                for user_id in user_ids:
                    result = self.permission_validator.can_access_user_data(permission_context, user_id)
                    if not result.allowed:
                        raise PermissionError(f"无权限访问用户 {user_id} 的统计数据：{result.reason}")

                for project_id in project_ids:
                    result = self.permission_validator.can_access_project_data(permission_context, project_id)
                    if not result.allowed:
                        raise PermissionError(f"无权限访问项目 {project_id} 的统计数据：{result.reason}")

                for dept_id in department_ids:
                    result = self.permission_validator.can_access_department_data(permission_context, dept_id)
                    if not result.allowed:
                        raise PermissionError(f"无权限访问部门 {dept_id} 的统计数据：{result.reason}")

            elif task.tool_name in ['generate_weekly_report', 'save_workhour']:
                # Phase 8 工具：只允许访问自己的数据，管理员除外
                target_user_id = processed_params.get('user_id')
                if target_user_id:
                    result = self.permission_validator.can_access_user_data(permission_context, target_user_id)
                    if not result.allowed:
                        raise PermissionError(f"无权限操作用户 {target_user_id} 的工时数据：{result.reason}")

            elif task.tool_name == 'approve_workhour':
                # 工时审核权限：以下两类用户可调用：
                #   A. 部门管理员及以上角色（deptAdmin / regionAdmin / companyAdmin / superAdmin）
                #   B. 项目负责人（entity_type 为 employee，但 managed_projects 非空）
                #      ——具体到某条工时是否属于其负责项目，由后端 SpringBoot 二次校验（会返回 403）
                ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
                is_admin = permission_context.entity_type in ADMIN_ROLES
                is_project_manager = bool(permission_context.managed_projects)
                if not is_admin and not is_project_manager:
                    raise PermissionError(
                        "工时审核权限不足：需要部门管理员角色，或担任至少一个项目的项目负责人"
                    )

            elif task.tool_name == 'export_report':
                # 导出报表权限：仅部门管理员及以上角色可调用
                ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
                if permission_context.entity_type not in ADMIN_ROLES:
                    raise PermissionError(
                        "权限不足：导出报表需要部门管理员及以上角色"
                    )

        # 执行工具
        logger.info(f"执行工具: {task.tool_name}, 参数: {processed_params}")

        from app.core.metrics import TOOL_CALL_COUNT, TOOL_CALL_LATENCY
        import time as _time_module
        _tool_start = _time_module.monotonic()
        start_time = datetime.now()  # 被后续 except 块引用，保留

        _call_status = "success"
        try:
            # 使用asyncio.wait_for确保工具执行也有超时控制
            import asyncio as _asyncio
            import inspect

            # 注入用户认证信息（所有调用 SpringBoot 的工具都需要）
            exec_params = dict(processed_params)
            if permission_context:
                if permission_context.auth_token:
                    exec_params["auth_token"] = permission_context.auth_token

                # 为需要 permission_context 的工具注入上下文
                if task.tool_name in ("sql_query", "batch_save_workhour", "suggest_workhour"):
                    exec_params["context"] = {
                        "user_id": permission_context.user_id,
                        "entity_type": permission_context.entity_type,
                        "department_id": permission_context.department_id,
                    }

            if inspect.iscoroutinefunction(handler):
                coro = handler(**exec_params)
            else:
                coro = _asyncio.get_event_loop().run_in_executor(
                    None, lambda: handler(**exec_params)
                )
            result = await asyncio.wait_for(coro, timeout=task.timeout)

            # 检查业务层失败（handler 返回了 success=False 但未抛异常）
            if isinstance(result, dict) and result.get("success") is False:
                _call_status = "error"

            execution_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"工具执行完成: {task.tool_name}, 耗时: {execution_time:.2f}秒")

            return {
                "tool_name": task.tool_name,
                "parameters": processed_params,
                "result": result,
                "execution_time": execution_time,
                "success": _call_status == "success"
            }
        except asyncio.TimeoutError:
            _call_status = "error"
            execution_time = (datetime.now() - start_time).total_seconds()
            error_msg = f"工具执行超时: {task.tool_name} (超时时间: {task.timeout}秒)"
            logger.error(error_msg)
            raise TimeoutError(error_msg)
        except PermissionError:
            _call_status = "permission_denied"
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"工具执行权限拒绝: {task.tool_name}, 耗时: {execution_time:.2f}秒")
            raise
        except Exception as e:
            _call_status = "error"
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"工具执行失败: {task.tool_name}, 耗时: {execution_time:.2f}秒, 错误: {e}")
            raise
        finally:
            TOOL_CALL_COUNT.labels(tool_name=task.tool_name, status=_call_status).inc()
            TOOL_CALL_LATENCY.labels(tool_name=task.tool_name).observe(
                _time_module.monotonic() - _tool_start
            )
    
    async def _execute_general_chat(
        self, 
        task: TaskNode, 
        permission_context: PermissionContext = None
    ) -> Dict[str, Any]:
        """
        执行通用聊天任务
        
        Args:
            task: 通用聊天任务
            permission_context: 权限上下文
            
        Returns:
            Dict[str, Any]: 聊天结果
        """
        if not self.llm_client:
            raise ValueError("通用聊天任务需要LLM客户端")
        
        # 处理参数中的依赖结果注入
        processed_params = self._inject_dependency_results(task.parameters)
        message = processed_params.get("message", "")
        
        if not message:
            raise ValueError("通用聊天任务必须包含消息内容")
        
        logger.info(f"执行通用聊天: {message[:100]}...")
        
        try:
            response = await self.llm_client.generate(
                prompt=message,
                max_tokens=1000,
                temperature=0.7
            )
            
            return {
                "message": message,
                "response": response,
                "success": True
            }
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            raise
    
    def _inject_dependency_results(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        注入依赖任务的执行结果到参数中
        
        Args:
            parameters: 原始参数
            
        Returns:
            Dict[str, Any]: 处理后的参数
        """
        if not parameters:
            return {}
        
        processed_params = {}
        
        for key, value in parameters.items():
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                # 检查是否是依赖结果引用，格式：{task_id.result.field}
                if ".result." in value:
                    try:
                        # 解析引用路径
                        ref_path = value[1:-1]  # 去掉大括号
                        parts = ref_path.split(".")
                        
                        if len(parts) >= 3 and parts[1] == "result":
                            task_id = parts[0]
                            field_path = parts[1:]  # 包含 "result" 作为起始路径

                            # 获取依赖任务的结果
                            if task_id in self.execution_results:
                                result = self.execution_results[task_id]
                                
                                # 按路径提取字段值
                                field_value = result
                                for field in field_path:
                                    if isinstance(field_value, dict) and field in field_value:
                                        field_value = field_value[field]
                                    else:
                                        field_value = None
                                        break
                                
                                if field_value is not None:
                                    processed_params[key] = field_value
                                else:
                                    logger.warning(f"无法从任务 {task_id} 结果中提取字段: {'.'.join(field_path)}")
                                    processed_params[key] = value
                            else:
                                logger.warning(f"依赖任务 {task_id} 的结果不存在")
                                processed_params[key] = value
                        else:
                            processed_params[key] = value
                    except Exception as e:
                        logger.warning(f"解析依赖结果引用失败: {value}, 错误: {e}")
                        processed_params[key] = value
                else:
                    processed_params[key] = value
            else:
                processed_params[key] = value
        
        return processed_params
    
    def _build_execution_summary(
        self, 
        task_plan: TaskPlan, 
        error: str = None
    ) -> Dict[str, Any]:
        """
        构建执行结果摘要
        
        Args:
            task_plan: 任务计划
            error: 错误信息
            
        Returns:
            Dict[str, Any]: 执行摘要
        """
        progress = task_plan.get_progress()
        
        summary = {
            "plan_id": task_plan.plan_id,
            "plan_name": task_plan.name,
            "status": task_plan.status,
            "progress": progress,
            "execution_time": task_plan.total_execution_time,
            "task_results": self.execution_results.copy(),
            "success": task_plan.status == TaskStatus.COMPLETED
        }
        
        if error:
            summary["error"] = error
        
        return summary
    
    def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定任务的执行结果
        
        Args:
            task_id: 任务ID
            
        Returns:
            Optional[Dict[str, Any]]: 任务执行结果
        """
        return self.execution_results.get(task_id)
    
    def clear_results(self):
        """清除执行结果缓存"""
        self.execution_results.clear()