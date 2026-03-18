"""
Stream Response - 流式响应处理器

实现SSE（Server-Sent Events）流式响应生成，支持多种事件类型的流式输出。
"""

import json
import asyncio
import logging
from typing import AsyncGenerator, Dict, Any, Optional
from datetime import datetime
from enum import Enum

from .intent_router import IntentRouter
from .task_executor import TaskExecutor
from ..models.task_plan import TaskPlan, TaskStatus


logger = logging.getLogger(__name__)


class SSEEventType(str, Enum):
    """SSE事件类型枚举"""
    START = "start"              # 开始处理
    THINKING = "thinking"        # 思考中
    TOOL_CALL = "tool_call"      # 工具调用
    TASK_START = "task_start"    # 任务开始
    TASK_COMPLETE = "task_complete"  # 任务完成
    RESPONSE = "response"        # 响应内容
    DONE = "done"               # 处理完成
    ERROR = "error"             # 错误


class StreamResponseGenerator:
    """
    流式响应生成器
    """
    
    def __init__(
        self,
        intent_router: IntentRouter,
        task_executor: TaskExecutor = None,
        llm_client=None
    ):
        """
        初始化流式响应生成器
        
        Args:
            intent_router: 意图路由器
            task_executor: 任务执行器
            llm_client: LLM客户端
        """
        self.intent_router = intent_router
        self.task_executor = task_executor
        self.llm_client = llm_client
    
    async def stream_response(
        self,
        message: str,
        user_context: Dict[str, Any] = None,
        session_id: str = None
    ) -> AsyncGenerator[str, None]:
        """
        生成流式响应
        
        Args:
            message: 用户消息
            user_context: 用户上下文
            session_id: 会话ID
            
        Yields:
            str: SSE格式的事件数据
        """
        try:
            # 发送开始事件
            yield self.format_sse_event(
                SSEEventType.START,
                {
                    "message": "开始处理您的请求...",
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            # 发送思考事件
            yield self.format_sse_event(
                SSEEventType.THINKING,
                {"message": "正在分析您的请求意图..."}
            )
            
            # 意图识别和路由
            route_decision = await self.intent_router.route_intent(
                message, user_context
            )
            
            # 根据路由决策处理请求
            if route_decision.target.value == "tool_executor":
                async for event in self._handle_tool_execution(route_decision, user_context):
                    yield event
                    
            elif route_decision.target.value == "planner_agent":
                async for event in self._handle_task_planning(route_decision, user_context):
                    yield event
                    
            elif route_decision.target.value == "rag_engine":
                async for event in self._handle_rag_query(route_decision, user_context):
                    yield event
                    
            elif route_decision.target.value == "llm_service":
                async for event in self._handle_llm_chat(route_decision, user_context):
                    yield event
            else:
                yield self.format_sse_event(
                    SSEEventType.ERROR,
                    {"message": f"不支持的路由目标: {route_decision.target}"}
                )
                return
            
            # 发送完成事件
            yield self.format_sse_event(
                SSEEventType.DONE,
                {
                    "message": "请求处理完成",
                    "timestamp": datetime.now().isoformat()
                }
            )
            
        except Exception as e:
            logger.error(f"流式响应生成异常: {e}", exc_info=True)
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {
                    "message": f"处理请求时发生错误: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }
            )
    
    async def _handle_tool_execution(
        self,
        route_decision,
        user_context: Dict[str, Any] = None
    ) -> AsyncGenerator[str, None]:
        """
        处理工具执行流程
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Yields:
            str: SSE事件
        """
        # 从parameters中获取工具名
        tool_name = route_decision.intent_result.parameters.get("tool_name", "unknown")

        yield self.format_sse_event(
            SSEEventType.TOOL_CALL,
            {
                "message": f"准备调用工具: {tool_name}",
                "tool_name": tool_name,
                "parameters": route_decision.intent_result.parameters
            }
        )

        try:
            # 执行工具调用
            result = await self.intent_router.execute_route(route_decision, user_context)

            yield self.format_sse_event(
                SSEEventType.RESPONSE,
                {
                    "message": "工具执行完成",
                    "result": result,
                    "tool_name": tool_name
                }
            )

        except Exception as e:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {
                    "message": f"工具执行失败: {str(e)}",
                    "tool_name": tool_name
                }
            )
    
    async def _handle_task_planning(
        self,
        route_decision,
        user_context: Dict[str, Any] = None
    ) -> AsyncGenerator[str, None]:
        """
        处理任务规划和执行流程
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Yields:
            str: SSE事件
        """
        if not self.task_executor:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {"message": "任务执行器未配置"}
            )
            return
        
        yield self.format_sse_event(
            SSEEventType.THINKING,
            {"message": "正在制定任务执行计划..."}
        )
        
        try:
            # 执行路由获取任务计划
            task_plan = await self.intent_router.execute_route(route_decision, user_context)
            
            if not isinstance(task_plan, TaskPlan):
                yield self.format_sse_event(
                    SSEEventType.ERROR,
                    {"message": "任务规划失败，返回结果不是有效的任务计划"}
                )
                return
            
            yield self.format_sse_event(
                SSEEventType.RESPONSE,
                {
                    "message": f"任务计划已生成，包含 {len(task_plan.tasks)} 个任务",
                    "plan_name": task_plan.name,
                    "task_count": len(task_plan.tasks)
                }
            )
            
            # 执行任务计划
            async for event in self._execute_task_plan(task_plan, user_context):
                yield event
                
        except Exception as e:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {"message": f"任务规划失败: {str(e)}"}
            )
    
    async def _execute_task_plan(
        self,
        task_plan: TaskPlan,
        user_context: Dict[str, Any] = None
    ) -> AsyncGenerator[str, None]:
        """
        执行任务计划并生成流式事件
        
        Args:
            task_plan: 任务计划
            user_context: 用户上下文
            
        Yields:
            str: SSE事件
        """
        # 获取拓扑排序后的任务层级
        task_levels = task_plan.topological_sort()
        
        for level_index, level_tasks in enumerate(task_levels):
            yield self.format_sse_event(
                SSEEventType.THINKING,
                {
                    "message": f"开始执行第 {level_index + 1} 层任务",
                    "level": level_index + 1,
                    "task_count": len(level_tasks)
                }
            )
            
            # 为每个任务生成开始事件
            for task in level_tasks:
                yield self.format_sse_event(
                    SSEEventType.TASK_START,
                    {
                        "task_id": task.task_id,
                        "task_type": task.task_type,
                        "tool_name": task.tool_name,
                        "description": task.description
                    }
                )
            
            # 执行同层级任务（并行）
            await self.task_executor._execute_level_tasks(
                level_tasks,
                user_context.get('permission_context') if user_context else None
            )
            
            # 为每个任务生成完成事件
            for task in level_tasks:
                if task.status == TaskStatus.COMPLETED:
                    yield self.format_sse_event(
                        SSEEventType.TASK_COMPLETE,
                        {
                            "task_id": task.task_id,
                            "status": "completed",
                            "execution_time": task.execution_time,
                            "result": task.result
                        }
                    )
                elif task.status == TaskStatus.FAILED:
                    yield self.format_sse_event(
                        SSEEventType.ERROR,
                        {
                            "task_id": task.task_id,
                            "status": "failed",
                            "error": task.error
                        }
                    )
        
        # 任务计划执行完成
        task_plan.complete_execution()
        
        yield self.format_sse_event(
            SSEEventType.RESPONSE,
            {
                "message": "所有任务执行完成",
                "plan_summary": task_plan.to_summary()
            }
        )
    
    async def _handle_rag_query(
        self,
        route_decision,
        user_context: Dict[str, Any] = None
    ) -> AsyncGenerator[str, None]:
        """
        处理RAG知识库查询流程
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Yields:
            str: SSE事件
        """
        yield self.format_sse_event(
            SSEEventType.THINKING,
            {"message": "正在搜索知识库..."}
        )
        
        try:
            result = await self.intent_router.execute_route(route_decision, user_context)
            
            yield self.format_sse_event(
                SSEEventType.RESPONSE,
                {
                    "message": "知识库查询完成",
                    "result": result
                }
            )
            
        except Exception as e:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {"message": f"知识库查询失败: {str(e)}"}
            )
    
    async def _handle_llm_chat(
        self,
        route_decision,
        user_context: Dict[str, Any] = None
    ) -> AsyncGenerator[str, None]:
        """
        处理LLM通用对话流程
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Yields:
            str: SSE事件
        """
        if not self.llm_client:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {"message": "LLM客户端未配置"}
            )
            return
        
        yield self.format_sse_event(
            SSEEventType.THINKING,
            {"message": "正在生成回复..."}
        )
        
        try:
            # 检查LLM客户端是否支持流式响应
            if hasattr(self.llm_client, 'stream_generate'):
                # 流式生成
                async for chunk in self.llm_client.stream_generate(
                    prompt=route_decision.intent_result.message,
                    max_tokens=1000,
                    temperature=0.7
                ):
                    yield self.format_sse_event(
                        SSEEventType.RESPONSE,
                        {
                            "chunk": chunk,
                            "is_partial": True
                        }
                    )
            else:
                # 非流式生成
                result = await self.intent_router.execute_route(route_decision, user_context)
                
                yield self.format_sse_event(
                    SSEEventType.RESPONSE,
                    {
                        "message": result,
                        "is_partial": False
                    }
                )
                
        except Exception as e:
            yield self.format_sse_event(
                SSEEventType.ERROR,
                {"message": f"LLM对话失败: {str(e)}"}
            )
    
    def format_sse_event(
        self,
        event_type: SSEEventType,
        data: Dict[str, Any],
        event_id: Optional[str] = None
    ) -> str:
        """
        格式化SSE事件
        
        Args:
            event_type: 事件类型
            data: 事件数据
            event_id: 事件ID（可选）
            
        Returns:
            str: 格式化的SSE事件字符串
        """
        lines = []
        
        if event_id:
            lines.append(f"id: {event_id}")
        
        lines.append(f"event: {event_type.value}")
        
        # 添加时间戳
        if "timestamp" not in data:
            data["timestamp"] = datetime.now().isoformat()
        
        # 序列化数据
        json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        lines.append(f"data: {json_data}")
        
        # SSE格式要求以两个换行符结束
        lines.append("")
        lines.append("")
        
        return "\n".join(lines)