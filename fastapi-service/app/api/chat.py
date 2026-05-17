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

from ..services.intent_router import IntentRouter, RouteTarget
from ..services.task_executor import TaskExecutor
from ..services.tool_registry import ToolRegistry
from ..services.permission_validator import PermissionValidator, PermissionContext
from ..services.llm_client import get_planner_llm_client
from ..models.task_plan import PlannerAgent
from ..services.langgraph_agent import initialize_agent, stream_agent_response


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI Chat"])


class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str = Field(..., description="用户消息", min_length=1, max_length=2000)
    session_id: Optional[str] = Field(None, description="会话ID")
    stream: bool = Field(default=True, description="是否使用流式响应")
    user_context: Optional[Dict[str, Any]] = Field(None, description="用户上下文")


def _resolve_user_identity(request: ChatRequest, http_request: Request) -> tuple[str, Optional[str], Optional[str], str, Dict[str, Any]]:
    """
    解析用户身份信息。

    优先级：body.user_context > header > anonymous fallback

    Returns:
        (user_id, entity_type, department_id, auth_token, user_context)
    """
    user_context = request.user_context or {}

    body_user_id = user_context.get("user_id")
    body_entity_type = user_context.get("entity_type")
    body_department_id = user_context.get("department_id")
    body_auth_token = user_context.get("auth_token")

    header_user_id = http_request.headers.get("X-User-ID")
    header_entity_type = http_request.headers.get("X-Entity-Type")
    header_department_id = http_request.headers.get("X-Department-ID")
    header_auth_token = http_request.headers.get("Authorization", "")

    user_id = body_user_id or header_user_id
    entity_type = body_entity_type or header_entity_type
    department_id = body_department_id or header_department_id
    auth_token = body_auth_token or header_auth_token

    if user_id:
        user_context["user_id"] = user_id
    else:
        user_id = "anonymous"
        user_context["user_id"] = user_id
        logger.warning(
            f"user_id fallback to anonymous, "
            f"session_id={request.session_id}, "
            f"body.user_id={body_user_id}, header.X-User-ID={header_user_id}"
        )

    if entity_type:
        user_context["entity_type"] = entity_type
    if department_id:
        user_context["department_id"] = department_id
    if auth_token:
        user_context["auth_token"] = auth_token

    return user_id, entity_type, department_id, auth_token, user_context


class ChatResponse(BaseModel):
    """聊天响应模型"""
    success: bool = Field(..., description="是否成功")
    message: Optional[str] = Field(None, description="响应消息")
    session_id: Optional[str] = Field(None, description="会话ID")
    result: Optional[Dict[str, Any]] = Field(None, description="执行结果")
    error: Optional[str] = Field(None, description="错误信息")


def _accumulate_response_text(prev: Optional[str], data: Dict[str, Any]) -> Optional[str]:
    """非流式聚合 response 事件文本。

    SSE `response` 事件按流式约定为增量分片：流式 RAG 路径会发多个 chunk
    事件 + 末尾来源事件，必须累积而非覆盖（历史 bug：用 `=` 覆盖导致只剩
    最后一个事件 = 来源 footer，答案体丢失）。单事件全量路径（工具/LLM/
    澄清/计划）只发一个 response 事件，累积后等价于自身，不会重复。
    """
    chunk_msg = data.get("message") or data.get("result", {}).get("response", "")
    if not chunk_msg:
        return prev
    return (prev or "") + chunk_msg


# 全局组件实例（在应用启动时初始化）
intent_router: Optional[IntentRouter] = None
task_executor: Optional[TaskExecutor] = None
tool_registry: Optional[ToolRegistry] = None
permission_validator: Optional[PermissionValidator] = None
planner_agent: Optional[PlannerAgent] = None


