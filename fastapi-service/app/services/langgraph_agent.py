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
import os
import re
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
    # 多步规划
    task_plan: Optional[Dict[str, Any]]          # 序列化的 TaskPlan（plan_and_execute 使用）
    plan_results: Optional[Dict[str, Any]]       # 各任务执行结果 {task_id: result}


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


def _expand_multi_day_date(date_str: str, duration: float) -> list:
    """
    将多天日期表达展开为多个 (date, duration) 元组列表。
    支持格式：
    - "周一到周五" / "周一至周五" / "周一~周五"
    - "周一、周二、周三"
    - "每天" / "每天都"（展开为工作日）
    - 单个工作日："周一" / "星期三"
    - 相对日期："今天"、"昨天"、"明天"、"后天"及组合
    无法解析时返回空列表。
    """
    from datetime import date, timedelta
    
    today = date.today()
    weekday_map = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}
    
    # 相对日期映射
    relative_map = {
        "今天": today,
        "昨天": today - timedelta(days=1),
        "明天": today + timedelta(days=1),
        "后天": today + timedelta(days=2),
        "大后天": today + timedelta(days=3),
        "前天": today - timedelta(days=2),
    }
    
    def get_weekday_date(weekday_name: str):
        """获取本周指定工作日的日期"""
        w = weekday_map.get(weekday_name)
        if w is None:
            return None
        days_ahead = w - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    
    date_str = date_str.strip()
    
    # 展开为日期列表
    expanded = []
    
    # 情况1：包含"每天"（每天、工作日每天）
    if "每天" in date_str or "每天都" in date_str:
        # 周一到周五
        for w in ["周一", "周二", "周三", "周四", "周五"]:
            d = get_weekday_date(w)
            if d:
                expanded.append((d, duration))
        return expanded
    
    # 情况2：范围格式 "周一到周五" / "周一至周五" / "周一~周五"
    range_pattern = re.compile(r"([周拾一二三四五六日])[一到至~]([周拾一二三四五六日])")
    m = range_pattern.search(date_str)
    if m:
        start_day, end_day = m.group(1), m.group(2)
        # 转换中文数字
        day_map = {"周": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        start_w = day_map.get(start_day.replace("周", "一").replace("拾", "十")[0] if start_day in ["周", "拾"] else start_day[0])
        end_w = day_map.get(end_day.replace("周", "一").replace("拾", "十")[0] if end_day in ["周", "拾"] else end_day[0])
        if start_w is not None and end_w is not None:
            # 找到本周一
            monday = today - timedelta(days=today.weekday())
            for delta in range(start_w, end_w + 1):
                d = (monday + timedelta(days=delta)).strftime("%Y-%m-%d")
                expanded.append((d, duration))
        return expanded
    
    # 情况3：逗号分隔 "周一、周三、周五"
    if "、" in date_str:
        days = re.findall(r"[周拾一二三四五六日][一两二三四五六日]?", date_str)
        for d in days:
            parsed = get_weekday_date(d)
            if parsed:
                expanded.append((parsed, duration))
        return expanded
    
    # 情况4：单个工作日
    single_day = re.search(r"[周拾一二三四五六日][一两二三四五六日]?", date_str)
    if single_day:
        parsed = get_weekday_date(single_day.group())
        if parsed:
            return [(parsed, duration)]
    
    # 情况5：相对日期 "今天和昨天" / "今天明天后天"
    rel_dates = []
    rel_found = False
    for rel_name, rel_date in relative_map.items():
        if rel_name in date_str:
            rel_dates.append(rel_date)
            rel_found = True
    if rel_found and rel_dates:
        for d in rel_dates:
            expanded.append((d.strftime("%Y-%m-%d"), duration))
        return expanded

    return []


async def node_llm_with_tools(state: AgentState) -> dict:
    """
    节点：Function Calling 主入口
    一次 LLM 调用同时完成：意图识别 + 工具选择 + 参数提取 + 缺参追问
    LLM/registry 不可用时自动降级到 node_classify_intent
    """
    # 基准测试模式：强制降级到两次 LLM 调用的 classify_intent 路径
    user_ctx = state.get("user_context") or {}
    if os.getenv("BENCHMARK_FORCE_FALLBACK") == "1" or user_ctx.get("_benchmark_force_fallback"):
        return await node_classify_intent(state)

    if not _llm_client or not _tool_registry:
        return await node_classify_intent(state)

    try:
        tools = _build_openai_tools(_tool_registry)
        if not tools:
            return await node_classify_intent(state)

        messages = state.get("conversation_history") or []
        if not messages:
            return await node_classify_intent(state)

        # num_ctx 自适应：历史超过 2000 字用大 context（仅 Ollama 支持）
        history_chars = sum(
            len(m.get("content", "")) for m in messages
        )
        num_ctx = (
            8192 if history_chars > 2000 else 4096
        )

        # Ollama 专属参数，vLLM 不识别会被忽略
        is_ollama = "11434" in (_llm_client.api_base or "")
        extra = {"num_ctx": num_ctx, "think": False} if is_ollama else {}

        result = await _llm_client.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=1500,
            extra=extra,
        )
    except Exception as e:
        logger.warning(f"Function Calling 失败，降级到规则路由: {e}")
        return await node_classify_intent(state)

    if result.get("finish_reason") == "tool_calls":
        tool_calls = result.get("tool_calls", [])
        if not tool_calls:
            return await node_classify_intent(state)

        user_ctx = state.get("user_context") or {}

        # ── 多工具调用：构建并行 TaskPlan ──────────────────────────────────────
        if len(tool_calls) >= 2:
            tasks = []
            for i, tc in enumerate(tool_calls):
                t_name = tc["name"]
                t_params = dict(tc.get("arguments", {}))
                if t_name == "knowledge_qa":
                    continue
                if user_ctx.get("user_id") and "user_id" not in t_params and "member_name" not in t_params:
                    t_params["user_id"] = user_ctx["user_id"]
                if user_ctx.get("auth_token"):
                    t_params["auth_token"] = user_ctx["auth_token"]
                tasks.append({
                    "task_id": f"t{i+1}",
                    "task_type": "tool_call",
                    "tool_name": t_name,
                    "parameters": t_params,
                    "dependencies": [],
                })
            if tasks:
                return {
                    "intent": "complex_request",
                    "tool_name": None,
                    "tool_params": {},
                    "query": state["user_message"],
                    "task_plan": {
                        "plan_name": "多工具并行执行",
                        "tasks": tasks,
                        "source": "multi_tool_calls",
                    },
                }

        # ── 单工具调用：原有逻辑不变 ─────────────────────────────────────────
        tc = tool_calls[0]
        tool_name = tc["name"]
        tool_params = dict(tc.get("arguments", {}))

        if user_ctx.get("user_id") and "user_id" not in tool_params and "member_name" not in tool_params:
            tool_params["user_id"] = user_ctx["user_id"]
        if user_ctx.get("auth_token"):
            tool_params["auth_token"] = user_ctx["auth_token"]

        if tool_name == "knowledge_qa":
            return {
                "intent": "knowledge_qa",
                "tool_name": None,
                "tool_params": {},
                "query": tool_params.get("query", state["user_message"]),
            }

        if tool_name == "save_workhour":
            # ── 多天展开：检测"周一到周五"等日期范围 ─────────────────────────
            raw_date = tool_params.get("date", "")
            duration = tool_params.get("duration", 0)
            expanded_dates = _expand_multi_day_date(raw_date, float(duration) if duration else 8) if raw_date else []
            
            if len(expanded_dates) >= 2:
                # 展开为多工具并行调用
                tasks = []
                for i, (d, dur) in enumerate(expanded_dates):
                    t_params = dict(tool_params)
                    t_params["date"] = d
                    t_params["duration"] = dur
                    if user_ctx.get("user_id"):
                        t_params["user_id"] = user_ctx["user_id"]
                    if user_ctx.get("auth_token"):
                        t_params["auth_token"] = user_ctx["auth_token"]
                    tasks.append({
                        "task_id": f"t{i+1}",
                        "task_type": "tool_call",
                        "tool_name": "save_workhour",
                        "parameters": t_params,
                        "dependencies": [],
                    })
                return {
                    "intent": "complex_request",
                    "tool_name": None,
                    "tool_params": {},
                    "query": state["user_message"],
                    "task_plan": {
                        "plan_name": "多天工时填报",
                        "tasks": tasks,
                        "source": "date_expansion",
                    },
                }
            
            # 单天或日期无效：走原有参数校验
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
                    "tool_name": tool_name,
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
            # ── 多天展开：检测"周一到周五"等日期范围 ─────────────────────────
            raw_date = tool_params.get("date", "")
            duration = tool_params.get("duration", 0)
            expanded_dates = _expand_multi_day_date(raw_date, float(duration) if duration else 8) if raw_date else []
            
            if len(expanded_dates) >= 2:
                # 展开为多工具并行调用
                tasks = []
                for i, (d, dur) in enumerate(expanded_dates):
                    t_params = dict(tool_params)
                    t_params["date"] = d
                    t_params["duration"] = dur
                    if user_ctx.get("user_id"):
                        t_params["user_id"] = user_ctx["user_id"]
                    if user_ctx.get("auth_token"):
                        t_params["auth_token"] = user_ctx["auth_token"]
                    tasks.append({
                        "task_id": f"t{i+1}",
                        "task_type": "tool_call",
                        "tool_name": "save_workhour",
                        "parameters": t_params,
                        "dependencies": [],
                    })
                return {
                    "intent": "complex_request",
                    "tool_name": None,
                    "tool_params": {},
                    "query": state["user_message"],
                    "task_plan": {
                        "plan_name": "多天工时填报",
                        "tasks": tasks,
                        "source": "date_expansion",
                    },
                }
            
            # 单天或日期无效：走原有参数校验
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
                    "tool_name": tool_name,
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


