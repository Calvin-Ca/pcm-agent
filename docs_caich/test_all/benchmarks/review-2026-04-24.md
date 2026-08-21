# 基准测试评审报告 — 指标 2：FC vs 两次 LLM 延迟对比

> 评审日期：2026-04-24
> 评审人：Claude
> 状态：**需要返工**

---

## 一、当前测试结果（全量 50 条）

| 指标 | A（两次 LLM）| B（Function Calling）| 降幅 |
|---|---|---|---|
| TTFT P50 | 4852 ms | 8235 ms | **-69.7%**（B 更慢） |
| TTFT P95 | 8691 ms | 15582 ms | -79.3% |
| E2E P50 | 9905 ms | 10222 ms | **-3.2%**（几乎持平） |
| E2E P95 | 15668 ms | 24885 ms | -58.8% |
| 平均 Token | 1151 | N/A | — |

**核心问题：结果与预期完全相反。**

Function Calling（单次 LLM）理应比两次 LLM 快，但测试显示 B 模式的 TTFT 中位数比 A 慢 70%，E2E 几乎持平。

---

## 二、根因诊断：A 模式没有走完整 LangGraph 流程

### 2.1 当前 A 模式的实际流程

```
脚本内直接调 LLM API（_call_llm_with_usage）
  → intent_classify prompt（裸 API 调用 #1）
  → param_extract prompt（裸 API 调用 #2）
  → 结束（无工具执行、无 LangGraph 框架开销、无日志写入）
```

**A 模式测的不是"旧架构"，而是"裸的两次 HTTP 请求"。**

### 2.2 B 模式的实际流程

```
POST /api/ai/chat/stream
  → FastAPI 路由层
  → PromptBuilder 构建消息（加载 Redis 历史 + 注入记忆 + system.yaml 格式化）
  → LangGraph _graph.astream()
    → node_llm_with_tools（Function Calling，含 tools schema）
    → 条件路由 → execute_tool / execute_rag / execute_llm
      → 工具执行：等待 SpringBoot / SQL Agent / RAG 管道
    → node_summarize（多步时）
  → SSE 流式输出
  → 会话日志写入（conversation_logs + ai_sessions 表）
```

**B 模式承担了完整的框架开销。**

### 2.3 对比不公平的点

| 开销项 | A 模式 | B 模式 | 影响 |
|---|---|---|---|
| PromptBuilder（历史/记忆加载）| 无 | 有 | ~50-200ms |
| LangGraph 图执行 | 无 | 有 | ~20-50ms |
| 工具执行（SpringBoot/SQL/RAG）| 无 | 有 | ** dominates，200ms-20s 不等** |
| 会话日志写入（DB）| 无 | 有 | ~50-100ms |
| tools schema 注入 | 无 | 有（prompt 更长）| prefill 时间增加 |

**结论：A 和 B 测的不是同一层的东西。**

---

## 三、为什么必须让 A 模式走完整 LangGraph 流程

### 3.1 面试追问场景

面试官如果看简历写"FC 将延迟从 X ms 降到 Y ms"，最自然的追问是：

> "你测的是端到端延迟还是纯 LLM 延迟？两次调用和一次调用分别走了系统的哪些环节？"

如果 A 模式只是裸调 API，而 B 模式走了完整系统，这个数字站不住脚。

### 3.2 正确的对比应该是

```
A 模式（对照组）= 完整 LangGraph 流程 + node_classify_intent（两次 LLM）+ 工具执行 + 日志
B 模式（实验组）= 完整 LangGraph 流程 + node_llm_with_tools（单次 Function Calling）+ 工具执行 + 日志

差异 = 2 次 LLM 调用 vs 1 次 LLM 调用（其余环节完全相同）
```

### 3.3 历史两次调用的真实路径

从 `intent_router.py` 源码确认：

1. **第一次 LLM**：`_classify_with_llm()` → `intent_classify.yaml` prompt → `_call_llm()`（历史上 `INTENT_LLM_MODEL=qwen-flash`）
2. **第二次 LLM**：`_extract_parameters_with_llm()` → `param_extract.yaml` prompt → `llm_client.generate()`（历史上 `CHAT_LLM_MODEL=qwen-plus`）

但 benchmark 任务清单 line 132 明确要求：
> "A 模式的 intent_classify 要走同模型（qwen-plus），否则比较不公平"

