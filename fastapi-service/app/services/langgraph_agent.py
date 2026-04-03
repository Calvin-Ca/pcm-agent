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

from app.services.prompt_manager import get_prompt_manager

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
    intent: Optional[str]          # knowledge_qa | tool_execution | complex_request | general_chat | clarify
    tool_name: Optional[str]
    tool_params: Dict[str, Any]
    query: str                     # RAG / 工具查询用
    # 引导填报（缺少必要参数时）
    clarify_message: Optional[str]
    # 执行结果（仅一个非 None）
    tool_result: Optional[Dict[str, Any]]
    rag_result: Optional[Dict[str, Any]]
    llm_result: Optional[str]
    error: Optional[str]


# ─── 节点函数 ─────────────────────────────────────────────────────────────────

def _build_openai_tools(tool_registry) -> list:
    """将 ToolRegistry 的 json_schema 转换为 OpenAI function calling 格式"""
    tools = []
    for tool_def in tool_registry.list_tools():
        schema = {k: v for k, v in tool_def.json_schema.items()
                  if k != "additionalProperties"}
        tools.append({
            "type": "function",
            "function": {
                "name": tool_def.name,
                "description": tool_def.description,
                "parameters": schema,
            }
        })
    return tools


async def node_llm_with_tools(state: AgentState) -> dict:
    """
    节点：Function Calling 主入口
    一次 LLM 调用同时完成：意图识别 + 工具选择 + 参数提取 + 缺参追问
    LLM/registry 不可用时自动降级到 node_classify_intent
    """
    if not _llm_client or not _tool_registry:
        return await node_classify_intent(state)

    try:
        tools = _build_openai_tools(_tool_registry)
        if not tools:
            return await node_classify_intent(state)

        messages = state.get("conversation_history") or []
        if not messages:
            return await node_classify_intent(state)

        result = await _llm_client.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=500,
        )
    except Exception as e:
        logger.warning(f"Function Calling 失败，降级到规则路由: {e}")
        return await node_classify_intent(state)

    if result.get("finish_reason") == "tool_calls":
        tool_calls = result.get("tool_calls", [])
        if not tool_calls:
            return await node_classify_intent(state)

        tc = tool_calls[0]
        tool_name = tc["name"]
        tool_params = dict(tc.get("arguments", {}))

        user_ctx = state.get("user_context") or {}
        if user_ctx.get("user_id") and "user_id" not in tool_params and "member_name" not in tool_params:
            tool_params["user_id"] = user_ctx["user_id"]
        if user_ctx.get("auth_token"):
            tool_params["auth_token"] = user_ctx["auth_token"]

        # knowledge_qa 工具：路由到 execute_rag，不走 execute_tool
        if tool_name == "knowledge_qa":
            return {
                "intent": "knowledge_qa",
                "tool_name": None,
                "tool_params": {},
                "query": tool_params.get("query", state["user_message"]),
            }

        if tool_name == "save_workhour":
            missing = []
            if not tool_params.get("project_id"):
                missing.append("**项目名称或项目ID**")
            if not tool_params.get("date"):
                missing.append("**工时日期**（如'今天'、'2026-03-26'）")
            if not tool_params.get("duration"):
                missing.append("**工时时长**（小时，如 8 或 4.5）")
            if missing:
                clarify_msg = _build_workhour_clarify_message(tool_params, missing)
                return {
                    "intent": "clarify",
                    "tool_name": None,
                    "tool_params": tool_params,
                    "query": state["user_message"],
                    "clarify_message": clarify_msg,
                }

        return {
            "intent": "tool_execution",
            "tool_name": tool_name,
            "tool_params": tool_params,
            "query": state["user_message"],
        }

    # finish_reason == "stop"：LLM 返回文字（闲聊/general_chat）
    content = result.get("content", "")
    user_message = state.get("user_message", "")

    # general_chat：预填 llm_result，避免再调一次 LLM
    return {
        "intent": "general_chat",
        "tool_name": None,
        "tool_params": {},
        "query": user_message,
        "llm_result": content,
    }


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

    # 将会话历史注入 user_ctx，供 route_intent 内部的 LLM 分类和参数提取使用
    user_ctx = dict(state.get("user_context") or {})
    full_history = state.get("conversation_history") or []
    # 去掉 system 消息和最后一条（当前用户消息），只保留历史对话轮次
    history_turns = [m for m in full_history[1:-1] if m.get("role") in ("user", "assistant")]
    if history_turns:
        user_ctx["conversation_history"] = history_turns

    try:
        intent_result = await router.route_intent(
            state["user_message"],
            user_ctx,
        )
    except Exception as e:
        logger.error(f"意图分类节点异常: {e}")
        return {"intent": "general_chat", "tool_name": None, "tool_params": {}, "query": state["user_message"], "error": str(e)}

    tool_params: Dict[str, Any] = {}

    if intent_result.intent_type == IntentType.TOOL_EXECUTION:
        tool_name = intent_result.parameters.get("tool_name")

        # 提取工具参数（排除路由元数据字段）
        # route_intent 内部已调用 _extract_parameters_with_llm，此处直接使用结果
        tool_params = {
            k: v for k, v in intent_result.parameters.items()
            if k not in ("tool_name", "matched_keywords", "llm_classified", "query")
        }

        # 注入用户身份（工时查询必须）
        # 若已有 member_name（查询他人），则不注入当前用户 ID，
        # 否则 query_timesheet 会因 resolved_user_id 非空而跳过成员查询
        if user_ctx.get("user_id") and "user_id" not in tool_params and "member_name" not in tool_params:
            tool_params["user_id"] = user_ctx["user_id"]
        if user_ctx.get("auth_token"):
            tool_params["auth_token"] = user_ctx["auth_token"]

        # 工时填报：检测必填参数是否缺失，缺失则转为引导对话
        if tool_name == "save_workhour":
            missing = []
            if not tool_params.get("project_id"):
                missing.append("**项目名称或项目ID**")
            if not tool_params.get("date"):
                missing.append("**工时日期**（如'今天'、'2026-03-26'）")
            if not tool_params.get("duration"):
                missing.append("**工时时长**（小时，如 8 或 4.5）")
            if missing:
                clarify_msg = _build_workhour_clarify_message(tool_params, missing)
                return {
                    "intent": "clarify",
                    "tool_name": None,
                    "tool_params": tool_params,
                    "query": state["user_message"],
                    "clarify_message": clarify_msg,
                }

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


