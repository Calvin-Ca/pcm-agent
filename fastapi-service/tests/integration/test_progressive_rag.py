"""
集成测试: A-RAG 渐进式披露 + Agent Loop (Phase 3)

驱动入口: 直接 await _graph.ainvoke(initial_state)
不通过 SSE stream, 检查最终 state.

覆盖:
1. 简单问题 → knowledge_qa 快速通道, agent_iterations ≤ 1
2. 跨文档复杂问题 → 多轮 tool_call 并出现 kb_* 类工具
3. force max_iterations → mock LLM 总是返回 tool_call → 5 轮后 force_end → summarize
4. 重复 tool_call 检测 → 连续相同 (tool, args) → 提前 end
5. kb_* 异常回退 → 异常多次后通过 agent loop 守卫终止
"""

import json
import pytest

from app.services import langgraph_agent as lg


# ─── 工具函数 ─────────────────────────────────────────────────────────────────


def _initial_state(user_message: str, max_iter: int = 5) -> dict:
    return {
        "user_message": user_message,
        "user_context": {"user_id": "u1", "entity_type": "employee"},
        "session_id": "s1",
        "conversation_history": [
            {"role": "system", "content": "你是工时助手"},
            {"role": "user", "content": user_message},
        ],
        "intent": None,
        "tool_name": None,
        "tool_params": {},
        "query": "",
        "clarify_message": None,
        "tool_result": None,
        "rag_result": None,
        "llm_result": None,
        "error": None,
        "task_plan": None,
        "plan_results": None,
        "agent_iterations": 0,
        "agent_max_iterations": max_iter,
        "agent_history": [],
    }


def _tc(name: str, args: dict | None = None) -> dict:
    """构造一个 OpenAI 风格 tool_calls 响应"""
    return {
        "finish_reason": "tool_calls",
        "content": None,
        "tool_calls": [{"name": name, "arguments": args or {}}],
    }


def _stop(content: str) -> dict:
    """构造一个 stop 响应 (LLM 直接给文字)"""
    return {"finish_reason": "stop", "content": content, "tool_calls": []}


class _ScriptedLLM:
    """按调用次数返回预设响应"""

    def __init__(self, responses: list[dict], api_base: str = "http://x"):
        self._responses = list(responses)
        self.api_base = api_base
        self.calls: list[list[dict]] = []

    async def generate_with_tools(self, messages, tools, **kwargs):
        self.calls.append(list(messages))
        if not self._responses:
            return _stop("no more")
        return self._responses.pop(0)


class _ScriptedExecutor:
    """对 execute_single_task 返回预设结果, 按 (tool_name) 路由"""

    def __init__(self, results: dict[str, list]):
        self._results = {k: list(v) for k, v in results.items()}
        self.calls: list[tuple] = []

    async def execute_single_task(self, task, permission_ctx=None):
        self.calls.append((task.tool_name, dict(task.parameters or {})))
        bucket = self._results.get(task.tool_name) or self._results.get("__default__")
        if bucket:
            r = bucket.pop(0) if bucket else None
            return r if r is not None else {"success": True, "data": "ok"}
        return {"success": True, "data": "ok"}


@pytest.fixture
def graph_with_mocks(monkeypatch):
    """
    返回 (run_fn, llm_mock_setter, executor_mock_setter):
    - run_fn(state) -> awaitable: 执行 graph
    - llm_mock_setter(responses) 安装 LLM 脚本
    - executor_mock_setter(results) 安装工具执行脚本
    """
    holders: dict = {"llm": None, "executor": None}

    def install_llm(responses: list[dict]):
        m = _ScriptedLLM(responses)
        monkeypatch.setattr(lg, "_llm_client", m, raising=False)
        holders["llm"] = m
        return m

    def install_executor(results: dict[str, list]):
        m = _ScriptedExecutor(results)
        monkeypatch.setattr(lg, "_task_executor", m, raising=False)
        holders["executor"] = m
        return m

    async def run(state):
        if lg._graph is None:
            lg._graph = lg._build_graph()
        return await lg._graph.ainvoke(state)

    return run, install_llm, install_executor, holders


# ─── 用例 1: 简单问题走 knowledge_qa 快速通道 ────────────────────────────────


@pytest.mark.asyncio
async def test_simple_question_uses_knowledge_qa(graph_with_mocks, monkeypatch):
    """LLM 返回 knowledge_qa tool_call → 走 execute_rag 一次结束"""
    run, install_llm, install_executor, _ = graph_with_mocks

    install_llm([_tc("knowledge_qa", {"query": "加班算工时吗"})])
    install_executor({})

    # mock RAG 服务避免真调 Milvus
    async def fake_rag(state):
        return {"rag_result": {"answer": "加班按制度计入工时"}}

    monkeypatch.setattr(lg, "node_execute_rag", fake_rag)
    # 重建图以应用 mock
    lg._graph = lg._build_graph()

    state = _initial_state("加班算工时吗")
    final = await run(state)

    assert final.get("intent") == "knowledge_qa"
    # knowledge_qa 不进 agent loop, iterations 应保持 0
    assert final.get("agent_iterations", 0) == 0
    assert final.get("rag_result", {}).get("answer")