所以 A 模式的两次调用都应使用 qwen-plus（和 B 模式同一个模型）。

---

## 四、数据中的其他异常

### 4.1 KB 类 query B 模式异常慢（15-27s）

| query | B E2E |
|---|---|
| "加班算不算工时" | 21.6s |
| "请假期间要填工时吗" | 15.8s |
| "工时审核流程是什么" | 24.9s |

原因：KB 类在 B 模式中走 `execute_rag` → `langchain_rag_stream_query()`，包含：
- Milvus 向量检索
- BM25 关键词检索
- CrossEncoder Reranker
- LLM 生成回答（stream）

而 A 模式的 KB 类 query 只是两次裸 LLM 调用（没有 RAG），所以 A 只要 5-10s，B 要 15-27s。

**这进一步证明对比不公平：A 没有执行 RAG 管道。**

### 4.2 SQL 类 query B 模式很快（2-5s）

| query | B E2E |
|---|---|
| "本月工时最多的前5人" | 4.1s |
| "谁还没填本周工时" | 2.7s |

原因：SQL 类在 B 模式中走 `execute_tool` → `sql_query` tool，但 SQL Agent 调用的数据库在当前环境中不可达（或只读账号未配置），所以工具执行很快失败/返回空，E2E 主要由 LLM 调用时间决定。

### 4.3 B 模式 token 无法获取

当前 SSE 流中不包含 `usage` 字段，B 模式的 token 消耗无法精确统计。需要：
- 方案 A：在 `stream_agent_response` 的 `done` 事件中附加 `usage`
- 方案 B：在脚本中通过 `tiktoken` 估算

---

## 五、建议的修正方案

### 5.1 最小改动方案（推荐）

在 `langgraph_agent.py` 的 `node_llm_with_tools` 开头加一个环境变量开关：

```python
async def node_llm_with_tools(state: AgentState) -> dict:
    if os.getenv("BENCHMARK_FORCE_FALLBACK") == "1":
        return await node_classify_intent(state)
    # ... 原有逻辑
```

基准测试脚本中：
- A 模式：设置 `BENCHMARK_FORCE_FALLBACK=1`，然后调 `/api/ai/chat/stream`
- B 模式：不设置，正常调 `/api/ai/chat/stream`

**优点**：改动极小（3 行代码），A/B 完全走同一代码路径，公平。

### 5.2 备选方案（不改生产代码）

在脚本中直接实例化 LangGraph 组件，手动执行：

```python
# A 模式
state = AgentState(...)
result = await node_classify_intent(state)
# 根据 intent 继续路由到 execute_tool/rag/llm
# 手动计时
```

**缺点**：需要复制 LangGraph 的路由逻辑，容易和真实流程 diverge。

### 5.3 关于 token 统计的修正

由于 B 模式 SSE 中无 usage，建议：
1. 在 `stream_agent_response` 的 `done` 事件中注入 `{"usage": {"prompt_tokens": X, "completion_tokens": Y}}`
2. 或者：A 模式的 token 也不记录（两者都无），只对比 E2E 延迟

---

## 六、修正后的预期结果

如果按 5.1 方案修正，预期结果应该是：

| 指标 | A（两次 LLM）| B（Function Calling）| 预期降幅 |
|---|---|---|---|
| E2E P50 | ~10-12s | ~5-7s | **~40-50%** |
| 说明 | 2 次 qwen-plus 调用 | 1 次 qwen-plus 调用 | 少一次网络往返 |

TTFT 的对比意义不大（A 的 TTFT = 第一次调用耗时，B 的 TTFT = 首 token 时间，定义不同），简历上主要写 E2E 对比。

---

## 七、待办

| # | 任务 | 负责人 | 优先级 |
|---|---|---|---|
| 1 | 加 `BENCHMARK_FORCE_FALLBACK` 开关到 `langgraph_agent.py` | Claude | P0 |
| 2 | 修改 `bench_fc_vs_two_calls.py`：A 模式走 `/api/ai/chat/stream` + fallback 开关 | Claude | P0 |
| 3 | 重跑全量 50 条 | Claude | P0 |
| 4 | （可选）SSE done 事件注入 usage | Claude | P1 |
| 5 | 回填 benchmark-tasks-2026-04.md | 用户 | P1 |