def _build_workhour_clarify_message(partial_params: Dict[str, Any], missing: list) -> str:
    """生成引导式提问，收集工时填报缺失的必要信息。"""
    lines = ["好的，我来帮您填报工时！请提供以下缺少的信息：\n"]
    for i, field in enumerate(missing, 1):
        lines.append(f"{i}. {field}")

    already = []
    if partial_params.get("project_id"):
        already.append(f"项目：{partial_params['project_id']}")
    if partial_params.get("date"):
        already.append(f"日期：{partial_params['date']}")
    if partial_params.get("duration"):
        already.append(f"时长：{partial_params['duration']}小时")
    if partial_params.get("description"):
        already.append(f"描述：{partial_params['description']}")
    if already:
        lines.append(f"\n（已获取：{', '.join(already)}）")

    lines.append("\n💡 您可以一次性回复所有信息，例如：")
    lines.append("「工时管理系统，今天，8小时，完成了AI助手开发」")
    return "\n".join(lines)


async def node_clarify(state: AgentState) -> dict:
    """节点：引导用户补充工时填报缺失参数（不调用 LLM，直接返回引导问题）"""
    return {"llm_result": state.get("clarify_message", "请提供更多信息以便完成工时填报。")}


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
    if state.get("llm_result"):
        return {"llm_result": state["llm_result"]}  # node_llm_with_tools 已预填，直接透传

    if not _llm_client:
        return {"llm_result": "LLM 服务未初始化", "error": "LLM not available"}

    base_system_prompt = get_prompt_manager().format("system") or (
        "你是一个专业的企业工时管理助手。"
        "请用简洁、友好的方式回答用户问题。"
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
        "clarify": "clarify_node",           # 工时填报引导对话
    }.get(intent, "execute_llm")


# ─── 构建图 ───────────────────────────────────────────────────────────────────

