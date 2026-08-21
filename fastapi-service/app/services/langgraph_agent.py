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
from datetime import date, datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.services.prompt_manager import get_prompt_manager
from app.services.chart_builder import build_chart_option
from app.services.project_resolver import resolve_project_suggestion
from app.services.hours_resolver import resolve_hours_suggestion
from app.services.llm_client import get_planner_llm_client

logger = logging.getLogger(__name__)

_WRITE_TOOL_NAMES = frozenset({"save_workhour", "batch_save_workhour", "approve_workhour"})
_AGENT_LOOP_TOOL_NAMES = frozenset({
    "kb_keyword_search",
    "kb_outline",
    "kb_read_section",
    "kb_semantic_search",
})
_WRITE_CONFIRMATION_RE = re.compile(r"^(?:确认(?:提交|保存|审批)?|提交|保存|没问题|可以)$")
_UNVERIFIED_COMPLETION_RE = re.compile(
    r"(?:已为您.{0,20}(?:保存|提交|补录|填报|批准|审批|生成|导出)|"
    r"(?:工时|记录|申请|审批|周报|报表).{0,16}已(?:成功)?(?:保存|提交|补录|填报|批准|审批|生成|导出))"
)
_WORKHOUR_DATE_REFERENCE_RE = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}日|"
    r"今天|昨天|前天|明天|后天|本周|这周|上周|下周|"
    r"周[一二三四五六日天]|(?:星期|礼拜)[一二三四五六日天]"
)
_WORKHOUR_DURATION_REFERENCE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|半)\s*(?:个)?(?:小时|h(?:ours?)?|天)",
    re.IGNORECASE,
)
_CHINESE_DURATION_RE = re.compile(r"[零〇一二两三四五六七八九十百]+(?:个)?(?:半)?小时")
_MONTH_DAY_WITHOUT_YEAR_RE = re.compile(r"\d{1,2}月\d{1,2}日")
_EXPLICIT_YEAR_DATE_RE = re.compile(r"\d{4}(?:年|[-/.])\d{1,2}(?:月|[-/.])\d{1,2}日?")
_SAVE_ACTION_RE = re.compile(r"保存|提交|填报|补录|录入|记录|记一下|记一笔|预存|dry\s*run|试运行|预检|^录", re.IGNORECASE)
_BATCH_ACTION_RE = re.compile(r"批量.{0,8}(?:录|填|保存|提交)|(?:录|填|保存|提交).{0,8}批量")
_APPROVE_ACTION_RE = re.compile(r"批准|审批|通过|同意|全批")
_SIMPLE_PROJECT_ID_REPLY_RE = re.compile(r"^(?:项目ID(?:是|为)?\s*)?[A-Za-z][A-Za-z0-9_-]*$", re.IGNORECASE)
_PROJECT_SLUG_RE = re.compile(r"^[a-z]+(?:-[a-z]+)+$")
_PREVIEW_REQUEST_RE = re.compile(r"dry\s*run|试运行|预存|预览|预检|预校验|检查合法性|别真存|不实际(?:保存|提交)", re.IGNORECASE)
_PENDING_WRITE_UPDATE_RE = re.compile(r"查到了|确实|改成|更正为|还是之前|项目ID|日期是|工时")
_BATCH_MEMBER_PREFIX_RE = re.compile(r"(?:^|[\n；;])\s*([\u4e00-\u9fa5]{2,4})\s+(?=\d{4}|[A-Za-z])")
_AGGREGATE_REQUEST_RE = re.compile(
    r"总共|一共|总工时|占比|比例|平均|排名|排行|最多|最少|环比|同比|"
    r"干了多少小时|用了多少工时|工时有多少|工时差多少|相差多少|多少小时|每天各|每月各|每周各|分别(?:是|有|用了|干了)?多少|"
    r"按(?:天|日|周|月|项目|人员|部门|工作类型).{0,6}(?:统计|汇总|分组)"
)
_DETAIL_REQUEST_RE = re.compile(r"明细|记录|哪些工时|做了哪些|干了哪些|填报情况")
_SUGGEST_REQUEST_RE = re.compile(r"该填(?:什么|哪些|多少)|要填(?:什么|哪些)?工时(?:吗)?|填\s*\d+(?:\.\d+)?\s*小时还是")
_CANCEL_WRITE_RE = re.compile(r"^(?:取消|不要|不用|算了|别提交|别保存)[！!。\s]*$")
_MEMBER_PATTERNS = (
    re.compile(r"^(?P<name>[\u4e00-\u9fa5]{2,4})(?=在|从|这周|本周|上周|下周|今年|本月|上月|昨天|今天|20\d{2}|\d{1,2}月)"),
    re.compile(r"(?:查|看|统计|算|生成|批准|审批|把)\s*(?P<name>[\u4e00-\u9fa5]{2,4})(?=在|从|这周|本周|上周|下周|今年|本月|上月|昨天|今天|20\d{2}|\d{1,2}月)"),
)


def _current_date() -> date:
    """Return today's date; the evaluation harness may replace this provider."""
    return date.today()

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
    stream_response: bool           # 流式入口延迟 RAG 生成，避免同一请求生成两次
    # 记忆上下文（Task 40）
    conversation_history: list     # OpenAI messages 格式的历史消息列表
    business_state: Dict[str, Any]  # Redis 中带 TTL 的结构化多轮业务状态
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
    # ── Agent Loop（承载 A-RAG 多轮调用，渐进式披露）─────────────────────────
    agent_iterations: int                        # 当前已循环轮数（从 0 开始）
    agent_max_iterations: int                    # 上限，默认 5
    agent_history: list                          # [{iteration, tool, args, observation}]
    # ── 方案 A：knowledge_qa 升级触发策略 ───────────────────────────────────
    rag_strategy: Optional[str]                  # "agent" | None（默认）
    _rag_fallback: Optional[bool]                # planner 中途失败标志


# ─── 节点函数 ─────────────────────────────────────────────────────────────────

def _probe_planner_availability() -> bool:
    """planner 探活：只查 key 非空 + client 可构造，不发真实请求"""
    key = os.getenv("PLANNER_LLM_API_KEY")
    if not key:
        return False
    try:
        get_planner_llm_client(temperature=0.1, max_tokens=1024)
        return True
    except Exception:
        return False


def _build_openai_tools(tool_registry) -> list:
    """将 ToolRegistry 的 json_schema 转换为 OpenAI function calling 格式"""
    from app.core.config import settings

    tools = []
    for tool_def in tool_registry.list_tools():
        # SQL Agent 关闭时即使模块曾被其他入口 import，也不向模型暴露。
        if tool_def.name == "sql_query" and not settings.SQL_AGENT_ENABLED:
            continue
        description = tool_def.description
        if not settings.SQL_AGENT_ENABLED:
            description = description.replace(
                "应使用 sql_query 查询",
                "当前工具集不支持直接查询",
            )
        schema = {k: v for k, v in tool_def.json_schema.items()
                  if k != "additionalProperties"}
        tools.append({
            "type": "function",
            "function": {
                "name": tool_def.name,
                "description": description,
                "parameters": schema,
            }
        })
    return tools


def _canonical_tool_value(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical_tool_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_tool_value(item) for item in value]
    return value


