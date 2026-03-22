"""
LangGraph Agent 编排层

将意图分类 → 工具执行 / RAG 查询 / LLM 对话 封装为 LangGraph StateGraph，
实现清晰的状态驱动流程、条件路由和 SSE 流式事件输出。

节点拓扑：
  START
    └─ classify_intent ──(条件路由)──┬─ execute_tool ─→ END
                                    ├─ execute_rag  ─→ END
                                    └─ execute_llm  ─→ END
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

# ─── 全局组件（由 initialize_agent 在应用启动时注入）──────────────────────────
_tool_registry = None
_task_executor = None
_llm_client = None
_intent_router = None
_graph = None  # 编译后的 CompiledGraph


def initialize_agent(
    intent_router,
    tool_registry,
    task_executor,
    llm_client,
) -> None:
    """注入运行时组件并编译 LangGraph 图"""
    global _tool_registry, _task_executor, _llm_client, _intent_router, _graph
    _tool_registry = tool_registry
    _task_executor = task_executor
    _llm_client = llm_client
    _intent_router = intent_router
    _graph = _build_graph()
    logger.info("✅ LangGraph Agent 初始化完成")


# ─── State Schema ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """LangGraph 全局状态，在节点间流转"""
    # 输入
    user_message: str
    user_context: Dict[str, Any]
    session_id: Optional[str]
    # 分类结果
    intent: Optional[str]          # knowledge_qa | tool_execution | complex_request | general_chat
    tool_name: Optional[str]
    tool_params: Dict[str, Any]
    query: str                     # RAG / 工具查询用
    # 执行结果（仅一个非 None）
    tool_result: Optional[Dict[str, Any]]
    rag_result: Optional[Dict[str, Any]]
    llm_result: Optional[str]
    error: Optional[str]


# ─── 节点函数 ─────────────────────────────────────────────────────────────────

async def node_classify_intent(state: AgentState) -> dict:
    """
    节点：意图分类

    复用 IntentRouter（LLM 主路径 + 规则降级），提取：
    - intent: 意图类型
    - tool_name: 工具名（tool_execution 时）
    - tool_params: 工具参数（已注入 user_id/auth_token）
    - query: 问题字符串
    """
    from app.services.intent_router import IntentType

    router = _intent_router
    if not router:
        return {"intent": "general_chat", "tool_name": None, "tool_params": {}, "query": state["user_message"]}

    try:
        intent_result = await router.route_intent(
            state["user_message"],
            state.get("user_context"),
        )
    except Exception as e:
        logger.error(f"意图分类节点异常: {e}")
        return {"intent": "general_chat", "tool_name": None, "tool_params": {}, "query": state["user_message"], "error": str(e)}

    user_ctx = state.get("user_context") or {}
    tool_params: Dict[str, Any] = {}

    if intent_result.intent_type == IntentType.TOOL_EXECUTION:
        tool_name = intent_result.parameters.get("tool_name")

        # 提取工具参数（排除路由元数据字段）
        tool_params = {
            k: v for k, v in intent_result.parameters.items()
            if k not in ("tool_name", "matched_keywords", "llm_classified", "query")
        }

        # 注入用户身份（工时查询必须）
        if user_ctx.get("user_id") and "user_id" not in tool_params:
            tool_params["user_id"] = user_ctx["user_id"]
        if user_ctx.get("auth_token"):
            tool_params["auth_token"] = user_ctx["auth_token"]

        # 用 LLM 精细化提取参数（如日期范围）
        if _intent_router and _llm_client and tool_name:
            try:
                llm_params = await _intent_router._extract_parameters_with_llm(
                    state["user_message"], tool_name
                )
                tool_params.update(llm_params)
            except Exception as e:
                logger.warning(f"LLM 参数提取失败，使用已有参数: {e}")

        return {
            "intent": intent_result.intent_type.value,
            "tool_name": tool_name,
            "tool_params": tool_params,
            "query": intent_result.parameters.get("query", state["user_message"]),
        }

    return {
        "intent": intent_result.intent_type.value,
        "tool_name": None,
        "tool_params": {},
        "query": intent_result.parameters.get("query", state["user_message"]),
    }


async def node_execute_tool(state: AgentState) -> dict:
    """节点：工具执行（query_timesheet / query_project / compute_statistics）"""
    if not _task_executor:
        return {"error": "TaskExecutor 未初始化", "tool_result": {"success": False}}

    from app.models.task_plan import TaskNode, TaskType
    import uuid

    task = TaskNode(
        task_id=f"lg_{uuid.uuid4().hex[:8]}",
        task_type=TaskType.TOOL_CALL,
        tool_name=state.get("tool_name"),
        parameters=state.get("tool_params") or {},
        description=f"执行工具: {state.get('tool_name')}",
    )

    user_ctx = state.get("user_context") or {}
    permission_ctx = user_ctx.get("permission_context")

    try:
        result = await _task_executor.execute_single_task(task, permission_ctx)
        return {"tool_result": result}
    except Exception as e:
        logger.error(f"工具执行节点异常: {e}")
        return {"tool_result": {"success": False, "error": str(e)}, "error": str(e)}


async def node_execute_rag(state: AgentState) -> dict:
    """节点：LangChain RAG 知识库查询（混合检索 + LLM 生成）"""
    from app.services.langchain_rag import langchain_rag_query

    try:
        result = await langchain_rag_query(state.get("query") or state["user_message"])
        return {"rag_result": result}
    except Exception as e:
        logger.error(f"RAG 节点异常: {e}")
        return {"rag_result": {"success": False, "error": str(e)}, "error": str(e)}


async def node_execute_llm(state: AgentState) -> dict:
    """节点：LLM 通用对话（问候 / 复杂请求降级 / 兜底）"""
    if not _llm_client:
        return {"llm_result": "LLM 服务未初始化", "error": "LLM not available"}

    system_prompt = (
        "你是一个专业的企业工时管理助手。"
        "请用简洁、友好的方式回答用户问题。"
        "如果涉及工时管理相关功能，可以引导用户使用对应的功能。"
    )

    try:
        answer = await _llm_client.generate(
            prompt=state["user_message"],
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=1000,
        )
        return {"llm_result": answer}
    except Exception as e:
        logger.error(f"LLM 对话节点异常: {e}")
        return {"llm_result": f"生成回答时出错: {e}", "error": str(e)}


# ─── 条件路由 ─────────────────────────────────────────────────────────────────

def _route_by_intent(state: AgentState) -> str:
    """根据意图分类结果选择下一节点"""
    intent = state.get("intent", "general_chat")
    return {
        "knowledge_qa": "execute_rag",
        "tool_execution": "execute_tool",
        "complex_request": "execute_llm",   # Phase 3: 改为独立规划节点
        "general_chat": "execute_llm",
    }.get(intent, "execute_llm")


# ─── 构建图 ───────────────────────────────────────────────────────────────────

def _build_graph():
    """构建并编译 LangGraph StateGraph"""
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("classify_intent", node_classify_intent)
    builder.add_node("execute_tool", node_execute_tool)
    builder.add_node("execute_rag", node_execute_rag)
    builder.add_node("execute_llm", node_execute_llm)

    # 入口
    builder.add_edge(START, "classify_intent")

    # 条件路由（classify_intent → 3 个执行节点之一）
    builder.add_conditional_edges(
        "classify_intent",
        _route_by_intent,
        {
            "execute_tool": "execute_tool",
            "execute_rag": "execute_rag",
            "execute_llm": "execute_llm",
        },
    )

    # 所有执行节点 → END
    builder.add_edge("execute_tool", END)
    builder.add_edge("execute_rag", END)
    builder.add_edge("execute_llm", END)

    return builder.compile()


# ─── SSE 流式输出 ─────────────────────────────────────────────────────────────

def _format_sse(event_type: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件字符串"""
    data.setdefault("timestamp", datetime.now().isoformat())
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def stream_agent_response(
    message: str,
    user_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    通过 LangGraph 图驱动整个对话流程，以 SSE 事件流的形式输出结果。

    SSE 事件序列：
        start → thinking → [tool_call?] → response → done
        或在出错时：start → thinking → error → done
    """
    if not _graph:
        yield _format_sse("error", {"message": "LangGraph Agent 未初始化"})
        return

    # 会话日志收集
    start_time = datetime.now()
    user_ctx = user_context or {}
    user_id = user_ctx.get("user_id", "anonymous")
    log_intent: Optional[str] = None
    log_route_type: Optional[str] = None
    log_status = "success"
    log_error: Optional[str] = None

    initial_state: AgentState = {
        "user_message": message,
        "user_context": user_ctx,
        "session_id": session_id,
        "intent": None,
        "tool_name": None,
        "tool_params": {},
        "query": message,
        "tool_result": None,
        "rag_result": None,
        "llm_result": None,
        "error": None,
    }

    yield _format_sse("start", {
        "message": "开始处理您的请求...",
        "session_id": session_id,
    })
    yield _format_sse("thinking", {"message": "正在分析您的请求意图..."})

    try:
        async for chunk in _graph.astream(initial_state):
            # chunk 是 {node_name: state_delta} 的字典
            for node_name, state_delta in chunk.items():
                if node_name == "classify_intent":
                    intent = state_delta.get("intent", "general_chat")
                    log_intent = intent
                    log_route_type = {
                        "knowledge_qa": "rag_engine",
                        "tool_execution": "tool_executor",
                        "complex_request": "llm_service",
                        "general_chat": "llm_service",
                    }.get(intent, "llm_service")

                    if intent == "tool_execution":
                        tool_name = state_delta.get("tool_name", "unknown")
                        yield _format_sse("tool_call", {
                            "tool_name": tool_name,
                            "message": f"正在调用工具: {tool_name}...",
                        })
                    elif intent == "knowledge_qa":
                        yield _format_sse("thinking", {"message": "正在搜索知识库..."})
                    else:
                        yield _format_sse("thinking", {"message": "正在生成回复..."})

                elif node_name == "execute_tool":
                    result = state_delta.get("tool_result")
                    error = state_delta.get("error")
                    if error or (result and not result.get("success", True)):
                        msg = error or result.get("error", "工具执行失败")
                        log_status = "error"
                        log_error = msg
                        yield _format_sse("error", {"message": msg})
                    else:
                        yield _format_sse("response", {
                            "result": result,
                            "tool_name": initial_state.get("tool_name"),
                        })

                elif node_name == "execute_rag":
                    result = state_delta.get("rag_result")
                    error = state_delta.get("error")
                    if error or (result and not result.get("success", True)):
                        err_msg = error or result.get("error", "RAG 查询失败")
                        log_status = "error"
                        log_error = err_msg
                        yield _format_sse("error", {"message": err_msg})
                    else:
                        yield _format_sse("response", {"result": result})

                elif node_name == "execute_llm":
                    llm_result = state_delta.get("llm_result")
                    error = state_delta.get("error")
                    if error and not llm_result:
                        log_status = "error"
                        log_error = error
                        yield _format_sse("error", {"message": error})
                    else:
                        yield _format_sse("response", {"message": llm_result or ""})

    except Exception as e:
        logger.error(f"LangGraph 流式执行异常: {e}", exc_info=True)
        log_status = "error"
        log_error = str(e)
        yield _format_sse("error", {"message": f"处理请求时发生错误: {e}"})

    finally:
        # 写会话日志（失败不影响主流程）
        try:
            from app.services.conversation_logger import get_conversation_logger
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            get_conversation_logger().log_conversation(
                session_id=session_id or "unknown",
                user_id=user_id,
                user_message=message,
                route_type=log_route_type or "unknown",
                intent=log_intent,
                duration_ms=duration_ms,
                status=log_status,
                error_message=log_error,
            )
        except Exception as log_err:
            logger.error(f"会话日志写入失败: {log_err}")

    yield _format_sse("done", {"message": "请求处理完成"})
