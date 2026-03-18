"""
AI Chat API - AI聊天接口

提供流式AI聊天服务，支持意图识别、工具调用、任务规划等功能。
"""

from datetime import datetime
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import json

from ..services.stream_response import StreamResponseGenerator
from ..services.intent_router import IntentRouter, RouteTarget
from ..services.task_executor import TaskExecutor
from ..services.tool_registry import ToolRegistry
from ..services.permission_validator import PermissionValidator, PermissionContext
from ..models.task_plan import PlannerAgent


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI Chat"])


class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str = Field(..., description="用户消息", min_length=1, max_length=2000)
    session_id: Optional[str] = Field(None, description="会话ID")
    stream: bool = Field(default=True, description="是否使用流式响应")
    user_context: Optional[Dict[str, Any]] = Field(None, description="用户上下文")


class ChatResponse(BaseModel):
    """聊天响应模型"""
    success: bool = Field(..., description="是否成功")
    message: Optional[str] = Field(None, description="响应消息")
    session_id: Optional[str] = Field(None, description="会话ID")
    result: Optional[Dict[str, Any]] = Field(None, description="执行结果")
    error: Optional[str] = Field(None, description="错误信息")


# 全局组件实例（在应用启动时初始化）
stream_generator: Optional[StreamResponseGenerator] = None
intent_router: Optional[IntentRouter] = None
task_executor: Optional[TaskExecutor] = None
tool_registry: Optional[ToolRegistry] = None
permission_validator: Optional[PermissionValidator] = None
planner_agent: Optional[PlannerAgent] = None


