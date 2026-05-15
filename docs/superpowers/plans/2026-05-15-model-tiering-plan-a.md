# 模型分层（方案 A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把复杂场景（PlannerAgent 多步规划、batch 工时解析、A-RAG 知识库多步导航、SQL Agent）从本地 8b 切到托管 API，轻量场景（闲聊、单工具 FC、意图降级）保留本地 8b，通过 `PLANNER_LLM_*` env 层实现可切换。

**Architecture:** 新增推理层工厂 `get_planner_llm_client()`（`PLANNER_LLM_*` 未配置时回退 `CHAT_LLM_*`，因此不改 .env 时纯 no-op，零风险）。三个隔离消费点（PlannerAgent×2 实例化点、batch_save_workhour）改用该工厂。A-RAG 是方案 A 的**唯一受控破例**：主 FC 节点首轮/单工具/闲聊仍走 8b，但当 `agent_history` 已含 ≥1 个 kb_* 工具调用（即已进入知识库多步导航）时，后续 FC 迭代切到推理层 API 客户端。SQL Agent 代码已支持 `SQL_AGENT_LLM_*`，仅需 .env 填值。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph / pytest / aiohttp（OpenAI 兼容）/ vLLM（本地 8b）/ DashScope（托管 API）

---

## 关键约定（执行者必读）

- **环境**：所有 python/pytest 命令必须走项目 venv：`.venv/Scripts/python.exe`（Windows）。不要用全局 Python。
- **后端编译**：本计划只动 `ai-service/fastapi-service/` 下 Python，与 Java 后端无关，不执行任何 mvn 命令。
- **方案 A 边界**：主 FC 节点（`node_llm_with_tools`）**首轮调用保持 8b**。唯一例外是 Task 4 的 A-RAG 受控破例，且仅在 `agent_history` 已含 kb_* 工具时触发。任何超出本计划范围对主 FC 首轮 / `_llm_client` 默认行为的改动都属越界。
- **回退安全**：`PLANNER_LLM_API_KEY` 未配置时，工厂回退 `CHAT_LLM`，全链路行为与现状完全一致。先合代码、后改 .env 是安全的。

## 文件结构

| 文件 | 改动 | 职责 |
|------|------|------|
| `fastapi-service/app/services/llm_client.py` | 新增模块级函数 | 推理层工厂 `get_planner_llm_client()` |
| `fastapi-service/app/api/chat.py` | 改 1 处（:126） | PlannerAgent（非流式/降级路径）注入推理层客户端 |
| `fastapi-service/app/services/langgraph_agent.py` | 改 1 处（:744-746）+ 新增 Task 4 升级逻辑 | node_plan_and_execute 的 PlannerAgent + A-RAG 受控升级 |
| `fastapi-service/app/tools/batch_save_workhour.py` | 改 1 处（:180） | 批量解析用推理层客户端 |
| `.env.example` | 文档化 3 层 + 示例值 | 配置说明 |
| `fastapi-service/tests/test_model_tiering.py` | 新建 | 本计划全部单测 |

---

### Task 1: 推理层工厂 `get_planner_llm_client()`

**Files:**
- Modify: `fastapi-service/app/services/llm_client.py`（文件尾部新增模块级函数）
- Test: `fastapi-service/tests/test_model_tiering.py`

- [ ] **Step 1: 写失败测试**

新建 `fastapi-service/tests/test_model_tiering.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -v`（cwd = `fastapi-service/`）
Expected: FAIL — `ImportError: cannot import name 'get_planner_llm_client'`

- [ ] **Step 3: 实现工厂**

在 `app/services/llm_client.py` 文件**末尾**（`_build_messages` 方法之后、模块级）追加：

```python
def get_planner_llm_client(
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> LLMClient:
    """推理层 LLM 工厂。

    复杂场景（多步规划 / 批量解析 / A-RAG 多步导航）专用。
    PLANNER_LLM_* 已配置则用之（通常指向托管 API）；
    未配置时回退 CHAT_LLM_*（本地 8b），保证不改 .env 时行为不变。
    """
    prefix = "PLANNER_LLM" if os.getenv("PLANNER_LLM_API_KEY") else "CHAT_LLM"
    return LLMClient(env_prefix=prefix, temperature=temperature, max_tokens=max_tokens)
```

