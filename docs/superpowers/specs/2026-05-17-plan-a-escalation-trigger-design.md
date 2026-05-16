# 方案 A 升级触发策略改进 — 设计

> 状态：设计已与用户分节确认（2026-05-17），待 spec 复审后转实施计划。

## 背景与问题

方案 A（模型分层）已在 172 启用（`.env` PLANNER/SQL_AGENT model = qwen3.5-plus），
但升级链路经两条独立路径实证**未触发**：

- 生产 `/api/ai/chat/stream`：知识问题被判 `knowledge_qa` → 单步 `execute_rag`
  （本地 qwen3-8b，`tool_count=0`），不进 kb_* FC agent
- bench `--mode progressive --smoke`：8b 首轮 FC `tool_calls=0`，未发起任何
  kb_* → `agent_history` 不含 kb_* → 升级钩子（`langgraph_agent.py:302`）不触发

根因：升级被门控在"`agent_history` 已含 kb_*"，而"8b 不发起 kb_* 多步"正是
8b 最弱、方案 A 本想补的环节（2026-05-05 评测：8b 44% 退化、tool_calls 均值
1.0）。升级被卡在"8b 先成功发起 kb_*"这个它做不到的前提上，对最需要它的查询
空转。代码正确，失效在**触发条件**。

升级目标为何是云端 3.5-plus 而非本地 14B：qwen3.6-plus 盲评
（`docs/benchmarks/llm-quality-bakeoff-2026-05-16.md`）显示 14B 仅 oneshot
最优（completeness 7.39），在本设计专治的 progressive 多步导航上 docs_coverage
**0.333 三者最差**（8B 0.444，3.5-plus 0.833）。要补的多步能力恰是 14B 最弱
项；只有云端 3.5-plus 实测达标（5 步、深度自调）。本地 14B 属独立的"方案 B
主 FC 换模型"候选，与本设计正交。

## 目标

让知识问题在 planner 可用时真正走云端 3.5-plus 驱动的 kb_* 多步导航；planner
不可用时回退本地 8b 单步 RAG，服务不断。改动爆炸半径锁在 knowledge_qa 分支 +
升级门一行条件，不影响 tool_execution / plan_and_execute / 降级 IntentRouter。

## 架构

现状两条路（`app/services/langgraph_agent.py`）：

```
intent=knowledge_qa   → execute_rag → END                 (单步 8b，退化路)
intent=tool_execution → execute_tool ⇄ llm_with_tools      (A-RAG 循环，escalation@302)
```

改动后 knowledge_qa 增加条件改道：

```
intent=knowledge_qa
  ├─ planner 可用 → rag_strategy="agent" → 回 llm_with_tools 循环
  │                  (首轮即 get_planner_llm_client + kb_* 工具 schema)
  │                  → execute_tool ⇄ llm_with_tools (复用现有循环/终止/summarize)
  └─ planner 不可用 → execute_rag → END   (回退 8b 单步)
```

不新建循环：复用 `llm_with_tools→execute_tool→continue` 现有机器
（`_agent_loop_should_continue` + max_iterations + force_end→summarize）。

## 组件与数据流

### 新增状态字段

`AgentState`（`langgraph_agent.py` 内 TypedDict，约 :71）新增两个可选键：

- `rag_strategy: Optional[str]`：`"agent"` | `None`（默认 `None`）
- `_rag_fallback: Optional[bool]`：planner 中途失败标志，默认 `None`

均为可选键，不破坏现有节点对 state 的读取。

### 数据流（一次知识问题）

1. `START → node_llm_with_tools`（首轮），8b 被调用。
2. **收敛点判定**，覆盖两条入口：
   - 8b 返回 `knowledge_qa` 兜底工具调用（`:371` 分支）
   - 8b 不调工具直接答（`finish_reason=stop` → `node_classify_intent`
     判 knowledge_qa）
   两处汇合后做 **planner 探活（不发真实请求）**：判定
   `os.getenv("PLANNER_LLM_API_KEY")` 非空且 `get_planner_llm_client()`
   可构造。
   - 探活过 → `rag_strategy="agent"`，intent 仍标 `knowledge_qa`（日志可读）
   - 探活不过 → `rag_strategy=None`，intent=`knowledge_qa`
3. **条件边**：`knowledge_qa` 路由从静态映射（`:957` 附近
   `"knowledge_qa": "execute_rag"`）改为按 `rag_strategy` 的函数判定：
   `=="agent"` → `llm_with_tools`；否则 → `execute_rag`。
4. **循环内首轮升级**（`:295-308` 升级块）门条件改为：

   ```python
   if state.get("rag_strategy") == "agent" or any(
       (h.get("tool") in _KB_TOOLS) for h in (agent_history or [])
   ):
       try:
           _fc_client = get_planner_llm_client(temperature=0.1, max_tokens=1024)
           logger.info("A-RAG 多步导航：FC 调用升级至推理层客户端 (model=%s)",
                       getattr(_fc_client, "model", "unknown"))
       except Exception as _esc_err:
           logger.warning("推理层客户端获取失败，回退 8b: %s", _esc_err)
           _fc_client = _llm_client
           # 新增：清策略 + 置回退标志，避免 8b 在循环里空绕
           state["rag_strategy"] = None
           state["_rag_fallback"] = True
   ```

   且 `rag_strategy=="agent"` 时确保 `_KB_TOOLS`
   （`kb_outline`/`kb_semantic_search`/`kb_keyword_search`/`kb_read_section`）
   注入本轮 `tools` schema（复用 progressive 工具集构造逻辑，已注册于
   `app/tools/__init__.py:35-38`；若 schema 已含则 no-op）。