def initialize_chat_components(
    tool_reg: ToolRegistry,
    perm_validator: PermissionValidator,
    llm_client=None
):
    """
    初始化聊天组件
    
    Args:
        tool_reg: 工具注册中心
        perm_validator: 权限验证器
        llm_client: LLM客户端
    """
    global stream_generator, intent_router, task_executor, tool_registry, permission_validator, planner_agent
    
    # 设置全局组件
    tool_registry = tool_reg
    permission_validator = perm_validator
    
    # 初始化意图路由器
    intent_router = IntentRouter()
    intent_router.set_tool_registry(tool_registry)
    intent_router.set_llm_client(llm_client)
    
    # 初始化任务规划代理
    planner_agent = PlannerAgent(tool_registry=tool_registry, llm_client=llm_client)
    intent_router.set_planner_agent(planner_agent)
    
    # 初始化任务执行器
    task_executor = TaskExecutor(
        tool_registry=tool_registry,
        permission_validator=permission_validator,
        llm_client=llm_client
    )
    intent_router.set_task_executor(task_executor)
    
    # 注册路由处理器
    if llm_client:
        async def llm_service_handler(params):
            """LLM 通用对话处理器"""
            message = params.get("message", "")
            system_prompt = (
                "你是一个专业的企业工时管理助手。"
                "请用简洁、友好的方式回答用户问题。"
                "如果涉及工时管理相关功能，可以引导用户使用对应的功能。"
            )
            response = await llm_client.generate(
                prompt=message,
                system_prompt=system_prompt,
                temperature=params.get("temperature", 0.7),
                max_tokens=params.get("max_tokens", 1000),
            )
            return {"success": True, "message": response}

        intent_router.register_route_handler(RouteTarget.LLM_SERVICE, llm_service_handler)
        logger.info("✅ LLM_SERVICE route handler registered")
    
    # 初始化流式响应生成器
    stream_generator = StreamResponseGenerator(
        intent_router=intent_router,
        task_executor=task_executor,
        llm_client=llm_client
    )
    
    logger.info("AI Chat components initialized")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request):
    """
    流式AI聊天接口
    
    Args:
        request: 聊天请求
        http_request: HTTP请求对象
        
    Returns:
        StreamingResponse: SSE流式响应
    """
    if not stream_generator:
        raise HTTPException(status_code=500, detail="AI Chat components not initialized")
    
    try:
        # 构建用户上下文
        user_context = request.user_context or {}
        
        # 从请求头中提取用户信息（如果有JWT等认证信息）
        # 这里可以根据实际的认证方式来提取用户信息
        user_id = http_request.headers.get("X-User-ID")
        entity_type = http_request.headers.get("X-Entity-Type")
        department_id = http_request.headers.get("X-Department-ID")
        
        if user_id:
            user_context["user_id"] = user_id
        if entity_type:
            user_context["entity_type"] = entity_type
        if department_id:
            user_context["department_id"] = department_id
        
        # 构建权限上下文
        if user_id and entity_type:
            permission_context = PermissionContext(
                user_id=user_id,
                entity_type=entity_type,
                department_id=department_id
            )
            user_context["permission_context"] = permission_context
        
        logger.info(f"处理聊天请求: {request.message[:100]}...")
        
        # 生成流式响应
        async def generate_stream():
            try:
                async for event in stream_generator.stream_response(
                    message=request.message,
                    user_context=user_context,
                    session_id=request.session_id
                ):
                    yield event
            except Exception as e:
                logger.error(f"流式响应生成异常: {e}", exc_info=True)
                # 发送错误事件
                error_event = stream_generator.format_sse_event(
                    "error",
                    {"message": f"处理请求时发生错误: {str(e)}"}
                )
                yield error_event
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            }
        )
        
    except Exception as e:
        logger.error(f"聊天接口异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"处理聊天请求失败: {str(e)}")


@router.post("/chat")
async def chat_non_stream(request: ChatRequest, http_request: Request):
    """
    非流式AI聊天接口
    
    Args:
        request: 聊天请求
        http_request: HTTP请求对象
        
    Returns:
        ChatResponse: 聊天响应
    """
    if not intent_router:
        raise HTTPException(status_code=500, detail="AI Chat components not initialized")
    
    try:
        # 构建用户上下文
        user_context = request.user_context or {}
        
        # 从请求头中提取用户信息
        user_id = http_request.headers.get("X-User-ID")
        entity_type = http_request.headers.get("X-Entity-Type")
        department_id = http_request.headers.get("X-Department-ID")
        
        if user_id:
            user_context["user_id"] = user_id
        if entity_type:
            user_context["entity_type"] = entity_type
        if department_id:
            user_context["department_id"] = department_id
        
        # 构建权限上下文
        if user_id and entity_type:
            permission_context = PermissionContext(
                user_id=user_id,
                entity_type=entity_type,
                department_id=department_id
            )
            user_context["permission_context"] = permission_context
        
        logger.info(f"处理非流式聊天请求: {request.message[:100]}...")
        
        # 意图识别和路由决策
        route_decision = await intent_router.make_route_decision(
            request.message, user_context
        )
        
        # 执行路由
        result = await intent_router.execute_route(route_decision, user_context)
        
        return ChatResponse(
            success=True,
            message="请求处理完成",
            session_id=request.session_id,
            result=result
        )
        
    except Exception as e:
        logger.error(f"非流式聊天接口异常: {e}", exc_info=True)
        return ChatResponse(
            success=False,
            session_id=request.session_id,
            error=f"处理聊天请求失败: {str(e)}"
        )


@router.get("/health")
async def health_check():
    """
    健康检查接口
    
    Returns:
        Dict: 健康状态
    """
    try:
        # 检查各组件状态
        components_status = {
            "stream_generator": stream_generator is not None,
            "intent_router": intent_router is not None,
            "task_executor": task_executor is not None,
            "tool_registry": tool_registry is not None,
            "permission_validator": permission_validator is not None,
            "planner_agent": planner_agent is not None
        }
        
        all_healthy = all(components_status.values())
        
        return {
            "status": "healthy" if all_healthy else "unhealthy",
            "components": components_status,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"健康检查异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"健康检查失败: {str(e)}")


@router.get("/status")
async def get_status():
    """
    获取AI服务状态
    
    Returns:
        Dict: 服务状态信息
    """
    try:
        status_info = {
            "service": "AI Chat Service",
            "version": "1.0.0",
            "components": {
                "intent_router": "active" if intent_router else "inactive",
                "task_executor": "active" if task_executor else "inactive",
                "tool_registry": f"{len(tool_registry.list_tools())} tools" if tool_registry else "inactive",
                "permission_validator": "active" if permission_validator else "inactive"
            }
        }
        
        return status_info
        
    except Exception as e:
        logger.error(f"状态查询异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"状态查询失败: {str(e)}")