"""
Prometheus 指标定义

所有指标统一在此定义，各模块 import 后使用。
"""

from prometheus_client import Counter, Histogram, Gauge, Info

# ─── 请求级指标 ────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "ai_chat_requests_total",
    "聊天请求总数",
    ["intent", "status"],  # intent: tool_execution/knowledge_qa/general_chat/clarify, status: success/error
)

REQUEST_LATENCY = Histogram(
    "ai_chat_request_duration_seconds",
    "聊天请求处理耗时（秒）",
    ["intent"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

ACTIVE_REQUESTS = Gauge(
    "ai_chat_active_requests",
    "当前正在处理的请求数",
)

# ─── 工具执行指标 ──────────────────────────────────────────────────────────────

TOOL_CALL_COUNT = Counter(
    "ai_tool_calls_total",
    "工具调用总数",
    ["tool_name", "status"],  # status: success/error/permission_denied
)

TOOL_CALL_LATENCY = Histogram(
    "ai_tool_call_duration_seconds",
    "工具调用耗时（秒）",
    ["tool_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ─── LLM 调用指标 ──────────────────────────────────────────────────────────────

LLM_CALL_COUNT = Counter(
    "ai_llm_calls_total",
    "LLM API 调用总数",
    ["model", "call_type", "status"],  # call_type: generate/stream/function_calling, status: success/error
)

LLM_CALL_LATENCY = Histogram(
    "ai_llm_call_duration_seconds",
    "LLM API 调用耗时（秒）",
    ["model", "call_type"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

LLM_TOKENS = Counter(
    "ai_llm_tokens_total",
    "LLM Token 使用量",
    ["model", "token_type"],  # token_type: prompt/completion
)

# ─── RAG 指标 ──────────────────────────────────────────────────────────────────

RAG_QUERY_COUNT = Counter(
    "ai_rag_queries_total",
    "RAG 查询总数",
    ["status"],  # status: success/no_results/error
)

RAG_QUERY_LATENCY = Histogram(
    "ai_rag_query_duration_seconds",
    "RAG 查询耗时（秒）",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0],
)

# ─── 服务信息 ──────────────────────────────────────────────────────────────────

SERVICE_INFO = Info(
    "ai_service",
    "AI 服务版本信息",
)