def _build_graph():
    """构建并编译 LangGraph StateGraph"""
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("llm_with_tools", node_llm_with_tools)  # Function Calling 主入口
    builder.add_node("execute_tool", node_execute_tool)
    builder.add_node("execute_rag", node_execute_rag)
    builder.add_node("execute_llm", node_execute_llm)
    builder.add_node("clarify_node", node_clarify)

    # 入口：Function Calling 节点
    builder.add_edge(START, "llm_with_tools")

    # 条件路由（llm_with_tools → 执行节点之一）
    builder.add_conditional_edges(
        "llm_with_tools",
        _route_by_intent,
        {
            "execute_tool": "execute_tool",
            "execute_rag": "execute_rag",
            "execute_llm": "execute_llm",
            "clarify_node": "clarify_node",
        },
    )

    # 所有执行节点 → END
    builder.add_edge("execute_tool", END)
    builder.add_edge("execute_rag", END)
    builder.add_edge("execute_llm", END)
    builder.add_edge("clarify_node", END)

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
    log_history_turns = 0
    log_memory_count = 0
    log_context_snapshot: Optional[dict] = None
    log_tool_name: str = ""           # 运行时从 classify_intent 节点获取
    log_tools_called: list = []       # 记录工具调用列表，用于 tool_count
    log_ai_response: str = ""         # 收集完整的 AI 响应文本

    # ── Task 40: 如果没有 session_id，自动生成一个 ─────────────────────────────
    from app.services.session_memory import generate_session_id
    effective_session_id = session_id or generate_session_id()

    # ── Task 40: 通过 PromptBuilder 构建带历史的 messages ─────────────────────
    conversation_history: list = []
    if _prompt_builder:
        try:
            from datetime import timedelta as _timedelta
            _today = datetime.now().date()
            _week_start = _today - _timedelta(days=_today.weekday())
            try:
                base_system = get_prompt_manager().format(
                    "system",
                    user_id=user_id if user_id != "anonymous" else "未知",
                    user_name=user_ctx.get("user_name", user_id or "用户"),
                    entity_type=user_ctx.get("entity_type", "employee"),
                    department_id=user_ctx.get("department_id", ""),
                    today=str(_today),
                    week_start=str(_week_start),
                    week_end=str(_week_start + _timedelta(days=6)),
                    last_week_start=str(_week_start - _timedelta(weeks=1)),
                    last_week_end=str(_week_start - _timedelta(days=1)),
                ) or "你是一个专业的企业工时管理助手。请用简洁、友好的方式回答用户问题。"
            except Exception:
                base_system = "你是一个专业的企业工时管理助手。请用简洁、友好的方式回答用户问题。"
            conversation_history = await _prompt_builder.build_messages_with_history(
                user_message=message,
                session_id=effective_session_id,
                user_id=user_id if user_id != "anonymous" else None,
                base_system_prompt=base_system,
            )
            # 统计注入的历史轮次和记忆条数（用于日志）
            # conversation_history = [system, user1, asst1, ..., current_user]
            # 去掉 system 和最后一条（当前消息），剩余条数 / 2 = 历史轮次
            history_msgs = [m for m in conversation_history[1:-1] if m.get("role") in ("user", "assistant")]
            log_history_turns = len(history_msgs) // 2
            # 从 system prompt 中提取记忆条数（粗略统计）
            system_content = conversation_history[0].get("content", "") if conversation_history else ""
            log_memory_count = system_content.count("\n-") if "关于该用户的已知信息" in system_content else 0
            # 构建上下文快照：最近2轮历史 + 记忆摘要
            recent_history = history_msgs[-4:]  # 最近2轮
            memories_section = ""
            if "关于该用户的已知信息" in system_content:
                start = system_content.find("关于该用户的已知信息")
                memories_section = system_content[start:]
            log_context_snapshot = {
                "history": recent_history,
                "memories": memories_section or None,
            }
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
        "clarify_message": None,
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
                if node_name in ("classify_intent", "llm_with_tools"):
                    intent = state_delta.get("intent", "general_chat")
                    log_intent = intent
                    log_route_type = {
                        "knowledge_qa": "rag_engine",
                        "tool_execution": "tool_executor",
                        "complex_request": "llm_service",
                        "general_chat": "llm_service",
                        "clarify": "clarify",
                    }.get(intent, "llm_service")

                    if intent == "tool_execution":
                        tool_name = state_delta.get("tool_name", "unknown")
                        log_tool_name = tool_name  # 记录真实工具名，供日志和摘要使用
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
                        log_tools_called = [{"tool_name": log_tool_name, "success": False, "error": msg}]
                        yield _format_sse("error", {"message": msg})
                    else:
                        _collected_assistant_response = _summarize_tool_result(log_tool_name, result)
                        log_ai_response = _collected_assistant_response
                        log_tools_called = [{"tool_name": log_tool_name, "success": True}]
                        yield _format_sse("response", {
                            "result": result,
                            "tool_name": log_tool_name,
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
                        response_text = result.get("response", "") if result else ""
                        sources = result.get("sources", []) if result else []
                        if sources:
                            source_names = [s.get("source", "") for s in sources if s.get("source")]
                            if source_names:
                                response_text += "\n\n---\n📚 **来源：** " + " | ".join(source_names)
                        _collected_assistant_response = response_text
                        log_ai_response = response_text
                        yield _format_sse("response", {"message": response_text})

                elif node_name == "execute_llm":
                    llm_result = state_delta.get("llm_result")
                    error = state_delta.get("error")
                    if error and not llm_result:
                        log_status = "error"
                        log_error = error
                        yield _format_sse("error", {"message": error})
                    else:
                        _collected_assistant_response = llm_result or ""
                        log_ai_response = _collected_assistant_response
                        yield _format_sse("response", {"message": llm_result or ""})

                elif node_name == "clarify_node":
                    # 引导式工时填报：直接输出引导问题
                    clarify_text = state_delta.get("llm_result", "")
                    _collected_assistant_response = clarify_text
                    log_ai_response = clarify_text
                    yield _format_sse("response", {"message": clarify_text})

    except PermissionError as e:
        logger.warning(f"权限拒绝: {e}")
        log_status = "rejected"
        log_error = str(e)
        log_tools_called = [{"tool_name": log_tool_name, "success": False, "error": str(e), "rejected": True}] if log_tool_name else []
        yield _format_sse("error", {"message": f"权限不足: {e}"})

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
            import os
            from app.services.conversation_logger import get_conversation_logger
            from app.services.database import get_db_service
            from app.models.ai_session import AiSession

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            model = os.getenv("CHAT_LLM_MODEL", os.getenv("LLM_MODEL", "unknown"))

            # 截断过大的 JSON 字段，防止单行超 MB
            safe_snapshot = log_context_snapshot
            if safe_snapshot and len(json.dumps(safe_snapshot, ensure_ascii=False)) > 8000:
                safe_snapshot = {
                    "history": safe_snapshot.get("history", [])[-2:],
                    "memories": (safe_snapshot.get("memories") or "")[:500] or None,
                    "truncated": True,
                }

            total_tokens = 0
            get_conversation_logger().log_conversation(
                session_id=effective_session_id,
                user_id=user_id,
                user_message=message,
                route_type=log_route_type or "unknown",
                intent=log_intent,
                history_turns_count=log_history_turns,
                memory_count=log_memory_count,
                context_snapshot=safe_snapshot,
                tools_called=log_tools_called or None,
                ai_response=log_ai_response or None,
                duration_ms=duration_ms,
                model_name=model,
                status=log_status,
                error_message=log_error,
            )

            # 同步更新 ai_sessions 汇总（upsert）
            if user_id != "anonymous":
                db = get_db_service()
                with db.get_session() as sess:
                    session_row = sess.query(AiSession).filter_by(session_id=effective_session_id).first()
                    if session_row:
                        session_row.turn_count += 1
                        session_row.total_tokens += total_tokens
                        session_row.last_active = datetime.now()
                    else:
                        sess.add(AiSession(
                            session_id=effective_session_id,
                            user_id=user_id,
                            turn_count=1,
                            total_tokens=total_tokens,
                            created_at=datetime.now(),
                            last_active=datetime.now(),
                        ))
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


# ─── 工具结果格式化（用于存入会话记忆，供多轮对话上下文使用）─────────────────────

def _summarize_tool_result(tool_name: str, result: Optional[Dict[str, Any]]) -> str:
    """
    将工具执行结果转为简洁的自然语言摘要，存入会话记忆。
    避免将大体量 JSON 写入 Redis，同时让 LLM 在下轮对话中能理解上一轮内容。
    """
    if not result:
        return "查询完成，无数据。"

    # 取 task_executor 包装的内层结果
    inner = result.get("result", result)

    if inner.get("success") is False:
        return f"查询失败：{inner.get('error', '未知错误')}"

    if tool_name == "query_timesheet":
        summary = inner.get("summary", {})
        total = inner.get("total_hours", 0)
        count = inner.get("record_count", 0)
        date_range = summary.get("date_range", "")
        member = summary.get("member_name", "")
        projects = summary.get("projects", {})

        parts = []
        if member:
            parts.append(f"查询对象：{member}")
        if date_range:
            parts.append(f"时间范围：{date_range}")
        parts.append(f"总工时：{total} 小时，共 {count} 条记录")
        if projects:
            proj_lines = [f"{p}：{s.get('total_hours', 0)} 小时" for p, s in list(projects.items())[:5]]
            parts.append("各项目：" + "；".join(proj_lines))
        return "工时查询结果：" + "；".join(parts)

    if tool_name == "query_project":
        projects = inner.get("projects", [])
        if not projects:
            return "项目查询完成，无数据。"
        names = [p.get("name") or p.get("projectName", "") for p in projects[:5]]
        return f"项目查询结果：共 {len(projects)} 个项目，包括：{', '.join(names)}"

    # 通用兜底：只取关键字段
    keys = ["message", "total", "count", "status"]
    parts = [f"{k}={inner[k]}" for k in keys if k in inner]
    return f"{tool_name} 执行完成" + (f"：{'; '.join(parts)}" if parts else "")