（`os` 已在文件顶部 import，无需新增 import。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add fastapi-service/app/services/llm_client.py fastapi-service/tests/test_model_tiering.py
git commit -m "feat(llm): 新增推理层工厂 get_planner_llm_client（PLANNER_LLM_* 回退 CHAT_LLM）"
```

---

### Task 2: PlannerAgent 两处实例化改用推理层客户端

**Files:**
- Modify: `fastapi-service/app/api/chat.py:126`
- Modify: `fastapi-service/app/services/langgraph_agent.py:744-748`
- Test: `fastapi-service/tests/test_model_tiering.py`

**背景**：`node_plan_and_execute`（langgraph_agent.py:744）和 `initialize_chat_components`（chat.py:126）各实例化一个 `PlannerAgent`，均传入 8b 的 `_llm_client` / `llm_client`。改为传入推理层客户端。这两处都**不是主 FC 节点**，不违反方案 A 边界。

- [ ] **Step 1: 写失败测试**

向 `tests/test_model_tiering.py` 追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py::test_node_plan_and_execute_uses_planner_client -v`
Expected: FAIL（`captured["llm_client"]` 是 8b `_llm_client` 而非 sentinel）

- [ ] **Step 3: 改 langgraph_agent.py**

在 `app/services/langgraph_agent.py` 顶部 import 区（与其他 `from app.services...` 同处）加：

```python
from app.services.llm_client import get_planner_llm_client
```

把 744-748 行的：

```python
        planner = PlannerAgent(
            tool_registry=_tool_registry,
            llm_client=_llm_client,
        )
```

改为：

```python
        planner = PlannerAgent(
            tool_registry=_tool_registry,
            llm_client=get_planner_llm_client(),
        )
```

- [ ] **Step 4: 改 chat.py**

在 `app/api/chat.py` import 区加 `from app.services.llm_client import get_planner_llm_client`，把 :126 行：

```python
    planner_agent = PlannerAgent(tool_registry=tool_registry, llm_client=llm_client)
```

改为：

```python
    planner_agent = PlannerAgent(tool_registry=tool_registry, llm_client=get_planner_llm_client())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -v`
Expected: PASS（全部）

- [ ] **Step 6: 提交**

```bash
git add fastapi-service/app/services/langgraph_agent.py fastapi-service/app/api/chat.py fastapi-service/tests/test_model_tiering.py
git commit -m "feat(llm): PlannerAgent 两处实例化切推理层客户端（多步规划走 API）"
```

---

### Task 3: batch_save_workhour 解析改用推理层客户端

**Files:**
- Modify: `fastapi-service/app/tools/batch_save_workhour.py:180`
- Test: `fastapi-service/tests/test_model_tiering.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_model_tiering.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py::test_batch_save_workhour_uses_planner_client -v`
Expected: FAIL（源码仍是 `LLMClient(env_prefix="CHAT_LLM"...)`）

- [ ] **Step 3: 改 batch_save_workhour.py**

确认顶部 import。若已有 `from app.services.llm_client import LLMClient`，改为：

```python
from app.services.llm_client import LLMClient, get_planner_llm_client
```

把 :180 行：

```python
    llm_client = LLMClient(env_prefix="CHAT_LLM", temperature=0.1, max_tokens=2000)
```

改为：

```python
    llm_client = get_planner_llm_client(temperature=0.1, max_tokens=2000)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add fastapi-service/app/tools/batch_save_workhour.py fastapi-service/tests/test_model_tiering.py
git commit -m "feat(llm): batch_save_workhour 解析切推理层客户端（强类型 JSON 走 API）"
```

---

### Task 4: A-RAG 受控破例 — kb_* 多步导航升级推理层

**Files:**
- Modify: `fastapi-service/app/services/langgraph_agent.py`（`node_llm_with_tools`，约 :230-300）
- Test: `fastapi-service/tests/test_model_tiering.py`