async def node_plan_and_execute(state: AgentState) -> dict:
    """
    节点：多步任务规划 + 并行执行

    两条入口：
    A. state["task_plan"]["source"] == "multi_tool_calls"
       → LLM 已返回多个 tool_calls，直接执行，跳过 PlannerAgent
    B. intent == "complex_request"（来自规则路由降级）
       → 调用 PlannerAgent 生成 TaskPlan，再执行
    """
    from app.models.task_plan import TaskPlan, TaskNode, TaskType, PlannerAgent

    user_ctx = state.get("user_context") or {}
    permission_ctx = user_ctx.get("permission_context")

    raw_plan = state.get("task_plan")

    # ── 路径 A：multi_tool_calls，直接构建 TaskPlan ─────────────────────────
    if raw_plan and raw_plan.get("source") == "multi_tool_calls":
        task_plan = TaskPlan(
            name=raw_plan.get("plan_name", "多工具执行"),
            description="LLM 多工具调用自动规划",
            user_request=state["user_message"],
        )
        for t in raw_plan.get("tasks", []):
            node = TaskNode(
                task_id=t["task_id"],
                task_type=TaskType.TOOL_CALL,
                tool_name=t["tool_name"],
                parameters=t["parameters"],
                dependencies=t.get("dependencies", []),
            )
            task_plan.add_task(node)

    # ── 路径 B：complex_request，调用 PlannerAgent ──────────────────────────
    else:
        if not _llm_client or not _tool_registry:
            return {"llm_result": "抱歉，多步规划功能暂时不可用。", "error": "规划组件未初始化"}

        planner = PlannerAgent(
            tool_registry=_tool_registry,
            llm_client=_llm_client,
        )
        try:
            task_plan = await planner.plan_tasks(
                user_request=state["user_message"],
                user_context=user_ctx,
            )
        except Exception as e:
            logger.error(f"PlannerAgent 规划失败: {e}")
            return {"llm_result": "抱歉，任务规划失败，请尝试更简单的问题描述。", "error": str(e)}

    # ── 执行 TaskPlan ────────────────────────────────────────────────────────
    if not _task_executor:
        return {"llm_result": "任务执行器未初始化。", "error": "TaskExecutor 未初始化"}

    try:
        summary = await _task_executor.execute_plan(
            task_plan=task_plan,
            permission_context=permission_ctx,
            timeout=120,
        )
        plan_results = summary.get("task_results", {})
        return {
            "plan_results": plan_results,
            "task_plan": {"plan_name": task_plan.name, "status": str(task_plan.status)},
        }
    except Exception as e:
        logger.error(f"TaskPlan 执行失败: {e}", exc_info=True)
        return {"llm_result": f"任务执行失败: {e}", "error": str(e)}