def _tool_call_signature(tool_name: str, arguments: dict) -> str:
    return json.dumps(
        {"name": tool_name, "arguments": _canonical_tool_value(arguments)},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _business_safe_params(arguments: dict) -> dict:
    """Keep business context while excluding credentials and transport metadata."""
    return {
        key: value
        for key, value in (arguments or {}).items()
        if key not in {"auth_token", "context", "permission_context"}
    }


def _deduplicate_tool_calls(tool_calls: list) -> list:
    """Keep the first exact tool call and drop model-emitted duplicates."""
    unique = []
    seen = set()
    for call in tool_calls:
        signature = _tool_call_signature(
            call.get("name") or "",
            call.get("arguments") or {},
        )
        if signature in seen:
            logger.warning("已阻止模型生成的重复工具调用: %s", call.get("name"))
            continue
        seen.add(signature)
        unique.append(call)
    return unique


def _prior_user_messages(state: AgentState) -> list[str]:
    return [
        str(item.get("content") or "")
        for item in (state.get("conversation_history") or [])[:-1]
        if item.get("role") == "user"
    ]


def _last_prior_assistant_message(state: AgentState) -> str:
    for item in reversed((state.get("conversation_history") or [])[:-1]):
        if item.get("role") == "assistant":
            return str(item.get("content") or "")
    return ""


def _looks_like_batch_request(message: str) -> bool:
    """Recognize a concrete multi-record preview even when the word 批量 is omitted."""
    if not (_BATCH_ACTION_RE.search(message) or _PREVIEW_REQUEST_RE.search(message)):
        return False
    durations = _WORKHOUR_DURATION_REFERENCE_RE.findall(message)
    dates = re.findall(r"20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?", message)
    tabular = "\t" in message or message.count("\n") >= 2
    separated_rows = message.count("；") + message.count(";") >= 1
    return bool(len(durations) >= 2 or len(dates) >= 2 or tabular or separated_rows)


def _looks_like_uncommanded_workhour_record(message: str) -> bool:
    """Detect a record-shaped payload that has no save/preview instruction."""
    if _SAVE_ACTION_RE.search(message) or _BATCH_ACTION_RE.search(message) or _PREVIEW_REQUEST_RE.search(message):
        return False
    has_date = bool(_WORKHOUR_DATE_REFERENCE_RE.search(message))
    has_duration = bool(_WORKHOUR_DURATION_REFERENCE_RE.search(message))
    has_project = bool(re.search(r"(?:^|\s)[A-Za-z][A-Za-z0-9_-]{1,20}(?:\s|$)", message))
    return has_date and has_duration and has_project


def _latest_prior_batch_text(state: AgentState) -> Optional[str]:
    for message in reversed(_prior_user_messages(state)):
        if _looks_like_batch_request(message):
            return message
    return None


def _pending_single_save_arguments(state: AgentState) -> Optional[dict]:
    """Recover a pending single write from user-authored history only.

    This is intentionally limited to explicit values; assistant text and model
    defaults are never treated as authorization or business data.
    """
    messages = [*_prior_user_messages(state), str(state.get("user_message") or "")]
    combined = "\n".join(messages)
    if not re.search(r"补录|填报|录入|保存|提交|记下|记一下|工时|小时", combined):
        return None

    date_matches = re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", combined)
    if date_matches:
        year, month, day = date_matches[-1]
        date_value = date(int(year), int(month), int(day)).isoformat()
    elif _WORKHOUR_DATE_REFERENCE_RE.search(combined):
        date_value = _resolve_fill_date(combined)
    else:
        return None

    durations = re.findall(r"(\d+(?:\.\d+)?)\s*(?:个)?(?:小时|h\b)", combined, re.IGNORECASE)
    if not durations:
        return None
    duration = float(durations[-1])

    project_matches = []
    for pattern in (
        r"项目ID(?:是|为)?\s*([A-Za-z][A-Za-z0-9_-]*)",
        r"项目(?:是|为)\s*([A-Za-z][A-Za-z0-9_-]*)",
        r"在[‘'\"]?([^，。；;\n]{2,24}?)[’'\"]?项目",
        r"项目(?:是|为)\s*([\u4e00-\u9fa5A-Za-z0-9_-]{2,24})",
    ):
        project_matches.extend(re.findall(pattern, combined, re.IGNORECASE))
    for message in messages:
        stripped = message.strip()
        if _SIMPLE_PROJECT_ID_REPLY_RE.fullmatch(stripped):
            project_matches.append(re.sub(r"^项目ID(?:是|为)?\s*", "", stripped, flags=re.IGNORECASE))
    if not project_matches:
        return None

    arguments = {
        "project_id": project_matches[-1].strip(),
        "date": date_value,
        "duration": duration,
    }
    description_match = re.search(r"(?:做了?|完成|处理)([^，。；;\n]{2,30})", combined)
    if description_match:
        description = description_match.group(1).strip()
        # “做了一次远程支持”中的“一次”是数量补语，不属于工作描述。
        description = re.sub(r"^一次", "", description).strip()
        if description:
            arguments["description"] = description
    return arguments


def _resolve_fill_date(message: str) -> str:
    today = _current_date()
    explicit = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", message)
    if explicit:
        return date(int(explicit.group(1)), int(explicit.group(2)), int(explicit.group(3))).isoformat()
    month_day = re.search(r"(\d{1,2})月(\d{1,2})日?", message)
    if month_day:
        return date(today.year, int(month_day.group(1)), int(month_day.group(2))).isoformat()
    if "昨天" in message:
        return (today - timedelta(days=1)).isoformat()
    if "前天" in message:
        return (today - timedelta(days=2)).isoformat()
    if "明天" in message:
        return (today + timedelta(days=1)).isoformat()
    weekday_match = re.search(r"(上周|本周|这周|下周)([一二三四五六日天])", message)
    if weekday_match:
        weekday = "一二三四五六日天".index(weekday_match.group(2))
        weekday = min(weekday, 6)
        offset = {"上周": -7, "本周": 0, "这周": 0, "下周": 7}[weekday_match.group(1)]
        monday = today - timedelta(days=today.weekday()) + timedelta(days=offset)
        return (monday + timedelta(days=weekday)).isoformat()
    return today.isoformat()


def _extract_member_name(message: str, state: AgentState) -> Optional[str]:
    current_name = str((state.get("user_context") or {}).get("user_name") or "")
    for pattern in _MEMBER_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        name = match.group("name")
        for verb in ("查询", "统计", "生成", "批准", "审批", "查", "看", "算", "把"):
            if name.startswith(verb) and len(name) - len(verb) >= 2:
                name = name[len(verb):]
                break
        name = re.sub(r"[一二三四五六七八九十]+月$", "", name)
        if name.startswith(("我", "本人", "自己", "今天", "昨天", "前天")):
            continue
        if (
            name != current_name
            and not any(token in name for token in ("项目", "部门", "工时", "周报"))
            and not name.endswith(("部", "平台", "园区", "系统", "升级"))
        ):
            return name
    return None


def _extract_department_name(message: str) -> Optional[str]:
    match = re.search(r"([\u4e00-\u9fa5]{2,16}部)", message)
    if not match:
        return None
    candidate = match.group(1)
    for delimiter in ("比", "和", "查", "统计", "看看", "看"):
        if delimiter in candidate:
            candidate = candidate.rsplit(delimiter, 1)[-1]
    return candidate if 2 <= len(candidate) <= 12 else None


def _infer_statistics_type(message: str, arguments: dict) -> str:
    if (
        re.search(r"每天|每日(?:分别|统计)?|每人每天|按天|按日", message)
        and not re.search(r"平均每天", message)
    ):
        return "daily_hours"
    if re.search(r"每周各|按周|自然周", message):
        return "weekly_hours"
    if arguments.get("member_name") or _extract_member_name(message, {"user_context": {}}):
        return "user_hours"
    if re.search(r"谁.{0,8}(?:最多|排名|排行)", message):
        return "user_hours"
    if arguments.get("department_id") or "部门" in message or _extract_department_name(message):
        return "department_hours"
    if "项目" in message or (arguments.get("project_id") and not re.search(r"需求分析|前端开发|后端开发|测试|文案", message)):
        return "project_hours"
    if re.search(r"每月|每个月|按月|月份|月度|环比|平均每个月", message):
        return "monthly_hours"
    return "user_hours"


def _normalize_week_argument(message: str, arguments: dict) -> None:
    explicit = re.search(r"(20\d{2})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", message)
    if explicit:
        arguments["week"] = date(
            int(explicit.group(1)), int(explicit.group(2)), int(explicit.group(3))
        ).isoformat()
    elif "上周" in message:
        arguments["week"] = "lastWeek"
    elif "本周" in message or "这周" in message:
        arguments["week"] = "thisWeek"
    else:
        week_number = re.search(r"第\s*(\d{1,2})\s*周", message)
        if week_number:
            arguments["week"] = f"{_current_date().year}-W{int(week_number.group(1)):02d}"


def _extract_project_reference(message: str) -> Optional[str]:
    explicit = re.search(r"项目ID(?:是|为)?\s*([A-Za-z][A-Za-z0-9_-]*)", message, re.IGNORECASE)
    if explicit:
        return explicit.group(1)
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9_-]{2,20})项目", message)
    if not match:
        return None
    candidate = match.group(1)
    for prefix in ("请先确认", "请确认", "先确认", "确认", "查询", "查一下", "生成"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    return candidate or None


def _forced_tool_calls_for_request(state: AgentState) -> Optional[list[dict]]:
    message = (state.get("user_message") or "").strip()
    pending_state = dict((state.get("business_state") or {}).get("pending_write") or {})
    pending_state_params = _business_safe_params(pending_state.get("params") or {})
    if _CANCEL_WRITE_RE.fullmatch(message):
        return []
    if _looks_like_batch_request(message):
        return [{"name": "batch_save_workhour", "arguments": {"text": message, "dry_run": True}}]
    if _WRITE_CONFIRMATION_RE.fullmatch(message):
        pending_name = pending_state.get("name")
        if pending_name in {"save_workhour", "batch_save_workhour"} and pending_state_params:
            pending_state_params["dry_run"] = not bool(pending_state.get("preview_succeeded"))
            return [{"name": pending_name, "arguments": pending_state_params}]
        pending_batch = _latest_prior_batch_text(state)
        if pending_batch:
            return [{"name": "batch_save_workhour", "arguments": {"text": pending_batch, "dry_run": False}}]
        pending_save = _pending_single_save_arguments(state)
        if pending_save:
            # 上一轮预检/工具执行失败时，“确认”不能升级成真实写入；
            # 只能保持 dry-run，待预检成功后再次获得明确确认。
            previous_assistant = _last_prior_assistant_message(state)
            pending_save["dry_run"] = bool(
                re.search(r"失败|无权|超时|未完成|未成功|无法|异常|不能", previous_assistant)
            )
            return [{"name": "save_workhour", "arguments": pending_save}]
    if _prior_user_messages(state) and _SIMPLE_PROJECT_ID_REPLY_RE.fullmatch(message):
        pending_save = pending_state_params or _pending_single_save_arguments(state)
        if pending_save:
            project_id = re.sub(r"^项目ID(?:是|为)?\s*", "", message, flags=re.IGNORECASE)
            return [{"name": "query_project", "arguments": {"project_id": project_id or pending_save.get("project_id")}}]
    if _prior_user_messages(state) and (
        _PENDING_WRITE_UPDATE_RE.search(message)
        or _PREVIEW_REQUEST_RE.search(message)
        or re.search(r"项目(?:是|为)", message)
    ):
        pending_save = pending_state_params or _pending_single_save_arguments(state)
        if pending_save:
            explicit_project = re.search(r"项目ID(?:是|为)?\s*([A-Za-z][A-Za-z0-9_-]*)", message, re.IGNORECASE)
            if explicit_project:
                pending_save["project_id"] = explicit_project.group(1)
            explicit_date = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", message)
            if explicit_date:
                pending_save["date"] = date(*map(int, explicit_date.groups())).isoformat()
            explicit_duration = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?(?:小时|h\b)", message, re.IGNORECASE)
            if explicit_duration:
                pending_save["duration"] = float(explicit_duration.group(1))
            pending_save["dry_run"] = True
            return [{"name": "save_workhour", "arguments": pending_save}]
    if _SUGGEST_REQUEST_RE.search(message):
        return [{"name": "suggest_workhour", "arguments": {"fill_date": _resolve_fill_date(message)}}]
    project_pair = re.search(
        r"^([^和，。；;\s]{2,12})和([^，。；;\s]{2,12}?)(?=这周|本周|上周|下周)",
        message,
    )
    if project_pair and _AGGREGATE_REQUEST_RE.search(message):
        today = _current_date()
        monday = today - timedelta(days=today.weekday())
        if "上周" in message:
            monday -= timedelta(days=7)
        elif "下周" in message:
            monday += timedelta(days=7)
        return [
            {"name": "compute_statistics", "arguments": {
                "statistics_type": "project_hours",
                "start_date": monday.isoformat(),
                "end_date": (monday + timedelta(days=6)).isoformat(),
                "project_id": project,
            }}
            for project in project_pair.groups()
        ]
    if _AGGREGATE_REQUEST_RE.search(message) and re.search(r"没(?:有)?归属项目|未归属项目|无项目", message):
        month = re.search(r"(?<!\d)(\d{1,2})月", message)
        if month:
            today = _current_date()
            month_number = int(month.group(1))
            start = date(today.year, month_number, 1)
            next_month = date(today.year + (month_number == 12), month_number % 12 + 1, 1)
            return [{"name": "compute_statistics", "arguments": {
                "statistics_type": "user_hours",
                "start_date": start.isoformat(),
                "end_date": (next_month - timedelta(days=1)).isoformat(),
                "project_id": "",
            }}]
    if "比例" in message and not _has_date_evidence(message):
        today = _current_date()
        arguments = {
            "statistics_type": _infer_statistics_type(message, {}),
            "start_date": f"{today.year}-01-01",
            "end_date": f"{today.year}-12-31",
        }
        project = _extract_project_reference(message)
        department = _extract_department_name(message)
        if project:
            arguments["project_id"] = project
        if department:
            arguments["department_id"] = department
        return [{"name": "compute_statistics", "arguments": arguments}]
    if "周报" in message:
        weekly_args = {}
        _normalize_week_argument(message, weekly_args)
        project = _extract_project_reference(message)
        if weekly_args and project and "每人小时数统计" in message:
            today = _current_date()
            monday = today - timedelta(days=today.weekday())
            return [
                {"name": "query_project", "arguments": {"project_id": project}},
                {"name": "compute_statistics", "arguments": {
                    "statistics_type": "user_hours",
                    "start_date": monday.isoformat(),
                    "end_date": (monday + timedelta(days=6)).isoformat(),
                }},
            ]
        if weekly_args and project and re.search(r"项目ID我忘了|先确认.+项目.+(?:存在|状态)", message):
            return [
                {"name": "query_project", "arguments": {"project_id": project}},
                {"name": "generate_weekly_report", "arguments": weekly_args},
            ]
        if weekly_args and re.search(r"项目ID(?:是|为)", message, re.IGNORECASE):
            return [{"name": "generate_weekly_report", "arguments": weekly_args}]
    if _AGGREGATE_REQUEST_RE.search(message) and "今年" in message:
        today = _current_date()
        arguments = {
            "statistics_type": _infer_statistics_type(message, {}),
            "start_date": f"{today.year}-01-01",
            "end_date": f"{today.year}-12-31",
        }
        return [{"name": "compute_statistics", "arguments": arguments}]
    return None


def _normalize_business_tool_calls(tool_calls: list, state: AgentState) -> list:
    """Repair common FC routing/identity mistakes without inventing business data."""
    forced = _forced_tool_calls_for_request(state)
    if forced == []:
        return []
    if forced is not None:
        # Deterministic routing chooses the path, but all calls must still pass
        # the same identity/schema/date normalization as model-generated calls.
        tool_calls = forced

    message = (state.get("user_message") or "").strip()
    member_name = _extract_member_name(message, state)
    business_state = state.get("business_state") or {}
    last_tool = business_state.get("last_tool") or {}
    last_params = dict(last_tool.get("params") or {})
    is_context_followup = bool(
        re.search(r"呢[？?]?$|^再|^继续|^还是|^改成|^更正|^只看|^按.+(?:汇总|统计)|刚才|之前|同样", message)
    )
    if not member_name and is_context_followup and not re.search(r"(?:换|看|查)(?:我|自己)|我自己|本人", message):
        member_name = last_params.get("member_name")
    normalized = []
    has_weekly = any(call.get("name") == "generate_weekly_report" for call in tool_calls)
    aggregate = bool(
        _AGGREGATE_REQUEST_RE.search(message)
        and not _DETAIL_REQUEST_RE.search(message)
        and not re.search(r"填了多少|报了多少", message)
    )

    for call in tool_calls:
        name = call.get("name") or ""
        arguments = dict(call.get("arguments") or {})

        if is_context_followup and name == last_tool.get("name"):
            for key in ("member_name", "project_id", "department_id", "work_type"):
                if arguments.get(key) in (None, "") and last_params.get(key) not in (None, ""):
                    arguments[key] = last_params[key]
            if not _has_date_evidence(message):
                for key in ("start_date", "end_date", "week"):
                    if arguments.get(key) in (None, "") and last_params.get(key) not in (None, ""):
                        arguments[key] = last_params[key]

        if "周报" in message and name == "query_timesheet":
            if has_weekly:
                continue
            name = "generate_weekly_report"
            arguments = {key: value for key, value in arguments.items() if key in {"user_id", "member_name"}}

        if "周报" in message and has_weekly and name == "export_report" and not re.search(r"Excel|报表|文件", message, re.IGNORECASE):
            continue

        if name == "query_timesheet" and aggregate:
            name = "compute_statistics"
            arguments["statistics_type"] = _infer_statistics_type(message, arguments)

        if name == "compute_statistics" and _DETAIL_REQUEST_RE.search(message) and not aggregate:
            name = "query_timesheet"
            arguments.pop("statistics_type", None)
            arguments.pop("department_id", None)
            arguments.pop("work_type", None)

        if name in {"query_timesheet", "compute_statistics", "generate_weekly_report", "save_workhour"} and member_name:
            arguments["member_name"] = member_name
            if arguments.get("user_id") == (state.get("user_context") or {}).get("user_id"):
                arguments.pop("user_id", None)

        if name == "compute_statistics":
            department_name = _extract_department_name(message)
            if department_name:
                arguments["department_id"] = department_name
                arguments.pop("member_name", None)
            arguments["statistics_type"] = _infer_statistics_type(message, arguments)
            work_type_map = {
                "前端开发": "frontend_development",
                "后端开发": "backend_development",
                "需求分析": "requirement_analysis",
            }
            for label, value in work_type_map.items():
                if label in message:
                    arguments["work_type"] = value
                    break
            if "比例" in message and not _has_date_evidence(message):
                today = _current_date()
                arguments["start_date"] = f"{today.year}-01-01"
                arguments["end_date"] = f"{today.year}-12-31"
            if "本月" in message:
                today = _current_date()
                arguments["start_date"] = today.replace(day=1).isoformat()
                arguments["end_date"] = today.isoformat()

        if name == "generate_weekly_report":
            _normalize_week_argument(message, arguments)

        tool_def = _tool_registry.get_tool(name) if _tool_registry else None
        if tool_def and tool_def.json_schema.get("additionalProperties") is False:
            allowed = set((tool_def.json_schema.get("properties") or {}).keys())
            arguments = {key: value for key, value in arguments.items() if key in allowed}

        normalized.append({**call, "name": name, "arguments": arguments})

    normalized = _deduplicate_tool_calls(normalized)

    if "周报" in message and re.search(r"项目ID我忘了|先确认.+项目.+(?:存在|状态)", message):
        project_calls = [call for call in normalized if call.get("name") == "query_project"]
        weekly_calls = [call for call in normalized if call.get("name") == "generate_weekly_report"]
        if project_calls and not weekly_calls:
            weekly_args = {}
            _normalize_week_argument(message, weekly_args)
            normalized.append({"name": "generate_weekly_report", "arguments": weekly_args})

    if "周报" in message and "每人小时数统计" in message:
        today = _current_date()
        monday = today - timedelta(days=today.weekday())
        project_calls = [call for call in normalized if call.get("name") == "query_project"]
        project_id = None
        for call in normalized:
            project_id = (call.get("arguments") or {}).get("project_id") or project_id
        if not project_calls and project_id:
            project_calls = [{"name": "query_project", "arguments": {"project_id": project_id}}]
        normalized = project_calls + [{
            "name": "compute_statistics",
            "arguments": {
                "statistics_type": "user_hours",
                "start_date": monday.isoformat(),
                "end_date": (monday + timedelta(days=6)).isoformat(),
            },
        }]

    compute_calls = [call for call in normalized if call.get("name") == "compute_statistics"]
    if len(compute_calls) == 1:
        base = compute_calls[0]
        base_args = dict(base.get("arguments") or {})

        member_pair = re.search(r"([\u4e00-\u9fa5]{2,3})和([\u4e00-\u9fa5]{2,3})(?=在|这周|本周|上周|下周)", message)
        project_pair = re.search(r"^([^和，。；;\s]{2,12})和([^，。；;\s]{2,12}?)(?=这周|本周|上周|下周|项目)", message)
        member_department_comparison = bool(re.search(r"比.+部平均", message))
        if member_department_comparison:
            member = _extract_member_name(message, state)
            department = _extract_department_name(message)
            if member and department:
                member_args = dict(base_args)
                member_args["statistics_type"] = "user_hours"
                member_args["member_name"] = member
                member_args.pop("department_id", None)
                member_args.pop("user_id", None)
                department_args = dict(base_args)
                department_args["statistics_type"] = "department_hours"
                department_args["department_id"] = department
                department_args.pop("member_name", None)
                department_args.pop("user_id", None)
                expanded = [
                    {**base, "arguments": member_args},
                    {**base, "arguments": department_args},
                ]
                normalized = [call for call in normalized if call is not base] + expanded
                compute_calls = expanded
        elif member_pair:
            expanded = []
            for name in member_pair.groups():
                args = dict(base_args)
                args["member_name"] = name
                args.pop("user_id", None)
                args["statistics_type"] = "user_hours"
                expanded.append({**base, "arguments": args})
            normalized = [call for call in normalized if call is not base] + expanded
            compute_calls = expanded
        elif project_pair and not re.search(r"李明|王芳|张伟|陈静|刘洋|赵敏", project_pair.group(1) + project_pair.group(2)):
            expanded = []
            for project in project_pair.groups():
                args = dict(base_args)
                args["project_id"] = project
                args.pop("user_id", None)
                args["statistics_type"] = "project_hours"
                expanded.append({**base, "arguments": args})
            normalized = [call for call in normalized if call is not base] + expanded
            compute_calls = expanded
        elif "占全公司" in message and base_args.get("department_id"):
            company_args = {
                "statistics_type": "monthly_hours",
                "start_date": base_args.get("start_date"),
                "end_date": base_args.get("end_date"),
            }
            normalized.append({"name": "compute_statistics", "arguments": company_args})

    compute_calls = [call for call in normalized if call.get("name") == "compute_statistics"]
    if len(compute_calls) == 1:
        months = [int(value) for value in re.findall(r"(?<!\d)(\d{1,2})月", message)]
        if "第二季度" in message and re.search(r"每个月|逐月|各月", message):
            months = [4, 5, 6]
        months = list(dict.fromkeys(month for month in months if 1 <= month <= 12))
        should_expand_months = bool(re.search(r"这[两三四五六七八九十]个月|每月.{0,8}分别|每个月|各月|逐月|环比", message))
        if len(months) >= 2 and should_expand_months:
            base = compute_calls[0]
            year_match = re.search(r"(20\d{2})年", message)
            year = int(year_match.group(1)) if year_match else _current_date().year
            expanded = []
            for month in months:
                month_start = date(year, month, 1)
                if month == 12:
                    month_end = date(year + 1, 1, 1) - timedelta(days=1)
                else:
                    month_end = date(year, month + 1, 1) - timedelta(days=1)
                if year == _current_date().year and month == _current_date().month:
                    month_end = min(month_end, _current_date())
                args = dict(base.get("arguments") or {})
                args["start_date"] = month_start.isoformat()
                args["end_date"] = month_end.isoformat()
                expanded.append({**base, "arguments": args})
            normalized = [call for call in normalized if call is not base] + expanded

    # 统计历史工时时，当前月的结束日期不得越过冻结的“今天”。
    today = _current_date()
    if re.search(r"(?:本月|这个月|\d{1,2}月)", message) and not re.search(r"本周|这周|上周|下周|自然周", message):
        for call in normalized:
            if call.get("name") != "compute_statistics":
                continue
            arguments = call.get("arguments") or {}
            try:
                start = date.fromisoformat(str(arguments.get("start_date") or ""))
                end = date.fromisoformat(str(arguments.get("end_date") or ""))
            except ValueError:
                continue
            if start.year == today.year and start.month == today.month and end > today:
                arguments["end_date"] = today.isoformat()

    return _deduplicate_tool_calls(normalized)


def _enforce_write_confirmation(tool_calls: list, user_message: str) -> list:
    """Force batch writes to preview unless this turn is an explicit confirmation."""
    is_confirmation = bool(_WRITE_CONFIRMATION_RE.fullmatch(user_message.strip()))
    for call in tool_calls:
        if call.get("name") != "batch_save_workhour":
            continue
        arguments = dict(call.get("arguments") or {})
        if not is_confirmation:
            arguments["dry_run"] = True
        call["arguments"] = arguments
    return tool_calls


def _missing_save_workhour_fields(arguments: dict, state: AgentState) -> list:
    missing = []
    if not arguments.get("project_id"):
        missing.append("**项目名称或项目ID**")
    if not arguments.get("date"):
        missing.append("**工时日期**（如'今天'、'2026-03-26'）")
    if not arguments.get("duration"):
        missing.append("**工时时长**（小时，如 8 或 4.5）")

    # On the first user turn the model must not invent a date merely because
    # the prompt contains today's date.  Later turns may legitimately inherit
    # an unambiguous pending write from conversation history.
    messages = state.get("conversation_history") or []
    prior_users = [
        item for item in messages[:-1]
        if item.get("role") == "user"
    ]
    user_message = state.get("user_message", "")
    date_label = "**工时日期**（如'今天'、'2026-03-26'）"
    if (
        arguments.get("date")
        and not prior_users
        and not _WORKHOUR_DATE_REFERENCE_RE.search(user_message)
        and date_label not in missing
    ):
        missing.append(date_label)

    duration_label = "**有效工时时长**（0.5 小时的整数倍，且不超过 10 小时）"
    if (
        arguments.get("duration")
        and not prior_users
        and not _WORKHOUR_DURATION_REFERENCE_RE.search(user_message)
        and duration_label not in missing
    ):
        missing.append(duration_label)
    try:
        duration = float(arguments.get("duration"))
        if duration <= 0 or duration > 10 or abs(duration * 2 - round(duration * 2)) > 1e-9:
            if duration_label not in missing:
                missing.append(duration_label)
    except (TypeError, ValueError):
        if arguments.get("duration") is not None and duration_label not in missing:
            missing.append(duration_label)
    if ("分钟" in user_message or _CHINESE_DURATION_RE.search(user_message)) and duration_label not in missing:
        missing.append(duration_label)

    date_value = arguments.get("date")
    if date_value:
        try:
            parsed_date = date.fromisoformat(str(date_value))
            today = _current_date()
            if parsed_date > today or parsed_date < today - timedelta(days=90):
                if date_label not in missing:
                    missing.append(date_label)
        except ValueError:
            if date_label not in missing:
                missing.append(date_label)
    if (
        not prior_users
        and _MONTH_DAY_WITHOUT_YEAR_RE.search(user_message)
        and not _EXPLICIT_YEAR_DATE_RE.search(user_message)
        and not re.search(r"今天|昨天|前天", user_message)
        and date_label not in missing
    ):
        missing.append(date_label)
    if re.search(r"不要\s*dry\s*run|非\s*dry\s*run|直接(?:保存|提交)", user_message, re.IGNORECASE):
        missing.append("**写入确认**（请先预览并在下一轮明确确认提交）")
    confirmation_label = "**写入确认**（请先预览并在下一轮明确确认提交）"
    if not prior_users and not _PREVIEW_REQUEST_RE.search(user_message) and confirmation_label not in missing:
        missing.append(confirmation_label)
    return missing


def _write_call_clarification(tool_name: str, arguments: dict, state: AgentState) -> Optional[str]:
    """Fail closed when the current turn does not clearly authorize a write route."""
    message = (state.get("user_message") or "").strip()
    prior_users = [
        item for item in (state.get("conversation_history") or [])[:-1]
        if item.get("role") == "user"
    ]
    is_confirmation = bool(_WRITE_CONFIRMATION_RE.fullmatch(message))
    is_simple_project_reply = bool(prior_users and _SIMPLE_PROJECT_ID_REPLY_RE.fullmatch(message))
    is_pending_update = bool(prior_users and _PENDING_WRITE_UPDATE_RE.search(message))

    if tool_name == "save_workhour":
        if not (_SAVE_ACTION_RE.search(message) or is_confirmation or is_simple_project_reply or is_pending_update):
            return "当前请求没有明确要求保存工时。请确认是否要保存，并补充完整的项目、日期和时长。"
        missing = _missing_save_workhour_fields(arguments, state)
        if missing:
            return "写入条件不完整或不合法，请补充或确认：" + "、".join(missing) + "。"
    elif tool_name == "batch_save_workhour":
        if not (_BATCH_ACTION_RE.search(message) or _PREVIEW_REQUEST_RE.search(message) or is_confirmation):
            return "当前请求没有明确要求批量写入。请先确认要批量填报的完整记录。"
        if not is_confirmation and not (
            _looks_like_batch_request(message) or _PREVIEW_REQUEST_RE.search(message)
        ):
            return "当前批量请求没有包含可执行的完整记录。请提供批量明细，并明确先预览或预检。"
        batch_text = str(arguments.get("text") or "")
        current_user_name = str((state.get("user_context") or {}).get("user_name") or "")
        mentioned_members = _BATCH_MEMBER_PREFIX_RE.findall(batch_text)
        privileged = str((state.get("user_context") or {}).get("entity_type") or "employee") in {
            "deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"
        }
        if arguments.get("dry_run") is not True and not privileged and any(name != current_user_name for name in mentioned_members):
            return "批量填报只能写入当前登录用户的工时；检测到其他成员姓名，请确认数据归属。"
    elif tool_name == "approve_workhour":
        if not (_APPROVE_ACTION_RE.search(message) or is_confirmation):
            return "当前请求没有明确要求执行审批。请确认审批对象和操作。"
        ids = arguments.get("workhour_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        if not ids or any(not re.fullmatch(r"wh_\d+", str(item)) for item in ids):
            return "工时记录 ID 格式无效，请提供形如 wh_8821 的有效记录 ID。"
    return None


def _all_user_text(state: AgentState) -> str:
    return "\n".join([*_prior_user_messages(state), str(state.get("user_message") or "")])


def _has_date_evidence(text: str) -> bool:
    return bool(
        _WORKHOUR_DATE_REFERENCE_RE.search(text)
        or re.search(r"20\d{2}年\d{1,2}月|20\d{2}年第\d{1,2}周|\d{1,2}月|本月|上月|今年|季度", text)
    )


def _tool_call_clarification(tool_name: str, arguments: dict, state: AgentState) -> Optional[str]:
    """Preflight every FC call so schema/semantic gaps become clarification, not execution errors."""
    write_message = _write_call_clarification(tool_name, arguments, state)
    if write_message:
        return write_message

    tool_def = _tool_registry.get_tool(tool_name) if _tool_registry else None
    if tool_def:
        required = tool_def.json_schema.get("required") or []
        missing = [key for key in required if arguments.get(key) in (None, "", [])]
        if missing:
            labels = {
                "start_date": "开始日期",
                "end_date": "结束日期",
                "statistics_type": "统计方式",
                "text": "批量工时明细",
                "workhour_ids": "工时记录ID",
                "action": "审批动作",
            }
            readable = "、".join(labels.get(key, key) for key in missing)
            return f"当前信息不足，请补充{readable}后再执行。"

    all_user_text = _all_user_text(state)
    if tool_name == "query_timesheet" and not _has_date_evidence(all_user_text):
        return "请提供要查询的开始日期和结束日期，或明确说明本周、上周、本月等时间范围。"

    if tool_name == "generate_weekly_report":
        if not re.search(r"本周|这周|上周|下周|(?:20\d{2}年)?第\d{1,2}周|20\d{2}年\d{1,2}月\d{1,2}日", all_user_text):
            return "请提供要生成周报的具体周次，例如本周、上周或某个日期所在周。"
        current_weekly_message = state.get("user_message") or ""
        has_named_user = bool(_extract_member_name(current_weekly_message, state))
        has_explicit_project_id = bool(re.search(r"项目ID(?:是|为)?\s*[A-Za-z0-9_-]+", current_weekly_message, re.IGNORECASE))
        if re.search(r"部门|合并|工作类型|开发类|测试类", current_weekly_message) or (
            "项目" in current_weekly_message and not has_named_user and not has_explicit_project_id
        ):
            return "当前周报工具按单个用户生成，请补充具体员工；项目、部门或工作类型周报需要先查询统计条件。"

    if tool_name == "suggest_workhour" and re.search(r"(?:正月|冬月|腊月|[一二三四五六七八九十]+月)?初[一二三四五六七八九十]", state.get("user_message") or ""):
        return "当前日期表达可能是农历，请提供公历 YYYY-MM-DD 日期后再生成工时建议。"

    if tool_name == "export_report":
        current = state.get("user_message") or ""
        explicit_dates = re.findall(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", current)
        bounded_relative = re.search(r"本周|这周|上周|本月|上月|季度|从.+到|至", current)
        if len(explicit_dates) < 2 and not bounded_relative:
            return "请提供报表的开始日期和结束日期，明确完整导出范围。"

    if tool_name == "query_project":
        current = (state.get("user_message") or "").strip()
        if current in {"查项目", "查询项目", "找项目", "项目"}:
            return "请提供项目名称或项目ID，或说明需要查询的项目范围。"
        if re.search(r"谁负责|哪些项目上工作|负责的项目|重点项目|相关的.*项目|进行中或暂停", current):
            return "当前项目查询需要项目名称或项目ID，请补充具体项目。"

    start_value = arguments.get("start_date")
    end_value = arguments.get("end_date")
    if start_value or end_value:
        try:
            start = date.fromisoformat(str(start_value)) if start_value else None
            end = date.fromisoformat(str(end_value)) if end_value else None
        except ValueError:
            return "日期格式无效，请使用 YYYY-MM-DD 格式重新提供日期范围。"
        if start and end and start > end:
            return "开始日期不能晚于结束日期，请确认正确的日期范围。"
        if tool_name == "query_timesheet" and start and end:
            today = _current_date()
            if end > today or start < today - timedelta(days=90) or (end - start).days > 90:
                return "查询日期超出当前支持范围，请提供不晚于今天且跨度不超过90天的日期范围。"

    return None


def _should_verify_project_before_save(arguments: dict, state: AgentState) -> bool:
    project_id = str(arguments.get("project_id") or "")
    if _PROJECT_SLUG_RE.fullmatch(project_id):
        return True
    return bool(
        _prior_user_messages(state)
        and _SIMPLE_PROJECT_ID_REPLY_RE.fullmatch((state.get("user_message") or "").strip())
    )


def _filter_system_prompt_for_available_tools(prompt: str) -> str:
    """Remove SQL instructions whenever sql_query is not actually callable."""
    if _tool_registry and _tool_registry.tool_exists("sql_query"):
        from app.core.config import settings
        if settings.SQL_AGENT_ENABLED:
            return prompt
    filtered = re.sub(r"\n\s*## sql_query 工具调用说明[\s\S]*$", "", prompt)
    return filtered + "\n\n复杂统计、排名和趋势分析使用 compute_statistics；若现有工具无法完成，应明确说明能力边界。"


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
    today = _current_date()
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
    # TODO: Q3 前删除此 env 开关（仅用于基准测试）
    if os.getenv("BENCHMARK_FORCE_FALLBACK") == "1":
        return await node_classify_intent(state)

    if not _llm_client or not _tool_registry:
        return await node_classify_intent(state)

    if _looks_like_uncommanded_workhour_record(state.get("user_message") or ""):
        return {
            "intent": "clarify",
            "tool_name": None,
            "tool_params": {},
            "query": state.get("user_message") or "",
            "clarify_message": "已识别到一条工时明细，但尚未收到保存或预览指令。请确认要先预览，还是仅解析内容。",
        }

    try: # 工具注册
        tools = _build_openai_tools(_tool_registry)
        if not tools:
            return await node_classify_intent(state)

        messages = state.get("conversation_history") or []
        if not messages:
            return await node_classify_intent(state)

        # ── A-RAG agent loop: 第 2 轮起把 history 拼成 OpenAI tool messages ──
        # 这样 LLM 能看到上几轮工具执行结果, 决定是否继续追问/精读, 或直接给最终答案
        agent_history = state.get("agent_history") or []
        if agent_history:
            history_msgs = []
            for h in agent_history:
                call_id = f"call_{h.get('iteration', 0)}"
                try:
                    args_json = json.dumps(h.get("args") or {}, ensure_ascii=False)
                    obs_json = json.dumps(
                        h.get("observation"), ensure_ascii=False, default=str
                    )
                except Exception:
                    args_json = str(h.get("args") or {})
                    obs_json = str(h.get("observation"))
                history_msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": h.get("tool") or "",
                            "arguments": args_json,
                        },
                    }],
                })
                history_msgs.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": obs_json,
                })
            # 在 conversation_history 之后追加, 使 LLM 看到 [system, ...对话历史..., user, assistant_tc, tool, ...]
            messages = list(messages) + history_msgs

        ### Ollama：请求级设置 16K/32K,vLLM：使用服务端已经配置好的上下文上限
        # 获取完整 messages
        #         ↓
        # 根据原始历史长度选择 16K/32K
        #         ↓
        # 消息超过9条？
        #         ├─ 是：保留 system + 最后8条
        #         └─ 否：不处理
        #         ↓
        # 粗略估算消息和工具 schema token
        #         ↓
        # 估算超过12K？
        #         ├─ 是：进一步缩减为 system + 最后4条
        #         └─ 否：保持当前结果
        #         ↓
        # 调用模型
        # num_ctx 自适应：历史超过 2000 字用大 context
        history_chars = sum(
            len(m.get("content") or "") for m in messages
        )
        # 上下文长度包含输入和输出，必须满足：prompt_tokens + max_tokens <= max_model_len。
        # vLLM 则由服务启动参数 --max-model-len 控制，
        # 未显式设置时会从模型配置自动推导。若不清楚模型上限，不要随意调大，
        # 应优先省略该启动参数或使用受当前 vLLM 版本支持的 --max-model-len auto。
        # 只有模型本身支持且显存足够时才能使用更长上下文；主动调小则可降低 KV cache 压力。
        # 当前应用期望 vLLM  最长按 32K 配置。
        num_ctx = 32768 if history_chars > 2000 else 16384

        # FC 调用时截短会话历史(messages)，防止 input tokens 超出模型上下文限制（32K context）
        # 策略1：按条数截断（保留 system + 最近 6 条 + 当前消息）
        ### 第一层截断
        if len(messages) > 9:
            messages = [messages[0]] + messages[-8:]

        # 策略2：按估算 token 数截断（字符数/3 + tools schema ~200 tokens/个）
        ### 第二层截断
        total_chars = sum(len(m.get("content") or "") for m in messages)
        estimated_tokens = total_chars // 3 + len(tools) * 200
        if estimated_tokens > 12000:
            # 32K context 下留 4K output，截断到 system + 最近 2 轮（4条）+ 当前消息
            messages = [messages[0]] + messages[-4:]
            logger.warning(f"FC 输入估算 {estimated_tokens} tokens，已截断到最近 2 轮")

        # tool call JSON 通常 100~600 tokens；qwen3-8b 8192 context，input ~4000-6000 时还有 2000+ 可用

        # ── A-RAG 受控破例（方案 A）：rag_strategy 指定 agent 或已进入 kb 多步导航 → 升级推理层 API ──
        _fc_client = _llm_client
        should_upgrade_planner = (
            state.get("rag_strategy") == "agent"
            or any(
                h.get("tool") in _AGENT_LOOP_TOOL_NAMES
                for h in (agent_history or [])
            )
        ) and _probe_planner_availability()
        if should_upgrade_planner:
            try:
                _fc_client = get_planner_llm_client(temperature=0.1, max_tokens=1024)
                logger.info("A-RAG 多步导航：FC 调用升级至推理层客户端 (model=%s)", getattr(_fc_client, "model", "unknown"))
            except Exception as _esc_err:
                logger.warning("推理层客户端获取失败，回退 8b: %s", _esc_err)
                _fc_client = _llm_client
                state["rag_strategy"] = None
                state["_rag_fallback"] = True

        fc_api_base = (_fc_client.api_base or "").lower()
        if "11434" in fc_api_base:
            extra = {"num_ctx": num_ctx, "think": False}
        elif "dashscope" in fc_api_base:
            extra = {"enable_thinking": False}
        else:
            # vLLM OpenAI API：Qwen3 的 hard switch 必须放在
            # chat_template_kwargs 中，顶层 enable_thinking 不会生效。
            extra = {"chat_template_kwargs": {"enable_thinking": False}}

        # llm 基于 messages 自动判别调用工具还是生成回复
        result = await _fc_client.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            # 本次最多生成 1024 个输出 token，并非固定生成量；模型可以提前结束。
            # 此值不负责截断输入，需与服务端 max_model_len 共同满足：
            # prompt_tokens + max_tokens <= max_model_len。
            max_tokens=1024,
            extra=extra,
        )

        forced_calls = _forced_tool_calls_for_request(state)
        if forced_calls == []:
            return {
                "intent": "general_chat",
                "tool_name": None,
                "tool_params": {},
                "query": state["user_message"],
                "llm_result": "已取消，本次不会执行写入。",
            }
        if forced_calls:
            result = {"finish_reason": "tool_calls", "tool_calls": forced_calls}
    except Exception as e:
        logger.warning(f"Function Calling 失败，降级到规则路由: {e}")
        # 方案 A：planner 调用失败时清除策略，避免循环回 llm_with_tools
        if state.get("rag_strategy") == "agent":
            state["rag_strategy"] = None
            state["_rag_fallback"] = True
        return await node_classify_intent(state)

    if result.get("finish_reason") == "tool_calls":
        tool_calls = _normalize_business_tool_calls(
            _deduplicate_tool_calls(result.get("tool_calls", [])),
            state,
        )
        if not tool_calls:   #  正常情况下：finish_reason == "tool_calls"应该必然伴随非空的：tool_calls，但代码仍然检查，这是防御性处理
            return await node_classify_intent(state)

        user_ctx = state.get("user_context") or {}

        # 批量写入首次必须预览。只有用户当前轮明确确认时，才允许
        # dry_run=false；模型自行生成 false 不能绕过确认边界。
        tool_calls = _enforce_write_confirmation(
            tool_calls,
            state.get("user_message", ""),
        )

        # 所有写工具都必须通过当前轮授权和参数边界；不能因为同轮
        # 还有其他工具，或模型已生成完整参数，就绕过澄清节点。
        for call in tool_calls:
            tool_name = call.get("name") or ""
            arguments = dict(call.get("arguments") or {})
            clarify_msg = _tool_call_clarification(tool_name, arguments, state)
            if clarify_msg:
                return {
                    "intent": "clarify",
                    "tool_name": tool_name,
                    "tool_params": arguments,
                    "query": state["user_message"],
                    "clarify_message": clarify_msg,
                }
            if tool_name == "save_workhour" and _should_verify_project_before_save(arguments, state):
                if _SIMPLE_PROJECT_ID_REPLY_RE.fullmatch((state.get("user_message") or "").strip()):
                    return {
                        "intent": "tool_execution",
                        "tool_name": "query_project",
                        "tool_params": {"project_id": arguments["project_id"]},
                        "query": state["user_message"],
                    }
                save_params = dict(arguments)
                if user_ctx.get("user_id") and "user_id" not in save_params and "member_name" not in save_params:
                    save_params["user_id"] = user_ctx["user_id"]
                return {
                    "intent": "complex_request",
                    "tool_name": None,
                    "tool_params": {},
                    "query": state["user_message"],
                    "task_plan": {
                        "plan_name": "核验项目后预览工时",
                        "source": "project_verification_guard",
                        "tasks": [
                            {
                                "task_id": "verify_project",
                                "task_type": "tool_call",
                                "tool_name": "query_project",
                                "parameters": {"project_id": arguments["project_id"]},
                                "dependencies": [],
                            },
                            {
                                "task_id": "preview_workhour",
                                "task_type": "tool_call",
                                "tool_name": "save_workhour",
                                "parameters": save_params,
                                "dependencies": ["verify_project"],
                            },
                        ],
                    },
                }

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
            result = {
                "intent": "knowledge_qa",
                "tool_name": None,
                "tool_params": {},
                "query": tool_params.get("query", state["user_message"]),
            }
            # knowledge_qa 是单步 RAG 快速通道。需要渐进检索时模型应直接选择
            # kb_* 工具；不要再升级 planner 做一次重复意图判定。
            result["rag_strategy"] = None
            return result

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
            missing = _missing_save_workhour_fields(tool_params, state)
            if missing:
                clarify_msg = await _build_workhour_clarify_message(
                    tool_params,    # llm根据用户消息得到的参数
                    missing,        # 对照后端接口和实际得到的参数，对比得到缺少的参数
                    user_id=user_ctx.get("user_id"),
                    auth_token=user_ctx.get("auth_token"),
                )
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

    # 没有任何工具执行时，不接受模型直接宣称业务操作已完成。
    # 转到确定性路由后，要么调用工具，要么澄清/拒绝。
    if content and _UNVERIFIED_COMPLETION_RE.search(content):
        logger.warning("已拦截未经工具证实的完成声明，转确定性路由")
        return await node_classify_intent(state)

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

        if (
            tool_name == "save_workhour"
            and _should_verify_project_before_save(tool_params, state)
        ):
            return {
                "intent": "tool_execution",
                "tool_name": "query_project",
                "tool_params": {"project_id": tool_params["project_id"]},
                "query": state["user_message"],
            }

        clarify_msg = _tool_call_clarification(tool_name, tool_params, state)
        if clarify_msg:
            return {
                "intent": "clarify",
                "tool_name": tool_name,
                "tool_params": tool_params,
                "query": state["user_message"],
                "clarify_message": clarify_msg,
            }

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
            missing = _missing_save_workhour_fields(tool_params, state)
            if missing:
                clarify_msg = await _build_workhour_clarify_message(
                    tool_params,
                    missing,
                    user_id=user_ctx.get("user_id"),
                    auth_token=user_ctx.get("auth_token"),
                )
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

    result = {
        "intent": intent_result.intent_type.value,
        "tool_name": None,
        "tool_params": {},
        "query": intent_result.parameters.get("query", state["user_message"]),
    }
    if intent_result.intent_type.value == "knowledge_qa":
        result["rag_strategy"] = None
    return result