**⚠️ 这是方案 A 唯一对主 FC 节点的受控破例。** 仅当 `agent_history` 已含 ≥1 个 kb_* 工具调用（已进入知识库多步导航）时，本轮 FC 调用切推理层客户端。**首轮、单工具、闲聊一律仍用 8b `_llm_client`，不得改动。** kb 工具名硬集合：`kb_keyword_search` / `kb_outline` / `kb_read_section` / `kb_semantic_search`（已核对 `app/tools/kb_*.py` 的 `name=` 字段）。`agent_history` 条目结构：`{"iteration", "tool", "args", "observation"}`，`tool` 字段即工具名。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_model_tiering.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -k arag -v`
Expected: `test_arag_after_kb_tool_escalates_to_planner` FAIL（仍用 8b）；`test_arag_first_iteration_uses_8b` 可能已 PASS

- [ ] **Step 3: 实现受控升级**

在 `node_llm_with_tools` 内、`result = await _llm_client.generate_with_tools(` 这一行（约 :293）**之前**，插入客户端选择逻辑。把原来的：

```python
        result = await _llm_client.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=1024,
            extra=extra,
        )
```

改为：

```python
        # ── A-RAG 受控破例（方案 A）：已进入 kb 多步导航 → 升级推理层 API ──
        # 首轮/单工具/闲聊不触发，仅当 agent_history 已含 kb_* 工具时切换。
        _KB_TOOLS = {
            "kb_keyword_search", "kb_outline",
            "kb_read_section", "kb_semantic_search",
        }
        _fc_client = _llm_client
        if any((h.get("tool") in _KB_TOOLS) for h in (agent_history or [])):
            try:
                _fc_client = get_planner_llm_client(temperature=0.1, max_tokens=1024)
                logger.info("A-RAG 多步导航：FC 调用升级至推理层客户端 (model=%s)", _fc_client.model)
            except Exception as _esc_err:
                logger.warning("推理层客户端获取失败，回退 8b: %s", _esc_err)
                _fc_client = _llm_client

        result = await _fc_client.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=1024,
            extra=extra,
        )
```

（`get_planner_llm_client` 已在 Task 2 Step 3 于本文件 import，无需重复。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py -v`
Expected: PASS（全部，含两个 arag 用例）

- [ ] **Step 5: 提交**

```bash
git add fastapi-service/app/services/langgraph_agent.py fastapi-service/tests/test_model_tiering.py
git commit -m "feat(llm): A-RAG kb 多步导航受控升级推理层（方案 A 受控破例，首轮仍 8b）"
```

---

### Task 5: .env.example 文档化 3 层 + SQL Agent 示例值

**Files:**
- Modify: `.env.example`（ai-service 根目录）

**背景**：SQL Agent 代码（`app/tools/sql_query.py:165-182` `SQLAgentLLMClient`）已支持 `SQL_AGENT_LLM_*` 回退 `CHAT_LLM`，无需改代码，仅需文档化让运维填值。本 Task 无测试（纯文档/配置）。

- [ ] **Step 1: 编辑 .env.example**

把 `.env.example` 中「── LLM 配置（主对话生成）──」段之后、「── MySQL」段之前插入推理层段，并补 SQL Agent LLM 示例。在第 23 行（`# CHAT_LLM_MODEL=qwen-plus` 注释行）之后插入：

```
# ── LLM 配置（推理层 / 复杂场景）─────────────────────────────
# 多步规划(PlannerAgent) + 批量工时解析 + A-RAG 知识库多步导航
# 留空 = 回退 CHAT_LLM（本地 8b）；填值 = 复杂场景走托管 API
# PLANNER_LLM_API_KEY=sk-your_dashscope_key
# PLANNER_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
# PLANNER_LLM_MODEL=qwen-plus
```

并把现有 SQL Agent LLM 注释段（约 39-42 行）改为带可用示例值：