5. planner 自驱多步，深度由其自调；`_agent_loop_should_continue` 管终止，
   force_end → `node_summarize`（`:794`）。全部复用现成机器。

## 错误处理与回退

统一回退落点：三种失败都汇到 `summarize` 出答案，绝不给用户报错/空响应。

| 场景 | 处理 |
|------|------|
| ① 探活假阳性（循环内首轮 planner 真实调用失败） | `:303-308` except → 回退 `_llm_client`(8b) + `state["_rag_fallback"]=True` + `rag_strategy=None`；`_agent_loop_should_continue` 见 `_rag_fallback` 立即 `force_end` → summarize |
| ② 循环中途 planner 掉线（已跑 1-2 步后失败） | 同上 except 路径；已采集的 `agent_history`（前序 kb_* observation）由 `node_summarize` 整合，部分多步成果不丢 |
| ③ max_iterations 正常兜底 | 完全复用 `_agent_loop_should_continue`（`:977`，`agent_iterations >= agent_max_iterations → force_end`），无新逻辑 |

`_agent_loop_should_continue` 需新增一条**最高优先级**短路：
`if state.get("_rag_fallback"): return "force_end"`（置于现有 max_iterations
判定之前）。

探活只查 key 非空 + client 可构造、不发真实请求：避免每个知识问题都加一次
云端握手 RTT；真实失败成本只在出问题时由循环内 except 付一次。

## 测试策略（可证伪，不靠运气）

### 单元测试（mock LLM client，不打网络）

- `test_knowledge_qa_routes_to_agent_when_planner_ok`：mock planner 可用 →
  断言 `rag_strategy=="agent"`、条件边去 `llm_with_tools`、首轮 `_fc_client`
  的 `.model` 为 planner 模型。
- `test_knowledge_qa_falls_back_when_planner_unavailable`：mock
  `PLANNER_LLM_API_KEY` 空 → 断言 `rag_strategy is None`、走 `execute_rag`、
  用 8b client。
- `test_planner_dropout_midloop_sets_fallback_flag`：mock planner 首轮 OK、
  第 2 轮抛错 → 断言 `_rag_fallback=True`、`_agent_loop_should_continue`
  返回 `force_end`、最终经 `summarize` 出答案、`agent_history` 前序不丢。
- `test_kb_tools_injected_in_agent_mode`：`rag_strategy=="agent"` 时断言本轮
  tools schema 含 4 个 kb_*。

### 集成冒烟（真实 planner，小样本，raw log 落盘 — 遵循记忆规则 8）

- 复用 `bench_progressive_rag.py`，断言式校验：跑 S01 + M01/L01 progressive，
  从 raw log **grep 断言**出现
  `A-RAG 多步导航：FC 调用升级至推理层客户端 (model=qwen3.5-plus)`，且
  `tool_calls>0`、命中 kb_*。**无此日志行 = 测试失败**。
- 回退用例：临时置错 `PLANNER_LLM_API_KEY` 跑 S01 → 断言 log 出现回退、最终
  仍出答案、`route_type` 回到 `rag_engine`。
- raw log `tee` 到带时间戳文件 + `sha256sum` + 完整贴报告，数字旁注来源行号。

### 验收口径（防自欺）

- 升级生效判据 = 日志实证 `model=qwen3.5-plus` + `tool_calls>0` +
  docs_coverage 较 8b 单步基线提升；不以"有答案/不报错"为通过。
- 生产侧：上线后查 `conversation_logs` 真实流量 knowledge 类的 `model_name`
  是否变为 qwen3.5-plus（原 qwen3-8b）、`tool_count>0` 占比 —— 脚本过
  ≠ 生产生效。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `fastapi-service/app/services/langgraph_agent.py` | AgentState 加 `rag_strategy`/`_rag_fallback`；收敛点（`:371` 分支 + `node_classify_intent` 出口）做 planner 探活并设 `rag_strategy`；`:295-308` 升级门条件放宽 + except 置回退标志 + kb_* 注入；knowledge_qa 条件边改函数判定；`_agent_loop_should_continue` 加 `_rag_fallback` 短路 |
| `fastapi-service/tests/`（新增测试文件） | 4 个单元测试 |
| `fastapi-service/tests/benchmark/bench_progressive_rag.py` | 断言式校验 + 回退用例（小改，加断言不改采集逻辑） |

## 不在范围（YAGNI）

- 不改 `node_execute_rag` 本身（继续作回退单步路）
- 不改 `classify_intent`/qwen-flash 前置分类（方案 C，已否决）
- 不下调 `:302` 旧阈值用于 tool_execution 路径（方案 B，已被冒烟证伪）
- 不动 8099 本地服务模型（8b→14B 属独立方案 B 决策）
- 不做按问题难易的复杂度门（依赖 8b 不可靠判断，已否决；深度交 planner 自调）