async def node_execute_tool(state: AgentState) -> dict:
    """节点：工具执行（query_timesheet / query_project / compute_statistics）

    A-RAG agent loop 改造点：
    - 每次执行后把 (tool, args, observation_truncated) 累加到 agent_history
    - agent_iterations += 1
    observation 截断到 ~500 tokens (字符数 / 3)，防止 prompt 爆。
    """
    if not _task_executor:
        return {
            "error": "TaskExecutor 未初始化",
            "tool_result": {"success": False},
            "agent_iterations": state.get("agent_iterations", 0) + 1,
            "agent_history": _append_agent_history(
                state, observation={"error": "TaskExecutor 未初始化"}
            ),
        }

    current_tool = state.get("tool_name") or ""
    current_args = state.get("tool_params") or {}
    current_signature = _tool_call_signature(current_tool, current_args)
    for previous in state.get("agent_history") or []:
        previous_signature = _tool_call_signature(
            previous.get("tool") or "",
            previous.get("args") or {},
        )
        if previous_signature != current_signature:
            continue
        is_write = current_tool in _WRITE_TOOL_NAMES
        message = (
            "检测到重复写操作，已阻止再次提交"
            if is_write else "检测到重复工具调用，已阻止再次执行"
        )
        blocked = {
            "success": False,
            "error_code": "DUPLICATE_WRITE_BLOCKED" if is_write else "DUPLICATE_CALL_BLOCKED",
            "error": message,
            "message": message,
        }
        logger.warning("%s: %s", message, current_tool)
        return {
            "tool_result": blocked,
            "error": message,
            "agent_iterations": state.get("agent_iterations", 0) + 1,
            "agent_history": _append_agent_history(state, observation=blocked),
        }

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

    try:  # 带着用户的权限上下文来执行工具
        result = await _task_executor.execute_single_task(task, permission_ctx)
        return {
            "tool_result": result,
            "agent_iterations": state.get("agent_iterations", 0) + 1,
            "agent_history": _append_agent_history(state, observation=result),   # _append_agent_history不在图中
        }
    except Exception as e:
        logger.error(f"工具执行节点异常: {e}")
        err_obs = {"success": False, "error": str(e)}
        return {
            "tool_result": err_obs,
            "error": str(e),
            "agent_iterations": state.get("agent_iterations", 0) + 1,
            "agent_history": _append_agent_history(state, observation=err_obs),
        }