```
# ── SQL Agent LLM（空 = 复用 CHAT_LLM；建议指向托管 API）────
# SQL_AGENT_LLM_API_KEY=sk-your_dashscope_key
# SQL_AGENT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
# SQL_AGENT_LLM_MODEL=qwen-plus
```

并在文件顶部「LLM 配置（意图分类）」段前加一段 3 层总览注释：

```
# ============================================================
# 模型分层（方案 A）：按能力分 3 层，复杂场景切 API、轻量留本地 8b
#   轻量层  INTENT_LLM_* / CHAT_LLM_*   → 本地 vLLM 8b（意图降级 / 闲聊 / 单工具 FC）
#   推理层  PLANNER_LLM_*               → 托管 API（多步规划 / batch / A-RAG 多步）
#   SQL 层  SQL_AGENT_LLM_*             → 托管 API（SQL 生成）
# 留空的层自动回退 CHAT_LLM，先合代码后填值是安全的。
# ============================================================
```

- [ ] **Step 2: 提交**

```bash
git add .env.example
git commit -m "docs(env): .env.example 文档化模型 3 层配置（推理层 + SQL 层示例值）"
```

---

### Task 6: 容器重启 + 端到端验证 + 完成报告

**Files:** 无代码改动。验证 + 报告。

**背景**：改了 `fastapi-service/` 源码，必须重启容器并跑端到端用例确认生效，不验证就 commit 等于没改。本验证用**回退态**（`.env` 不填 PLANNER_LLM_*），确认 no-op 安全：行为应与改动前完全一致。

- [ ] **Step 1: 全量单测回归**

Run（cwd `fastapi-service/`）：`.venv/Scripts/python.exe -m pytest tests/test_model_tiering.py tests/test_core_functionality.py -v`
Expected: 全 PASS，无回归。

- [ ] **Step 2: 重启 ai-service 容器**

```bash
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose restart ai-service"
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 30"
```
Expected: 日志无 traceback，`LLM Client initialized` 正常，无 import 错误。

- [ ] **Step 3: 端到端冒烟（回退态，应与改前一致）**

用 employee token 跑 1 条普通工时查询 + 1 条知识库问答（参考 `docs/` 中 E2E 脚本与 token 获取方式；公网测试入口放 116，遵守 WAF 规避）。
Expected: 两条都正常返回，行为与改动前无差异（因 PLANNER_LLM_* 未配置 → 回退 8b）。

- [ ] **Step 4: 完成报告（必须包含以下全部）**

报告须明确写出：
1. **当前分支 + 是否已 push**；与 main 的 commit 差距 `git log main..HEAD --oneline`；推荐合并方式 + 预期冲突文件（若在 feature branch）。
2. 每个 commit 的 `git show --stat`，确认 scope 与 message 名实相符（fix/feat/docs/test/chore）。
3. **「被迫修复」栏**：是否为打通测试改过本计划范围外的代码？如有，列实际命令 + 改动范围；如无，明确写「无」。
4. **破坏性 git 操作**：是否执行过 `reset --hard` / `push --force` / `branch -D` / `checkout HEAD --` / `clean -fd`？如有必须显式列出（执行前本应先报告等批准）；如无明确写「无」。
5. Step 2/3 的容器日志与冒烟实际输出摘录。

---

## Self-Review

- **Spec 覆盖**：推理层工厂(T1) / PlannerAgent×2(T2) / batch(T3) / A-RAG 受控破例(T4) / SQL 层 + 3 层文档(T5) / 验证(T6) — roadmap 第五阶段每条 ⬜ 均有对应 Task。✓
- **Placeholder 扫描**：无 TBD/TODO；每个代码步均含完整可粘贴代码。✓
- **类型一致**：工厂名 `get_planner_llm_client` 在 T1 定义，T2/T3/T4 引用一致；kb 工具名集合与 `app/tools/kb_*.py` `name=` 实际值一致；`agent_history` 字段名 `tool` 与 `_append_agent_history` 写入一致。✓
- **方案 A 边界**：仅 T4 触碰主 FC，且为 `agent_history` 条件触发的受控破例，首轮/单工具/闲聊路径零改动。✓