def initialize_chat_components(
    tool_reg: ToolRegistry,
    perm_validator: PermissionValidator,
    llm_client=None,
    prompt_builder=None,
):
    """
    初始化聊天组件
    
    Args:
        tool_reg: 工具注册中心
        perm_validator: 权限验证器
        llm_client: LLM客户端
    """
    global intent_router, task_executor, tool_registry, permission_validator, planner_agent
    
    # 设置全局组件
    tool_registry = tool_reg
    permission_validator = perm_validator
    
    # 初始化意图路由器
    intent_router = IntentRouter()
    intent_router.set_tool_registry(tool_registry)
    intent_router.set_llm_client(llm_client)
    
    # 初始化任务规划代理
    planner_agent = PlannerAgent(tool_registry=tool_registry, llm_client=get_planner_llm_client())
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

    # 注册工具执行器路由处理器
    async def tool_executor_handler(params):
        """工具执行路由处理器"""
        from app.models.task_plan import TaskNode, TaskType
        import uuid
        task = TaskNode(
            task_id=f"direct_{uuid.uuid4().hex[:8]}",
            task_type=TaskType.TOOL_CALL,
            tool_name=params.get("tool_name"),
            parameters=params.get("tool_parameters", {}),
            description=f"执行工具: {params.get('tool_name')}"
        )
        result = await task_executor.execute_single_task(
            task, params.get("permission_context")
        )
        return result

    intent_router.register_route_handler(RouteTarget.TOOL_EXECUTOR, tool_executor_handler)
    logger.info("✅ TOOL_EXECUTOR route handler registered")

    # 注册RAG引擎路由处理器（LangChain 混合检索）
    async def rag_engine_handler(params):
        """RAG知识库查询路由处理器"""
        from app.services.langchain_rag import langchain_rag_query
        return await langchain_rag_query(question=params.get("query", ""))

    intent_router.register_route_handler(RouteTarget.RAG_ENGINE, rag_engine_handler)
    logger.info("✅ RAG_ENGINE route handler registered")

    # 初始化 LangGraph Agent（流式端点使用）
    initialize_agent(
        intent_router=intent_router,
        tool_registry=tool_registry,
        task_executor=task_executor,
        llm_client=llm_client,
        prompt_builder=prompt_builder,
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
    if not intent_router:
        raise HTTPException(status_code=500, detail="AI Chat components not initialized")

    from app.core.metrics import ACTIVE_REQUESTS, REQUEST_COUNT, REQUEST_LATENCY
    import time

    ACTIVE_REQUESTS.inc()
    _intent = "unknown"

    try:
        # 解析用户身份信息（body 优先，header 兜底）
        user_id, entity_type, department_id, auth_token, user_context = _resolve_user_identity(
            request, http_request
        )

        # 构建权限上下文
        if user_id and entity_type:
            permission_context = PermissionContext(
                user_id=user_id,
                entity_type=entity_type,
                department_id=department_id,
                auth_token=auth_token
            )
            user_context["permission_context"] = permission_context

        logger.info(f"处理聊天请求: {request.message[:100]}...")
        
        # 生成流式响应（LangGraph Agent）
        async def generate_stream():
            nonlocal _intent
            _stream_start = time.monotonic()
            _stream_status = "success"
            try:
                async for event in stream_agent_response(
                    message=request.message,
                    user_context=user_context,
                    session_id=request.session_id,
                ):
                    # 提取 intent 标签
                    if "tool_call" in event:
                        _intent = "tool_execution"
                    elif "thinking" in event and "knowledge" in event.lower():
                        _intent = "knowledge_qa"
                    elif "response" in event:
                        if _intent == "unknown":
                            _intent = "general_chat"
                    yield event
            except Exception as e:
                _stream_status = "error"
                logger.error(f"流式响应生成异常: {e}", exc_info=True)
                import json
                yield (
                    f"event: error\n"
                    f"data: {json.dumps({'type': 'error', 'message': f'处理请求时发生错误: {str(e)}'}, ensure_ascii=False)}\n\n"
                )
            finally:
                # 埋点放在 generator 内部 finally，测的是真实流持续时间
                duration = time.monotonic() - _stream_start
                REQUEST_COUNT.labels(intent=_intent, status=_stream_status).inc()
                REQUEST_LATENCY.labels(intent=_intent).observe(duration)

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

    finally:
        # 外层只负责活跃请求计数，埋点已移到 generate_stream 内部
        ACTIVE_REQUESTS.dec()


@router.post("/chat")
async def chat_non_stream(request: ChatRequest, http_request: Request):
    """
    非流式AI聊天接口（使用 Function Calling 架构）

    Args:
        request: 聊天请求
        http_request: HTTP请求对象

    Returns:
        ChatResponse: 聊天响应
    """
    if not intent_router:
        raise HTTPException(status_code=500, detail="AI Chat components not initialized")

    from datetime import datetime
    from app.services.conversation_logger import get_conversation_logger

    start_time = datetime.now()
    status = "success"
    error_message = None
    route_type = None
    intent_value = None
    tool_name = None
    response_message = None

    try:
        # 解析用户身份信息（body 优先，header 兜底）
        user_id, entity_type, department_id, auth_token, user_context = _resolve_user_identity(
            request, http_request
        )

        # 构建权限上下文
        if user_id and entity_type:
            permission_context = PermissionContext(
                user_id=user_id,
                entity_type=entity_type,
                department_id=department_id,
                auth_token=auth_token
            )
            user_context["permission_context"] = permission_context

        logger.info(f"处理非流式聊天请求: {request.message[:100]}...")

        # 使用 LangGraph Agent 处理请求（收集流式输出）
        collected_events = []
        final_response = None
        effective_session_id = request.session_id
        detected_intent = None
        detected_tool = None
        tool_result = None

        async for event_str in stream_agent_response(
            message=request.message,
            user_context=user_context,
            session_id=request.session_id,
        ):
            # 解析 SSE 事件
            if event_str.startswith("event:"):
                lines = event_str.strip().split("\n")
                event_type = lines[0].replace("event:", "").strip()
                if len(lines) > 1 and lines[1].startswith("data:"):
                    data_str = lines[1].replace("data:", "").strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = {}

                    collected_events.append({"event": event_type, "data": data})

                    # 提取关键信息
                    if event_type in ("start", "done") and data.get("session_id"):
                        effective_session_id = data.get("session_id")
                    elif event_type == "tool_call":
                        detected_tool = data.get("tool_name")
                        tool_name = detected_tool
                    elif event_type == "response":
                        final_response = _accumulate_response_text(final_response, data)
                        if data.get("result"):
                            tool_result = data.get("result")
                    elif event_type == "error":
                        final_response = data.get("message", "处理请求时发生错误")
                        status = "error"
                        error_message = final_response

        # 根据事件推断意图类型
        for event in collected_events:
            if event["event"] == "tool_call":
                detected_intent = "tool_execution"
                route_type = "tool_executor"
                break
            elif event["event"] == "thinking":
                msg = event["data"].get("message", "")
                if "搜索知识库" in msg:
                    detected_intent = "knowledge_qa"
                    route_type = "rag_engine"
                    break

        if not detected_intent:
            detected_intent = "general_chat"
            route_type = "llm_service"

        intent_value = detected_intent

        # 构建与旧格式兼容的 result
        response_text = final_response or "请求处理完成"
        result = {
            "route_info": {
                "target": route_type,
                "intent_type": detected_intent,
                "confidence": 0.9,  # Function Calling 通常有高置信度
            },
            # 兼容不同前端取值路径：旧前端取 result.message，新前端常取
            # result.response/content/text。四个字段保持同一用户可见文本。
            "message": response_text,
            "response": response_text,
            "content": response_text,
            "text": response_text,
            "data": {
                "message": response_text,
                "response": response_text,
                "content": response_text,
                "text": response_text,
            },
        }

        if detected_tool:
            result["tool_name"] = detected_tool
        if tool_result:
            result["result"] = tool_result

        response = ChatResponse(
            success=status != "error",
            message=response_text,
            session_id=effective_session_id,
            result=result,
            error=error_message if status == "error" else None,
        )

        return response

    except Exception as e:
        logger.error(f"非流式聊天接口异常: {e}", exc_info=True)
        status = "error"
        error_message = str(e)

        return ChatResponse(
            success=False,
            session_id=request.session_id,
            error=f"处理聊天请求失败: {str(e)}"
        )

    finally:
        # 记录会话日志 + Prometheus 指标
        try:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            from app.models.conversation import ConversationLogEntry
            conv_logger = get_conversation_logger()

            conv_logger.log_conversation(
                ConversationLogEntry(
                    session_id=(effective_session_id if 'effective_session_id' in locals() else request.session_id) or "unknown",
                    user_id=user_id if 'user_id' in locals() else "anonymous",
                    user_message=request.message,
                    route_type=route_type or "unknown",
                    intent=intent_value,
                    duration_ms=duration_ms,
                    status=status,
                    error_message=error_message
                )
            )
        except Exception as log_error:
            logger.error(f"Failed to log conversation: {log_error}")

        # Prometheus 指标
        try:
            from app.core.metrics import REQUEST_COUNT, REQUEST_LATENCY
            import time as _time_module
            duration_sec = (datetime.now() - start_time).total_seconds()
            REQUEST_COUNT.labels(intent=intent_value or "unknown", status=status).inc()
            REQUEST_LATENCY.labels(intent=intent_value or "unknown").observe(duration_sec)
        except Exception as metric_err:
            logger.warning(f"Failed to record metrics: {metric_err}")


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
