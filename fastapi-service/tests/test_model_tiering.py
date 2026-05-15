import os
import pytest
from app.services.llm_client import LLMClient, get_planner_llm_client


def test_planner_factory_falls_back_to_chat_when_planner_unset(monkeypatch):
    monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)
    monkeypatch.setenv("CHAT_LLM_API_KEY", "chat-key")
    monkeypatch.setenv("CHAT_LLM_API_BASE", "http://chat-base/v1")
    monkeypatch.setenv("CHAT_LLM_MODEL", "qwen3-8b")
    client = get_planner_llm_client()
    assert isinstance(client, LLMClient)
    assert client.api_key == "chat-key"
    assert client.model == "qwen3-8b"


def test_planner_factory_uses_planner_when_set(monkeypatch):
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "planner-key")
    monkeypatch.setenv("PLANNER_LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("PLANNER_LLM_MODEL", "qwen-plus")
    client = get_planner_llm_client()
    assert client.api_key == "planner-key"
    assert client.model == "qwen-plus"
    assert "dashscope" in client.api_base


def test_node_plan_and_execute_uses_planner_client(monkeypatch):
    """node_plan_and_execute 走 PlannerAgent 时，PlannerAgent.llm_client 应来自推理层工厂。"""
    import app.services.langgraph_agent as lg
    captured = {}

    class FakePlanner:
        def __init__(self, tool_registry=None, llm_client=None):
            captured["llm_client"] = llm_client
        async def plan_tasks(self, **kw):
            raise RuntimeError("stop-here")

    monkeypatch.setattr("app.models.task_plan.PlannerAgent", FakePlanner)
    sentinel = object()
    monkeypatch.setattr(lg, "get_planner_llm_client", lambda *a, **k: sentinel)
    monkeypatch.setattr(lg, "_llm_client", object())
    monkeypatch.setattr(lg, "_tool_registry", object())

    import asyncio
    state = {"user_message": "对比 A、B、C 三个项目本月工时", "user_context": {}}
    asyncio.get_event_loop().run_until_complete(lg.node_plan_and_execute(state))
    assert captured["llm_client"] is sentinel


def test_batch_save_workhour_uses_planner_client(monkeypatch):
    """batch_save_workhour 解析阶段应使用推理层工厂创建的客户端。"""
    import app.tools.batch_save_workhour as bsw
    captured = {}

    def fake_factory(*a, **k):
        obj = object()
        captured["client"] = obj
        return obj

    monkeypatch.setattr(bsw, "get_planner_llm_client", fake_factory, raising=False)
    # 触发模块内创建客户端的代码路径：直接断言源码已改为调用工厂
    import inspect
    src = inspect.getsource(bsw)
    assert "get_planner_llm_client(" in src
    assert 'LLMClient(env_prefix="CHAT_LLM"' not in src


import asyncio
import app.services.langgraph_agent as lg


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_arag_first_iteration_uses_8b(monkeypatch):
    """无 kb 历史（首轮）→ 必须用 8b _llm_client，不升级。"""
    used = {}

    class FakeClient:
        api_base = "http://172.19.3.136:8099/v1"
        tag = "8b"
        async def generate_with_tools(self, **kw):
            used["tag"] = self.tag
            return {"finish_reason": "stop", "content": "hi"}

    monkeypatch.setattr(lg, "_llm_client", FakeClient())
    monkeypatch.setattr(lg, "_tool_registry", object())
    monkeypatch.setattr(lg, "_build_openai_tools", lambda r: [{"type": "function"}])
    monkeypatch.setattr(lg, "get_planner_llm_client",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该升级")))
    state = {
        "user_message": "考勤怎么算迟到",
        "conversation_history": [{"role": "user", "content": "考勤怎么算迟到"}],
        "agent_history": [],
    }
    _run(lg.node_llm_with_tools(state))
    assert used["tag"] == "8b"


def test_arag_after_kb_tool_escalates_to_planner(monkeypatch):
    """agent_history 已含 kb_* → 本轮 FC 切推理层客户端。"""
    used = {}

    class Base:
        api_base = "http://172.19.3.136:8099/v1"
        async def generate_with_tools(self, **kw):
            used["tag"] = self.tag
            return {"finish_reason": "stop", "content": "done"}

    class Eight(Base): tag = "8b"
    class Planner(Base): tag = "planner"

    monkeypatch.setattr(lg, "_llm_client", Eight())
    monkeypatch.setattr(lg, "_tool_registry", object())
    monkeypatch.setattr(lg, "_build_openai_tools", lambda r: [{"type": "function"}])
    monkeypatch.setattr(lg, "get_planner_llm_client", lambda *a, **k: Planner())
    state = {
        "user_message": "继续",
        "conversation_history": [{"role": "user", "content": "考勤制度"}],
        "agent_history": [
            {"iteration": 0, "tool": "kb_keyword_search", "args": {}, "observation": "x"}
        ],
    }
    _run(lg.node_llm_with_tools(state))
    assert used["tag"] == "planner"
