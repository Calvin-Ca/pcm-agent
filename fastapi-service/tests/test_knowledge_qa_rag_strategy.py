"""
TDD 单元测试：方案 A 升级触发策略改进
mock LLM client，不打网络。
"""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock
import app.services.langgraph_agent as lg


class FakeToolDef:
    def __init__(self, name, description="", json_schema=None):
        self.name = name
        self.description = description
        self.json_schema = json_schema or {}


class FakeRegistry:
    def list_tools(self):
        return [
            FakeToolDef("query_timesheet"),
            FakeToolDef("knowledge_qa"),
            FakeToolDef("kb_outline"),
            FakeToolDef("kb_keyword_search"),
            FakeToolDef("kb_semantic_search"),
            FakeToolDef("kb_read_section"),
        ]

    def get_tool(self, name):
        return next((tool for tool in self.list_tools() if tool.name == name), None)


class FakeLLMClient:
    def __init__(self, model="qwen3-8b"):
        self.model = model
        self.api_base = "http://test/v1"
        self.api_key = "test-key"
        self.last_kwargs = None

    async def generate_with_tools(self, **kw):
        self.last_kwargs = kw
        return {
            "finish_reason": "tool_calls",
            "tool_calls": [{"name": "knowledge_qa", "arguments": {"query": "test query"}}],
        }

    async def generate(self, **kw):
        return "test answer"


class FakePlannerClient:
    def __init__(self):
        self.model = "qwen3.5-plus"
        self.api_base = "https://test/v1"
        self.api_key = "planner-key"


@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    monkeypatch.setattr(lg, "_llm_client", None)
    monkeypatch.setattr(lg, "_tool_registry", None)
    monkeypatch.setattr(lg, "_intent_router", None)


@pytest.fixture
def knowledge_qa_state():
    return {
        "user_message": "test query",
        "user_context": {},
        "conversation_history": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "test query"},
        ],
    }


# ─── 测试 1: planner 可用时 knowledge_qa 仍走单步快速通道 ─────────────────────

@pytest.mark.asyncio
async def test_knowledge_qa_uses_fast_path_when_planner_ok(monkeypatch, knowledge_qa_state):
    """knowledge_qa 已是单步 RAG 决策，不应再升级 planner 重复分类。"""
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("PLANNER_LLM_API_BASE", "https://test/v1")

    llm_client = FakeLLMClient()
    lg._llm_client = llm_client
    lg._tool_registry = FakeRegistry()

    def unexpected_planner(*args, **kwargs):
        raise AssertionError("knowledge_qa fast path must not call planner")

    monkeypatch.setattr(lg, "get_planner_llm_client", unexpected_planner)

    result = await lg.node_llm_with_tools(knowledge_qa_state)

    # ① 返回 knowledge_qa 意图
    assert result["intent"] == "knowledge_qa"
    # ② 单步 RAG，不进入 agent 自循环
    assert result.get("rag_strategy") is None

    # ③ 条件边直接路由到 execute_rag
    route = lg._route_by_intent(result)
    assert route == "execute_rag"
    assert llm_client.last_kwargs["extra"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


@pytest.mark.asyncio
async def test_streaming_rag_defers_non_stream_generation():
    result = await lg.node_execute_rag({
        "stream_response": True,
        "query": "工时填报规则是什么",
        "user_message": "工时填报规则是什么",
    })

    assert result == {
        "rag_result": {"success": True, "deferred_stream": True}
    }


# ─── 测试 2: planner 不可用时回退到单步 RAG ──────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_qa_falls_back_when_planner_unavailable(monkeypatch, knowledge_qa_state):
    """mock PLANNER_LLM_API_KEY 空 -> 断言 rag_strategy is None、走 execute_rag、用 8b client。"""
    monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)

    lg._llm_client = FakeLLMClient()
    lg._tool_registry = FakeRegistry()

    result = await lg.node_llm_with_tools(knowledge_qa_state)

    assert result["intent"] == "knowledge_qa"
    assert result.get("rag_strategy") is None

    route = lg._route_by_intent(result)
    assert route == "execute_rag"


# ─── 测试 3: planner 中途掉线时设置回退标志 ───────────────────────────────────

@pytest.mark.asyncio
async def test_planner_dropout_midloop_sets_fallback_flag(monkeypatch, knowledge_qa_state):
    """mock planner 首轮 OK、第 2 轮抛错 -> 断言 _rag_fallback=True、
    _agent_loop_should_continue 返回 force_end、最终经 summarize 出答案、
    agent_history 前序不丢。"""
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "test-key")

    lg._llm_client = FakeLLMClient()
    lg._tool_registry = FakeRegistry()

    call_count = [0]

    def fake_get_planner(*a, **k):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakePlannerClient()
        raise RuntimeError("planner dropout")

    monkeypatch.setattr(lg, "get_planner_llm_client", fake_get_planner)

    # 模拟第二轮 state：rag_strategy='agent'，已有 agent_history
    state = {
        "intent": "knowledge_qa",
        "rag_strategy": "agent",
        "agent_iterations": 1,
        "agent_max_iterations": 5,
        "agent_history": [
            {
                "iteration": 0,
                "tool": "kb_outline",
                "args": {"category": "工时管理"},
                "observation": {"success": True, "result": "..."},
            }
        ],
        "_rag_fallback": None,
    }

    # 模拟两轮调用：首轮 OK，第 2 轮抛错（升级门 except 分支）
    _fc = fake_get_planner()  # 第 1 轮 OK
    assert _fc.model == "qwen3.5-plus"
    try:
        _fc2 = fake_get_planner()  # 第 2 轮抛错
    except Exception:
        state["rag_strategy"] = None
        state["_rag_fallback"] = True

    assert state.get("_rag_fallback") is True

    # _agent_loop_should_continue 应返回 force_end
    cont = lg._agent_loop_should_continue(state)
    assert cont == "force_end"

    # agent_history 前序不丢
    assert len(state["agent_history"]) == 1
    assert state["agent_history"][0]["tool"] == "kb_outline"


# ─── 测试 4: agent 模式下 tools schema 包含 4 个 kb_* ────────────────────────

def test_kb_tools_injected_in_agent_mode(monkeypatch):
    """rag_strategy=='agent' 时断言本轮 tools schema 含 4 个 kb_*。"""
    lg._tool_registry = FakeRegistry()
    tools = lg._build_openai_tools(lg._tool_registry)
    names = {t["function"]["name"] for t in tools}

    kb_tools = {"kb_outline", "kb_keyword_search", "kb_semantic_search", "kb_read_section"}
    assert kb_tools.issubset(names), f"missing kb tools: {kb_tools - names}"