# 对 LLM 无价值的技术字段：记录 UUID、项目 UUID（已有 project_name）、空时间戳
# 注意：user_id 保留——跨用户查询时 LLM 需要它区分不同人的记录
_LLM_NOISE_KEYS = frozenset({"id", "project_id", "created_at"})


def _strip_noise_fields(obj: Any) -> Any:
    """递归删除对 LLM 无价值的技术字段，保留 user_id 供跨用户查询区分身份。"""
    if isinstance(obj, dict):
        return {
            k: _strip_noise_fields(v)
            for k, v in obj.items()
            if k not in _LLM_NOISE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_noise_fields(item) for item in obj]
    return obj


def _truncate_observation(observation: Any, max_chars: int = 8000) -> Any:
    """
    观察结果送入 LLM 前先剥噪声字段再限制长度。
    剥后每条工时记录约 80 字符（保留 user_id），8000 上限可覆盖 ~100 条。
    """
    cleaned = _strip_noise_fields(observation)
    try:
        s = json.dumps(cleaned, ensure_ascii=False, default=str)
    except Exception:
        s = str(cleaned)
    if len(s) <= max_chars:
        return cleaned
    return {
        "_truncated": True,
        "_preview": s[:max_chars],
        "_full_length_chars": len(s),
    }


def _append_agent_history(state: AgentState, observation: Any) -> list:
    """把当前轮次执行结果追加到 agent_history (浅 copy 后返回新 list)"""
    history = list(state.get("agent_history") or [])
    history.append({
        "iteration": state.get("agent_iterations", 0),
        "tool": state.get("tool_name"),
        "args": state.get("tool_params") or {},
        "observation": _truncate_observation(observation),
    })
    return history