async def node_summarize(state: AgentState) -> dict:
    """
    节点：多步执行结果汇总

    将 plan_results 中的各工具执行结果交给 LLM 综合分析，
    生成面向用户的自然语言回答。
    """
    plan_results = state.get("plan_results") or {}
    user_message = state.get("user_message", "")

    if not plan_results:
        return {"llm_result": "所有任务均已完成，但未产生可汇总的结果。"}

    if not _llm_client:
        # 降级：直接拼接各工具结果
        parts = []
        for task_id, result in plan_results.items():
            r = result.get("result", result)
            if isinstance(r, dict) and r.get("success"):
                parts.append(str(r))
        return {"llm_result": "\n\n".join(parts) if parts else "任务已完成。"}

    # 构建汇总 prompt
    results_text = ""
    for task_id, result in plan_results.items():
        tool_name = result.get("tool_name", task_id)
        r = result.get("result", result)
        results_text += f"\n【{tool_name}】执行结果：\n{json.dumps(r, ensure_ascii=False, indent=2)}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "你是工时管理系统的智能助手。"
                "用户提出了一个需要多步操作的请求，系统已自动执行了多个工具并收集到结果。"
                "请将这些结果综合分析，用简洁、友好的语言回答用户的原始问题。"
                "如果有数据对比或排名，请用表格或列表呈现。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原始问题：{user_message}\n\n"
                f"各工具执行结果如下：{results_text}\n\n"
                "请根据以上结果，综合回答用户问题。"
            ),
        },
    ]

    try:
        answer = await _llm_client.generate(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
        )
        return {"llm_result": answer}
    except Exception as e:
        logger.error(f"汇总节点 LLM 调用失败: {e}")
        return {"llm_result": f"结果已收集，但汇总生成失败：{e}"}


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
        "complex_request": "plan_and_execute",   # 多步规划 + 并行执行
        "general_chat": "execute_llm",
        "clarify": "clarify_node",
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
    builder.add_node("plan_and_execute", node_plan_and_execute)   # 多步规划执行
    builder.add_node("summarize", node_summarize)                 # 结果汇总

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
            "plan_and_execute": "plan_and_execute",
        },
    )

    # 所有执行节点 → END（plan_and_execute → summarize → END）
    builder.add_edge("execute_tool", END)
    builder.add_edge("execute_rag", END)
    builder.add_edge("execute_llm", END)
    builder.add_edge("clarify_node", END)
    builder.add_edge("plan_and_execute", "summarize")
    builder.add_edge("summarize", END)

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
    user_id = user_ctx.get("user_id")
    if not user_id:
        user_id = "anonymous"
        logger.warning(
            f"user_id fallback to anonymous in stream_agent_response, "
            f"session_id={session_id}, user_context keys={list(user_ctx.keys())}"
        )
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
    _streaming_rag_active = False      # 知识问答时流式 RAG 标志

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
            _month_start = _today.replace(day=1)
            if _today.month == 12:
                _month_end = _today.replace(year=_today.year + 1, month=1, day=1) - _timedelta(days=1)
            else:
                _month_end = _today.replace(month=_today.month + 1, day=1) - _timedelta(days=1)
            _last_month_end = _month_start - _timedelta(days=1)
            _last_month_start = _last_month_end.replace(day=1)
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
                    month_start=str(_month_start),
                    month_end=str(_month_end),
                    last_month_start=str(_last_month_start),
                    last_month_end=str(_last_month_end),
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
        "task_plan": None,
        "plan_results": None,
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
                        _streaming_rag_active = True
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
                    # 流式 RAG：当 intent 阶段检测到 knowledge_qa 时，
                    # 在此处拦截，改为直接流式输出 RAG 内容
                    if _streaming_rag_active:
                        from app.services.langchain_rag import langchain_rag_stream_query
                        _full_response = ""
                        _final_sources = []
                        _final_retrieved_count = 0
                        try:
                            async for item in langchain_rag_stream_query(message):
                                if item.get("type") == "chunk":
                                    text = item.get("content", "")
                                    _full_response += text
                                    yield _format_sse("response", {"message": text})
                                elif item.get("type") == "done":
                                    _final_sources = item.get("sources", [])
                                    _final_retrieved_count = item.get("retrieved_count", 0)
                                elif item.get("type") == "error":
                                    _full_response = item.get("message", "RAG 查询失败")
                                    log_status = "error"
                                    log_error = _full_response
                                    yield _format_sse("error", {"message": _full_response})
                                    break
                        except Exception as rag_err:
                            log_status = "error"
                            log_error = str(rag_err)
                            yield _format_sse("error", {"message": f"RAG 查询异常: {rag_err}"})
                        else:
                            # 流式结束后，附加来源信息
                            if _final_sources:
                                source_names = [s.get("source", "") for s in _final_sources if s.get("source")]
                                if source_names:
                                    yield _format_sse("response", {
                                        "message": f"\n\n---\n📚 **来源：** " + " | ".join(source_names)
                                    })
                                    _full_response += f"\n\n---\n📚 **来源：** " + " | ".join(source_names)
                            _collected_assistant_response = _full_response
                            log_ai_response = _full_response
                        _streaming_rag_active = False
                        continue  # 跳过后续的普通 rag_result 处理

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

                elif node_name == "plan_and_execute":
                    # 多步执行：发送各工具调用进度事件
                    plan_results = state_delta.get("plan_results") or {}
                    error = state_delta.get("error")
                    if error:
                        log_status = "error"
                        log_error = error
                        yield _format_sse("error", {"message": error})
                    else:
                        log_intent = "complex_request"
                        log_route_type = "plan_executor"
                        log_tools_called = []
                        for task_id, res in plan_results.items():
                            tool_name = res.get("tool_name", task_id)
                            success = res.get("result", {}).get("success", True) if isinstance(res.get("result"), dict) else True
                            log_tools_called.append({"tool_name": tool_name, "success": success})
                            yield _format_sse("tool_call", {
                                "tool": tool_name,
                                "status": "success" if success else "error",
                                "task_id": task_id,
                            })
                        if not plan_results:
                            yield _format_sse("thinking", {"message": "多步任务执行中..."})

                elif node_name == "summarize":
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