# ─── 用例 2: 跨文档复杂问题, ≥ 2 次 tool 调用且含 kb_* ────────────────────────


@pytest.mark.asyncio
async def test_cross_doc_question_invokes_kb_tools(graph_with_mocks):
    """
    LLM 第 1 轮: kb_outline → 第 2 轮: kb_keyword_search → 第 3 轮: stop
    最终 agent_history 至少 2 条且包含 kb_* 工具
    """
    run, install_llm, install_executor, holders = graph_with_mocks

    install_llm([
        _tc("kb_outline", {"category": "工时管理"}),
        _tc("kb_keyword_search", {"query": "周末加班审批超时", "top_k": 3}),
        _stop("根据制度, 周末加班需补审批后再计入工时."),
    ])
    install_executor({
        "kb_outline": [{"success": True, "documents": [{"title": "加班制度"}]}],
        "kb_keyword_search": [
            {"success": True, "results": [{"file": "ot.md", "section": "审批"}]}
        ],
    })

    state = _initial_state("周末加班审批超时,工时怎么记?")
    final = await run(state)

    history = final.get("agent_history") or []
    assert len(history) >= 2, f"期望 ≥ 2 条 history, 实际 {len(history)}"
    used_tools = {h.get("tool") for h in history}
    assert any(t and t.startswith("kb_") for t in used_tools), (
        f"应至少包含一个 kb_* 工具, 实际 {used_tools}"
    )


# ─── 用例 3: force max_iterations ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_force_end_at_max_iterations(graph_with_mocks):
    """LLM 永远返回 tool_call → 跑满 max_iter 后走 force_end → summarize"""
    run, install_llm, install_executor, holders = graph_with_mocks

    install_llm([
        _tc("kb_keyword_search", {"query": f"q{i}"}) for i in range(20)
    ])
    install_executor({
        "kb_keyword_search": [
            {"success": True, "results": [{"file": f"f{i}.md"}]} for i in range(20)
        ],
    })

    state = _initial_state("一个无尽问题", max_iter=3)
    final = await run(state)

    # iterations 应停在 max_iter (闸 1 触发) 或更高 (因为先执行后判断)
    iters = final.get("agent_iterations", 0)
    max_iters = state["agent_max_iterations"]
    assert iters >= max_iters, f"期望 iterations ≥ {max_iters}, 实际 {iters}"
    # force_end 路径会进 summarize, 给 llm_result 兜底
    # 即使 LLMClient 没设置, summarize 也会拼接 plan_results
    history = final.get("agent_history") or []
    assert len(history) >= max_iters


# ─── 用例 4: 重复 tool_call → 提前 end ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeated_tool_call_ends_early(graph_with_mocks):
    """LLM 连续 3 次返回相同 tool_call → 闸 2 触发 → end"""
    run, install_llm, install_executor, holders = graph_with_mocks

    same_call = _tc("kb_keyword_search", {"query": "加班"})
    install_llm([same_call, same_call, same_call, same_call, same_call])
    install_executor({
        "kb_keyword_search": [
            {"success": True, "results": []} for _ in range(5)
        ],
    })

    state = _initial_state("加班相关问题", max_iter=10)
    final = await run(state)

    history = final.get("agent_history") or []
    # 闸 2: 最近 3 条中相同 (tool, args) ≥ 2 次, 在第 2 轮结束后就应触发
    # 所以总轮数应远小于 max_iter=10
    assert len(history) < 10
    assert len(history) >= 2
    # 全部应该是相同的工具与参数
    assert all(h.get("tool") == "kb_keyword_search" for h in history)


# ─── 用例 5: kb_* 异常回退 (连续异常守卫) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_failure_triggers_error_gate(graph_with_mocks):
    """
    工具持续失败 (success=False) → 闸 3 (连续 3 次错误) 触发 → end
    验证 agent loop 不会无限执行失败工具
    """
    run, install_llm, install_executor, holders = graph_with_mocks

    install_llm([
        _tc("kb_keyword_search", {"query": f"q{i}"}) for i in range(20)
    ])
    # 工具一直失败
    install_executor({
        "kb_keyword_search": [
            {"success": False, "error": "RAG 未就绪"} for _ in range(20)
        ],
    })

    state = _initial_state("查制度", max_iter=10)
    final = await run(state)

    history = final.get("agent_history") or []
    # 连续 3 次失败 → 闸 3 触发 → end. 总轮数 ~3
    assert 3 <= len(history) <= 5, f"期望 3-5 轮触发错误守卫, 实际 {len(history)}"
    # 所有 observation 都应表示失败
    fail_count = 0
    for h in history:
        obs = h.get("observation") or {}
        if isinstance(obs, dict) and (obs.get("success") is False or obs.get("error")):
            fail_count += 1
    assert fail_count >= 3