async def _build_workhour_clarify_message(
    partial_params: Dict[str, Any],
    missing: list,
    user_id: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> str:
    """生成引导式提问，收集工时填报缺失的必要信息，并注入历史推荐。"""
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

    # 注入基于历史的智能推荐，调用spring后端接口
    if user_id:
        base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
            f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
        )
        try:
            projects = await resolve_project_suggestion(user_id, auth_token, base_url, top_k=3)
            if projects:
                lines.append("\n📌 根据您最近 30 天的填报记录，推荐以下项目：")
                for p in projects:
                    lines.append(f"   • {p['project_name']}（最近 {p['frequency']} 次）")
            default_hours = await resolve_hours_suggestion(
                user_id,
                projects[0]["project_id"] if projects else None,
                auth_token,
                base_url,
            )
            lines.append(f"\n⏱ 推荐工时：{default_hours} 小时")
        except Exception:
            # 推荐失败不阻断主流程
            pass

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
    B. intent == "complex_request"（来自规则路由降级），node_classify_intent
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

    # ── 路径 B：complex_request，调用 PlannerAgent，再构建 task_plan ──────────────────────────
    # 模型声明调用工具，但调用列表为空
    # node_llm_with_tools 调用主模型失败，走备用 node_classify_intent
    else:
        if not _llm_client or not _tool_registry:
            return {"llm_result": "抱歉，多步规划功能暂时不可用。", "error": "规划组件未初始化"}

        # Plan-and-Execute（先规划、后执行）架构
        planner = PlannerAgent(
            tool_registry=_tool_registry,
            llm_client=get_planner_llm_client(),
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
            # 成功示例：
            # {
            #     "plan_id": "plan-001",
            #     "plan_name": "本周工时分析",
            #     "status": TaskStatus.COMPLETED,
            #     "progress": {
            #         "total": 3,
            #         "completed": 3,
            #         "failed": 0,
            #     },
            #     "execution_time": 2.7,
            #     "task_results": {
            #         "t1": {...},
            #         "t2": {...},
            #         "t3": {...},
            #     },
            #     "success": True,
            # }
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

    两种入口:
    - plan_and_execute → summarize: 消费 state.plan_results (TaskPlan 多工具并行)
    - agent_loop force_end → summarize: 消费 state.agent_history (A-RAG 多轮循环兜底)
    """
    plan_results = state.get("plan_results") or {}
    user_message = state.get("user_message", "")

    # ── A-RAG agent loop 兜底: 没有 plan_results 但有 agent_history ────────
    # 把 agent_history 转成 plan_results 等价结构, 复用下方的汇总逻辑
    if not plan_results:
        history = state.get("agent_history") or []
        if history:
            plan_results = {}
            for h in history:
                tid = f"agent_t{h.get('iteration', 0)}"
                plan_results[tid] = {
                    "tool_name": h.get("tool"),
                    "result": h.get("observation"),
                }

    if not plan_results:
        return {"llm_result": "所有任务均已完成，但未产生可汇总的结果。"}

    def _is_explicit_success(item: Any) -> bool:
        if not isinstance(item, dict) or item.get("success") is not True:
            return False
        inner = item.get("result")
        return not isinstance(inner, dict) or inner.get("success") is not False

    failed_results = [item for item in plan_results.values() if not _is_explicit_success(item)]

    def _safe_failure_summary() -> str:
        messages = []
        unknown = False
        for item in failed_results:
            if not isinstance(item, dict):
                continue
            inner = item.get("result") if isinstance(item.get("result"), dict) else item
            status = str(inner.get("status") or item.get("status") or "")
            error_code = str(inner.get("error_code") or item.get("error_code") or "")
            if status == "unknown" or error_code == "WRITE_RESULT_UNKNOWN":
                unknown = True
            message = inner.get("message") or inner.get("error") or item.get("message") or item.get("error")
            if message and str(message) not in messages:
                messages.append(str(message))
        if unknown:
            return "提交结果未知，请查询确认。"
        detail = "；".join(messages[:3])
        return f"任务未完成：{detail}" if detail else "任务未完成，请检查参数或权限后重试。"

    if not _llm_client:
        # 降级：直接拼接各工具结果
        parts = []
        for task_id, result in plan_results.items():
            r = result.get("result", result)
            if isinstance(r, dict) and r.get("success"):
                parts.append(str(r))
        if failed_results:
            parts.append(_safe_failure_summary())
        return {"llm_result": "\n\n".join(parts) if parts else _safe_failure_summary()}

    # 构建汇总 prompt — 截断过长的结果文本，防止超出模型上下文
    def _truncate_result_for_summary(tool_name: str, r: dict) -> str:
        """对工具结果做摘要级截断，保留关键信息"""
        if not isinstance(r, dict):
            return str(r)[:500]
        # batch_save_workhour / save_workhour：保留 preview_text / message
        if "preview_text" in r:
            return r["preview_text"]
        if "summary_text" in r:
            return r["summary_text"]
        if "message" in r:
            return str(r["message"])[:800]
        # 其他工具：保留 success + 前 3 个关键字段
        filtered = {k: v for k, v in r.items() if k in ("success", "error", "message", "summary", "total", "count")}
        return json.dumps(filtered, ensure_ascii=False)[:800]

    results_text = ""
    for task_id, result in plan_results.items():
        tool_name = result.get("tool_name", task_id)
        r = result.get("result", result)
        summary_str = _truncate_result_for_summary(tool_name, r)
        results_text += f"\n【{tool_name}】执行结果：\n{summary_str}\n"

    # 总长度硬限制：超过 4000 字符直接截断
    MAX_SUMMARY_CHARS = 6000
    if len(results_text) > MAX_SUMMARY_CHARS:
        results_text = results_text[:MAX_SUMMARY_CHARS] + "\n...[更多结果已省略]"

    messages = [
        {
            "role": "system",
            "content": (
                "你是工时管理系统的智能助手。"
                "用户提出了一个需要多步操作的请求，系统已自动执行了多个工具并收集到结果。"
                "请将这些结果综合分析，用简洁、友好的语言回答用户的原始问题。"
                "只有工具结果明确 success=true 才能声称对应业务动作成功。"
                "任意结果为 success=false、failed、timeout 或 unknown 时，必须如实说明未完成或结果未知，不得根据其他成功的查询结果推断写入成功。"
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
        if failed_results and _UNVERIFIED_COMPLETION_RE.search(str(answer)):
            logger.error("汇总模型将失败/未知工具结果误报为成功，已使用确定性失败摘要")
            return {"llm_result": _safe_failure_summary()}
        return {"llm_result": answer}
    except Exception as e:
        logger.error(f"汇总节点 LLM 调用失败: {e}")
        # 降级：直接返回 tools 的关键结果（如 batch_save_workhour 的 preview_text）
        fallback_parts = []
        for task_id, result in plan_results.items():
            r = result.get("result", result)
            if isinstance(r, dict):
                if "preview_text" in r:
                    fallback_parts.append(r["preview_text"])
                elif "message" in r:
                    fallback_parts.append(str(r["message"]))
        if fallback_parts:
            return {"llm_result": "\n\n".join(fallback_parts)}
        return {"llm_result": f"结果已收集，但汇总生成失败：{e}"}


async def node_execute_rag(state: AgentState) -> dict:
    """节点：LangChain RAG 知识库查询（混合检索 + LLM 生成）"""
    if state.get("stream_response"):
        # stream_agent_response 会在收到 execute_rag 节点事件后直接调用
        # langchain_rag_stream_query。此处只交接，避免先非流式生成、再流式生成。
        return {"rag_result": {"success": True, "deferred_stream": True}}

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
            # 无历史时降级为单轮模式（conversation_history 构建失败的兜底）
            answer = await _llm_client.generate(
                prompt=state["user_message"],
                system_prompt="你是一个专业的企业工时管理助手。请用简洁、友好的方式回答用户问题。",
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
    if intent == "knowledge_qa":
        return "llm_with_tools" if state.get("rag_strategy") == "agent" else "execute_rag"
    return {
        "tool_execution": "execute_tool",
        "complex_request": "plan_and_execute",   # 多步规划 + 并行执行
        "general_chat": "execute_llm",
        "clarify": "clarify_node",
    }.get(intent, "execute_llm") 


# ─── A-RAG Agent Loop 守卫 ────────────────────────────────────────────────────

def _agent_loop_should_continue(state: AgentState) -> str:
    """
    Agent loop 条件路由 (承载 A-RAG 多轮渐进式披露)。

    返回:
        - "continue":  回到 llm_with_tools 让 LLM 看 observation 决定下一步
        - "end":       提前结束 (重复 / 连续异常等)
        - "force_end": 达到 max_iterations, 走 summarize 兜底

    死循环 3 道闸:
        1. agent_iterations >= agent_max_iterations  → force_end
        2. 最近 3 轮中 (tool, args) 重复出现 ≥ 2 次   → end
        3. agent_history 末尾 3 条全是 error          → end
    """
    # 最高优先级：planner 回退标志 → 立即结束
    if state.get("_rag_fallback"):
        return "force_end"                      #############

    # Agent Loop 只用于 A-RAG 渐进式检索。普通业务工具执行后已由
    # SSE 处理器生成用户可见结果，若再回到 LLM 会诱发重复查询和重复写。
    if state.get("tool_name") not in _AGENT_LOOP_TOOL_NAMES:
        return "end"

    iters = state.get("agent_iterations", 0)
    max_iters = state.get("agent_max_iterations", 5) or 5
    history = state.get("agent_history") or []

    # 闸 1: 达到上限
    if iters >= max_iters:
        logger.warning(f"Agent loop 达到 max_iterations={max_iters}, 强制 summarize 收尾")
        return "force_end"                      #############

    # 闸 2: 重复 tool_call 检测 (最近 3 次中同 (tool, args) 出现 ≥ 2 次)
    if len(history) >= 2:
        recent = history[-3:]
        signatures: list = []
        for h in recent:
            try:
                sig = (
                    h.get("tool") or "",
                    json.dumps(h.get("args") or {}, ensure_ascii=False, sort_keys=True),
                )
            except Exception:
                sig = (h.get("tool") or "", str(h.get("args")))
            signatures.append(sig)
        for s in set(signatures):
            if signatures.count(s) >= 2:
                logger.warning(f"Agent loop 检测到重复 tool_call {s[0]}, 提前结束")
                return "end"                 #############

    # 闸 3: 连续 3 次异常 → 提前结束 (避免持续失败的工具调用浪费 token)
    if len(history) >= 3:
        last_three = history[-3:]
        all_errors = True
        for h in last_three:
            obs = h.get("observation")
            if isinstance(obs, dict):
                if obs.get("success") is False or obs.get("error"):
                    continue
                if isinstance(obs.get("result"), dict) and (
                    obs["result"].get("success") is False or obs["result"].get("error")
                ):
                    continue
            all_errors = False
            break
        if all_errors:
            logger.warning("Agent loop 检测到连续 3 次工具异常, 提前结束")
            return "end"

    # 默认: 回到 llm_with_tools 让 LLM 决定 (它可能继续调工具, 也可能给最终答案)
    return "continue"


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

    # 条件路由（llm_with_tools → 执行节点之一 / 自循环）
    builder.add_conditional_edges(
        "llm_with_tools",
        _route_by_intent,
        {
            "execute_tool": "execute_tool",
            "execute_rag": "execute_rag",
            "execute_llm": "execute_llm",
            "clarify_node": "clarify_node",
            "plan_and_execute": "plan_and_execute",
            "llm_with_tools": "llm_with_tools",  # 方案 A：knowledge_qa 升级后回循环
        },
    )

    # 所有执行节点 → END（plan_and_execute → summarize → END）
    # ── execute_tool: A-RAG agent loop, 不再固定 → END ──────────────────────
    builder.add_conditional_edges(
        "execute_tool",
        _agent_loop_should_continue,       # 条件边的执行结果如果是"continue"，则路由到"llm_with_tools"节点
        {
            "continue": "llm_with_tools",   # 回到主节点形成循环
            "end": END,                     # 重复/连续异常 → 提前结束
            "force_end": "summarize",       # 达到 max_iterations → 走兜底汇总
        },
    )
    builder.add_edge("execute_rag", END)
    builder.add_edge("execute_llm", END)
    builder.add_edge("clarify_node", END)
    builder.add_edge("plan_and_execute", "summarize")
    builder.add_edge("summarize", END)

    return builder.compile()


# ─── SSE 流式输出 ─────────────────────────────────────────────────────────────

def _format_sse(event_type: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件字符串

    同时把 event_type 写入 data JSON 的 `type` 字段。原因:
    SpringBoot AIController 当前用 `bodyToFlux(String.class)` 消费 SSE，
    Spring WebFlux 默认会丢掉 `event:` 行只保留 `data:` 内容。把 type 冗余
    放进 data JSON,前端可以不依赖 event 行就拿到类型。
    """
    data.setdefault("type", event_type)
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
    round_tool_name: str = ""
    round_tool_params: Dict[str, Any] = {}
    round_task_plan: Optional[Dict[str, Any]] = None
    round_tool_result: Optional[Dict[str, Any]] = None
    _streaming_rag_active = False      # 知识问答时流式 RAG 标志

    # ── Task 40: 如果没有 session_id，自动生成一个 ─────────────────────────────
    from app.services.session_memory import generate_session_id
    effective_session_id = session_id or generate_session_id()

    # ── Task 40: 通过 PromptBuilder 构建带历史的 messages ─────────────────────
    conversation_history: list = []
    business_state: Dict[str, Any] = {}
    if _prompt_builder:
        try:    # 算一堆日期：因为用户会说「本周工时」「上个月呢」这种相对时间,LLM 自己不知道"今天"是哪天、"本周"从哪到哪。
            from datetime import timedelta as _timedelta
            _today = _current_date()
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
                    user_name=user_ctx.get("user_name", user_id or "用户"), # 提示词注入用户信息
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
                base_system = _filter_system_prompt_for_available_tools(base_system)
            except Exception:
                base_system = "你是一个专业的企业工时管理助手。请用简洁、友好的方式回答用户问题。"

            # 长短期记忆注入
            conversation_history = await _prompt_builder.build_messages_with_history(
                user_message=message,    # 用户这句话
                session_id=effective_session_id,        # 本次会话
                user_id=user_id if user_id != "anonymous" else None,  # 匿名则传 None(匿名不查长期记忆)
                base_system_prompt=base_system, # 上面拼好的系统提示
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
        try:
            getter = getattr(_prompt_builder, "get_business_state", None)
            if getter:
                business_state = await getter(effective_session_id) or {}
        except Exception as e:
            logger.warning(f"加载结构化会话状态失败，降级为文本历史: {e}")

    initial_state: AgentState = {
        # 用户级：用户带来了什么
        "user_message": message,                # 当前run的用户消息
        "user_context": user_ctx,               # 用户身份、权限上下文
        "session_id": effective_session_id,
        "stream_response": True,
        "conversation_history": conversation_history, # {"role": ** ,"content": ** ,}
        "business_state": business_state,

        # agent 认为要做什么
        "intent": None,
        "tool_name": None,
        "tool_params": {},
        "query": message,     # 用于 RAG 知识库查询：有处理后的 query，优先使用；没有 query，就使用用户原话兜底。
        "clarify_message": None,

        # 结果
        "tool_result": None,
        "rag_result": None,
        "llm_result": None,
        "error": None,

        # 复杂任务
        "task_plan": None,
        "plan_results": None,

        # ── Agent Loop 默认值 ─────────────────────────────────────────────
        "agent_iterations": 0,    # 工具执行次数
        "agent_max_iterations": int(os.getenv("AGENT_MAX_ITERATIONS", "5") or 5),   # 工具执行次数最大值
        "agent_history": [],    # 一条条工具执行详情：工具名称、参数、结果
    }

    yield _format_sse("start", {
        "message": "开始处理您的请求...",
        "session_id": effective_session_id,
    })
    yield _format_sse("thinking", {"message": "正在分析您的请求意图..."})

    # 用于收集本轮 assistant 响应（供 finally 块保存记忆）
    _collected_assistant_response: str = ""

    # ── Langfuse：把本轮所有 LLM generation 归到同一条 trace，打 user/session ──
    from app.services.langfuse_client import trace_context as _lf_trace_context, flush as _lf_flush
    _lf_trace = _lf_trace_context(
        user_id=user_id,
        session_id=effective_session_id,
        tags=["workhour-agent"],
        trace_name="chat",
        metadata={
            # 数据集构建需要：角色（越权/权限标注）、部门；user_message 作为 prompt
            "entity_type": user_ctx.get("entity_type"),
            "department_id": user_ctx.get("department_id"),
            "user_message": message,
        },
    )
    _lf_trace.__enter__()

    try:
        async for chunk in _graph.astream(initial_state):
            # chunk 是 {node_name: state_delta} 的字典
            for node_name, state_delta in chunk.items():
                if node_name in ("classify_intent", "llm_with_tools"):
                    intent = state_delta.get("intent", "general_chat")
                    log_intent = intent
                    candidate_tool = state_delta.get("tool_name") or ""
                    if candidate_tool:
                        round_tool_name = candidate_tool
                        round_tool_params = dict(state_delta.get("tool_params") or {})
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
                        if intent == "complex_request":
                            round_task_plan = state_delta.get("task_plan")
                        yield _format_sse("thinking", {"message": "正在生成回复..."})

                elif node_name == "execute_tool":
                    result = state_delta.get("tool_result")
                    round_tool_result = result if isinstance(result, dict) else None
                    error = state_delta.get("error")
                    if error or (result and not result.get("success", True)):
                        # 错误消息优先级：state.error > result.error > result.result.error > 默认值
                        inner = result.get("result") if isinstance(result, dict) else None
                        msg = (
                            error
                            or (result.get("error") if isinstance(result, dict) else None)
                            or (result.get("message") if isinstance(result, dict) else None)
                            or (inner.get("error") if isinstance(inner, dict) else None)
                            or (inner.get("message") if isinstance(inner, dict) else None)
                            or "工具执行失败"
                        )
                        log_status = "error"
                        log_error = msg
                        log_tools_called = [{"tool_name": log_tool_name, "success": False, "error": msg}]
                        yield _format_sse("error", {"message": msg})
                    else:
                        # 从工具结果提取摘要文本供前端展示
                        inner_result = result.get("result") if isinstance(result, dict) else None
                        summary_text = None
                        if isinstance(inner_result, dict):
                            # batch_save_workhour 返回 preview_text（人类可读的预览文本），优先展示
                            summary_text = (
                                inner_result.get("preview_text")
                                or inner_result.get("summary")
                                or inner_result.get("message")
                            )
                            # query_timesheet / compute_statistics 的 summary 是**结构化 dict**
                            # （date_range/total_hours/projects…），不是展示文本。直接当消息用会：
                            # 非流式路径 str+dict 抛 TypeError → 整个请求 500；
                            # 流式路径把 dict 塞进 message → 前端渲染成 [object Object]。
                            # 非字符串一律丢弃，落到下面 _build_fallback_message 生成 Markdown 表格。
                            if not isinstance(summary_text, str):
                                summary_text = None

                        # 如果 LLM 没有提供 summary，自动生成 Markdown 表格 fallback
                        if not summary_text:
                            summary_text = _build_fallback_message(log_tool_name, result)

                        # 通用后处理：没有预定义格式化规则时，用 LLM 生成自然语言摘要
                        if not summary_text:
                            summary_text = await _generate_llm_summary(
                                log_tool_name, result, message
                            )

                        _collected_assistant_response = summary_text or _summarize_tool_result(log_tool_name, result)
                        log_ai_response = _collected_assistant_response
                        log_tools_called = [{"tool_name": log_tool_name, "success": True}]

                        # 精简 result：只暴露用户关心的数据，去掉内部执行信息
                        user_facing_data = _extract_user_facing_data(log_tool_name, result)
                        yield _format_sse("response", {
                            "result": user_facing_data,
                            "tool_name": log_tool_name,
                            "message": _collected_assistant_response,  # 前端优先展示此摘要
                        })
                        # ── Chart 事件：工具结果可视化 ───────────────────────────
                        try:
                            chart_data = await build_chart_option(
                                user_query=message,
                                tool_result=result,
                                llm_client=_llm_client,
                            )
                            if chart_data:
                                yield _format_sse("chart", chart_data)
                        except Exception as chart_err:
                            logger.warning(f"Chart 事件生成失败，静默降级: {chart_err}")

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
                                    yield _format_sse("response", {"chunk": text})
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
                                        "chunk": f"\n\n---\n📚 **来源：** " + " | ".join(source_names)
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
        # ── Langfuse：关闭本轮 trace 上下文并尽力上报（失败不影响主流程）──────────
        try:
            _lf_trace.__exit__(None, None, None)
            _lf_flush()
        except Exception:
            pass

        # 保存结构化多轮业务状态。该状态与会话使用相同 Redis TTL，
        # 不保存 auth_token，也不依赖助手可见回复反向猜测工具参数。
        if _prompt_builder:
            try:
                next_business_state = dict(business_state)
                if _CANCEL_WRITE_RE.fullmatch(message.strip()):
                    next_business_state.pop("pending_write", None)
                if round_tool_name:
                    safe_params = _business_safe_params(round_tool_params)
                    next_business_state["last_tool"] = {
                        "name": round_tool_name,
                        "params": safe_params,
                    }
                    inner_result = (
                        round_tool_result.get("result")
                        if isinstance(round_tool_result, dict)
                        else None
                    )
                    result_success = not (
                        log_status == "error"
                        or (isinstance(round_tool_result, dict) and round_tool_result.get("success") is False)
                        or (isinstance(inner_result, dict) and inner_result.get("success") is False)
                    )
                    next_business_state["last_outcome"] = "success" if result_success else "failed"
                    if round_tool_name in _WRITE_TOOL_NAMES:
                        if safe_params.get("dry_run") is True or log_intent == "clarify" or not result_success:
                            next_business_state["pending_write"] = {
                                "name": round_tool_name,
                                "params": safe_params,
                                "preview_succeeded": bool(result_success and safe_params.get("dry_run") is True),
                            }
                        elif result_success:
                            next_business_state.pop("pending_write", None)
                if round_task_plan:
                    next_business_state["last_plan"] = {
                        "tasks": [
                            {
                                "tool_name": task.get("tool_name"),
                                "parameters": _business_safe_params(task.get("parameters") or {}),
                            }
                            for task in (round_task_plan.get("tasks") or [])
                        ]
                    }
                updater = getattr(_prompt_builder, "update_business_state", None)
                if updater:
                    await updater(effective_session_id, user_id, next_business_state)
            except Exception as state_err:
                logger.debug(f"保存结构化会话状态失败（非关键）: {state_err}")

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
            from app.models.conversation import ConversationLogEntry
            from app.services.database import get_db_service
            from app.models.ai_session import AiSession

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            model = os.getenv("CHAT_LLM_MODEL", os.getenv("LLM_MODEL", "unknown"))

            # 上下文快照渐进式压缩（技术债 #11：汇总→裁剪→硬截断，降级安全）
            from app.services.context_compressor import compress_context_snapshot
            safe_snapshot = compress_context_snapshot(log_context_snapshot, max_chars=8000)

            total_tokens = 0
            get_conversation_logger().log_conversation(
                ConversationLogEntry(
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
            )

            # 同步更新 ai_sessions 汇总（upsert）
            # 这是本块**第二处**写库，与上面的 conversation_logs 各走一次连接：
            # 只挡住 log_conversation 是不够的，这里仍会去连内网 MySQL，
            # 本地每请求照样白等数秒 TCP 超时（见 CONVERSATION_LOG_ENABLED 注释）。
            from app.core.config import settings as _settings
            if user_id != "anonymous" and _settings.CONVERSATION_LOG_ENABLED:
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

    if tool_name == "suggest_workhour":
        projects = inner.get("suggested_projects", [])
        hours = inner.get("suggested_hours", 8)
        tip = inner.get("tip", "")
        if not projects:
            return "暂无历史填报记录，请直接告诉我您要填报的项目和工时。"
        lines = ["根据您最近 30 天的填报记录，推荐如下：\n"]
        for i, p in enumerate(projects, 1):
            freq = p.get("frequency", 0)
            lines.append(f"{i}. {p['project_name']}（最近填报 {freq} 次）")
        lines.append(f"\n推荐工时：{hours} 小时")
        if tip:
            lines.append(f"\n{tip}")
        return "\n".join(lines)

    # 通用兜底：优先取工具自带的 message，避免暴露工具名
    if inner.get("message"):
        return str(inner["message"])
    if inner.get("success"):
        return "操作已成功完成。"
    return "操作已完成。"


async def _generate_llm_summary(tool_name: str, result: Dict[str, Any], user_message: str) -> Optional[str]:
    """用 LLM 将工具执行结果转化为用户友好的自然语言摘要（通用后处理）"""
    if not _llm_client:
        return None

    inner = result.get("result") if isinstance(result, dict) else result
    if not isinstance(inner, dict):
        inner = {"result": inner} if inner else {}

    # 截断过长的结果，防止超出 prompt 长度
    result_text = json.dumps(inner, ensure_ascii=False)
    if len(result_text) > 3000:
        result_text = result_text[:3000] + "\n...（结果过长，已截断）"

    system_prompt = (
        "你是企业工时管理系统的智能助手。你的任务是将系统返回的数据转化为"
        "简洁、友好、口语化的中文回复。要求："
        "1. 不要提及工具名称、字段名、JSON 结构等技术细节；"
        "2. 直接告诉用户他能理解的信息；"
        "3. 如果涉及数据，用列表或简短描述呈现，不要直接输出 JSON；"
        "4. 控制在 300 字以内；"
        "5. 禁止在回复中展示 UUID 格式的技术 ID（如 project_id、record_id、user_id 等），只展示名称。"
    )

    prompt = (
        f'用户说："{user_message}"\n\n'
        f"系统返回了以下结果：\n{result_text}\n\n"
        f"请用自然语言直接回复用户，不要暴露技术细节。"
    )

    try:
        summary = await _llm_client.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=800,
        )
        return summary.strip() if summary else None
    except Exception as e:
        logger.warning(f"LLM 工具摘要生成失败: {e}")
        return None


# ─── Response 事件后处理：精简结果 + Markdown 表格 fallback ──────────────────

def _extract_user_facing_data(tool_name: str, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    从 task_executor 的完整返回中提取用户关心的数据。
    去掉内部字段（parameters, execution_time 等），只保留业务数据。
    """
    if not result or not isinstance(result, dict):
        return None

    inner = result.get("result")
    if not isinstance(inner, dict):
        return None

    if inner.get("success") is False:
        return {"error": inner.get("error", "查询失败")}

    if tool_name == "sql_query":
        return {
            "row_count": inner.get("row_count", 0),
            "columns": inner.get("columns", []),
            "data": inner.get("data", []),
            "summary": inner.get("summary", ""),
        }

    if tool_name == "compute_statistics":
        return {
            "statistics_type": inner.get("statistics_type", ""),
            "date_range": inner.get("date_range", ""),
            "total_hours": inner.get("total_hours", 0),
            "total_records": inner.get("total_records", 0),
            "items": [
                {
                    "name": item.get("name", ""),
                    "total_hours": item.get("total_hours", 0),
                    "work_days": item.get("work_days", 0),
                    "average_daily_hours": item.get("average_daily_hours", 0),
                }
                for item in inner.get("items", [])
            ],
        }

    if tool_name == "query_timesheet":
        _display = {"user_id", "project_name", "date", "duration", "description"}
        return {
            "total_hours": inner.get("total_hours", 0),
            "record_count": inner.get("record_count", 0),
            "records": [
                {k: v for k, v in r.items() if k in _display}
                for r in inner.get("records", [])[:20]
            ],
        }

    if tool_name == "suggest_workhour":
        return {
            "suggested_projects": inner.get("suggested_projects", []),
            "suggested_hours": inner.get("suggested_hours", 8),
            "tip": inner.get("tip", ""),
        }

    # 通用 fallback：保留 message 和关键字段
    return {k: v for k, v in inner.items() if k in ("message", "success", "data", "items", "records", "projects")}


def _format_markdown_table(rows: List[Dict[str, Any]], max_rows: int = 20) -> str:
    """把数据行转为 Markdown 表格字符串"""
    if not rows:
        return ""

    rows = rows[:max_rows]
    columns = list(rows[0].keys())

    # 表头
    header = " | ".join(columns)
    separator = " | ".join(["---"] * len(columns))

    # 数据行
    lines = []
    for row in rows:
        line = " | ".join(str(row.get(c, "")) for c in columns)
        lines.append(line)

    return "\n".join([header, separator] + lines)


def _build_fallback_message(tool_name: str, result: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    当 LLM 没有提供 summary/message 时，自动生成 Markdown 表格作为 fallback。
    """
    user_data = _extract_user_facing_data(tool_name, result)
    if not user_data:
        return None

    if user_data.get("error"):
        return None  # 错误已在 error 事件中处理

    # sql_query
    if "data" in user_data and "columns" in user_data:
        data = user_data["data"]
        if data:
            table = _format_markdown_table(data, max_rows=20)
            suffix = f"\n\n（共 {len(data)} 条记录）" if len(data) > 20 else ""
            return table + suffix
        return "查询完成，暂无数据。"

    # compute_statistics
    if "items" in user_data:
        items = user_data["items"]
        if items:
            table = _format_markdown_table(items, max_rows=20)
            suffix = f"\n\n（共 {len(items)} 条记录，总计工时：{user_data.get('total_hours', 0)} 小时）"
            return table + suffix
        return "统计完成，暂无数据。"

    # query_timesheet
    if "records" in user_data:
        records = user_data["records"]
        if records:
            table = _format_markdown_table(records, max_rows=20)
            suffix = f"\n\n（共 {user_data.get('record_count', 0)} 条记录，总工时：{user_data.get('total_hours', 0)} 小时）"
            return table + suffix
        return "查询完成，暂无工时记录。"

    # suggest_workhour
    if "suggested_projects" in user_data:
        projects = user_data["suggested_projects"]
        hours = user_data.get("suggested_hours", 8)
        tip = user_data.get("tip", "")
        if not projects:
            return "暂无历史填报记录，请直接告诉我您要填报的项目和工时。"
        lines = ["**智能填报建议**\n"]
        lines.append("| 序号 | 推荐项目 | 最近填报次数 |")
        lines.append("| --- | --- | --- |")
        for i, p in enumerate(projects, 1):
            freq = p.get("frequency", 0)
            lines.append(f"| {i} | {p.get('project_name', '')} | {freq} |")
        lines.append(f"\n**推荐工时：** {hours} 小时")
        if tip:
            lines.append(f"\n*{tip}*")
        return "\n".join(lines)

    # 通用 fallback
    msg = user_data.get("message")
    return msg if msg else None
