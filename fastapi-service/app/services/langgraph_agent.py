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
_prompt_builder = None  # Task 39: PromptBuilder


def initialize_agent(
    intent_router,
    tool_registry,
    task_executor,
    llm_client,
    prompt_builder=None,
) -> None:
    """注入运行时组件并编译 LangGraph 图"""
    global _tool_registry, _task_executor, _llm_client, _intent_router, _graph, _prompt_builder
    _tool_registry = tool_registry
    _task_executor = task_executor
    _llm_client = llm_client
    _intent_router = intent_router
    _prompt_builder = prompt_builder
    _graph = _build_graph()
    logger.info("✅ LangGraph Agent 初始化完成")


# ─── State Schema ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """LangGraph 全局状态，在节点间流转"""
    # 输入
    user_message: str
    user_context: Dict[str, Any]
    session_id: Optional[str]
    # 记忆上下文（Task 40）
    conversation_history: list     # OpenAI messages 格式的历史消息列表
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
    """节点：LLM 通用对话（问候 / 复杂请求降级 / 兜底）

    优先使用 conversation_history（带记忆的多轮对话），
    无历史时降级为单轮 prompt 模式。
    """
    if not _llm_client:
        return {"llm_result": "LLM 服务未初始化", "error": "LLM not available"}

    base_system_prompt = (
        "你是一个专业的企业工时管理助手。"
        "请用简洁、友好的方式回答用户问题。"
        "如果涉及工时管理相关功能，可以引导用户使用对应的功能。"
    )

    try:
        history = state.get("conversation_history") or []
        if history:
            # 带历史的多轮对话：history 已包含 system/user/assistant 消息
            # 末尾追加当前用户消息
            messages = list(history)
            # history 由 stream_agent_response 通过 PromptBuilder 构建，已含当前消息
            answer = await _llm_client.generate(
                messages=messages,
                temperature=0.7,
                max_tokens=1000,
            )
        else:
            # 无历史时降级为单轮模式
            answer = await _llm_client.generate(
                prompt=state["user_message"],
                system_prompt=base_system_prompt,
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

    # ── Task 40: 如果没有 session_id，自动生成一个 ─────────────────────────────
    from app.services.session_memory import generate_session_id
    effective_session_id = session_id or generate_session_id()

    # ── Task 40: 通过 PromptBuilder 构建带历史的 messages ─────────────────────
    conversation_history: list = []
    if _prompt_builder:
        try:
            base_system = (
                "你是一个专业的企业工时管理助手。"
                "请用简洁、友好的方式回答用户问题。"
                "如果涉及工时管理相关功能，可以引导用户使用对应的功能。"
            )
            conversation_history = await _prompt_builder.build_messages_with_history(
                user_message=message,
                session_id=effective_session_id,
                user_id=user_id if user_id != "anonymous" else None,
                base_system_prompt=base_system,
            )
        except Exception as e:
            logger.warning(f"构建带历史 messages 失败，降级为无历史模式: {e}")

    initial_state: AgentState = {
        "user_message": message,
        "user_context": user_ctx,
        "session_id": effective_session_id,
        "conversation_history": conversation_history,
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
        "session_id": effective_session_id,
    })
    yield _format_sse("thinking", {"message": "正在分析您的请求意图..."})

    # 用于收集本轮 assistant 响应（供 finally 块保存记忆）
    _collected_assistant_response: str = ""

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
                        _collected_assistant_response = json.dumps(result, ensure_ascii=False)
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
                        _collected_assistant_response = result.get("response", "") if result else ""
                        yield _format_sse("response", {"result": result})

                elif node_name == "execute_llm":
                    llm_result = state_delta.get("llm_result")
                    error = state_delta.get("error")
                    if error and not llm_result:
                        log_status = "error"
                        log_error = error
                        yield _format_sse("error", {"message": error})
                    else:
                        _collected_assistant_response = llm_result or ""
                        yield _format_sse("response", {"message": llm_result or ""})

    except Exception as e:
        logger.error(f"LangGraph 流式执行异常: {e}", exc_info=True)
        log_status = "error"
        log_error = str(e)
        yield _format_sse("error", {"message": f"处理请求时发生错误: {e}"})

    finally:
        # ── Task 40: 保存本轮对话到短期记忆（失败不影响主流程）─────────────────
        if _prompt_builder and log_status == "success":
            try:
                from app.services.session_memory import get_session_memory
                session_svc = get_session_memory()
                if session_svc and _collected_assistant_response:
                    await session_svc.add_messages(
                        session_id=effective_session_id,
                        user_id=user_id,
                        user_content=message,
                        assistant_content=_collected_assistant_response,
                        intent=log_intent,
                    )
            except Exception as mem_err:
                logger.debug(f"保存会话历史失败（非关键）: {mem_err}")

        # ── Task 40: 工具调用成功后提取长期记忆（方案B）────────────────────────
        if (
            _prompt_builder
            and log_intent == "tool_execution"
            and log_status == "success"
            and user_id != "anonymous"
            and _collected_assistant_response
        ):
            try:
                from app.services.user_memory import get_user_memory
                user_memory_svc = get_user_memory()
                if user_memory_svc:
                    await _try_extract_long_term_memory(
                        user_memory_svc=user_memory_svc,
                        user_id=user_id,
                        user_message=message,
                        assistant_response=_collected_assistant_response,
                    )
            except Exception as mem_err:
                logger.debug(f"长期记忆提取失败（非关键）: {mem_err}")

        # 写会话日志（失败不影响主流程）
        try:
            from app.services.conversation_logger import get_conversation_logger
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            get_conversation_logger().log_conversation(
                session_id=effective_session_id,
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

    yield _format_sse("done", {"message": "请求处理完成", "session_id": effective_session_id})


# ─── 长期记忆提取辅助函数 ──────────────────────────────────────────────────────

async def _try_extract_long_term_memory(
    user_memory_svc,
    user_id: str,
    user_message: str,
    assistant_response: str,
) -> None:
    """
    在工具调用成功后，尝试从对话中提取值得长期记忆的信息。

    提取规则（基于规则，不消耗 LLM token）：
    - 用户明确提到自己的 user_id / 工号
    - 用户表达偏好（"我一般"、"我习惯"、"我通常"）
    - 用户说明工作场景（"我是 XX 部门"、"我负责 XX 项目"）
    """
    import re

    patterns = [
        # 身份信息
        (r"我的?(user_?id|工号|员工号|账号)[是为：:]\s*(\S+)", 0.9),
        (r"(user_?id|工号)[是为：:]\s*(\S+)", 0.9),
        # 组织信息
        (r"我[在是](.{2,10}部门)", 0.7),
        (r"我负责(.{2,20}项目)", 0.7),
        # 偏好
        (r"我(一般|习惯|通常|喜欢|倾向)(.{4,30})", 0.6),
    ]

    for pattern, importance in patterns:
        match = re.search(pattern, user_message)
        if match:
            content = f"用户表达：{user_message.strip()}"
            await user_memory_svc.store_memory(
                user_id=user_id,
                content=content,
                importance=importance,
            )
            logger.debug(f"提取长期记忆: user_id={user_id}, importance={importance:.1f}")
            break  # 每轮最多存一条，避免冗余
