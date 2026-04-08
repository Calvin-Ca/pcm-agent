# Phase 3 代码评审报告

> 评审日期：2026-04-08
> 评审范围：T1（密码硬编码）、T2（Prometheus 指标）、T3（流式 RAG）
> T4（vLLM）尚未实施，不在本次评审范围

---

## 总体评价

T1、T2、T3 的核心逻辑均已正确实现。发现 1 个 **P1 Bug**（会导致工具调用时崩溃），2 个 **P2 问题**（功能缺失），其余均为低优先级。

---

## 问题清单

### P1 — task_executor.py 存在 NameError（会导致崩溃）🔴

**文件**：`fastapi-service/app/services/task_executor.py`

**问题**：在引入 Prometheus 指标时，原有的 `start_time = datetime.now()` 被误删，但后续 4 处代码仍然引用 `start_time`：

```python
# 第 335/347/353/358 行
execution_time = (datetime.now() - start_time).total_seconds()
```

`start_time` 现在未定义，任何工具执行（成功或失败）触发这几行时都会抛出 `NameError: name 'start_time' is not defined`，导致工具调用全部崩溃。

**修复方案**：在 `_tool_start` 定义的同一行附近补回 `start_time`：

```python
# 在 task_executor.py 的 "执行工具" 注释之后，_tool_start 定义之前，补回：
_tool_start = _time_module.monotonic()
start_time = datetime.now()   # ← 补回这行
```

---

### P2a — 流式 RAG 未接入 SSE 输出（stream_query 未被调用）🟡

**文件**：`fastapi-service/app/services/langgraph_agent.py`

**问题**：`langchain_rag.py` 中的 `stream_query()` 和 `langchain_rag_stream_query()` 方法已实现，但 `langgraph_agent.py` 的 `node_execute_rag`（第 500 行）和 `stream_agent_response` 中仍然调用非流式的 `langchain_rag_query()`。

用户感受：知识问答依然会"卡住"等待完整答案，流式方法虽然写好了但没有被使用。

**修复方案**：在 `stream_agent_response` 中，当 LangGraph 完成后检测到 `rag_result` 时，改用简单的流式输出。或者在 `node_execute_rag` 之前拦截，直接走 `langchain_rag_stream_query`。

最小改动方案（不改 LangGraph 图结构）：在 `stream_agent_response` 里，把 RAG 结果拆成多个小 chunk 发送，模拟流式效果（伪流式）。真正的流式需要绕过 LangGraph 节点。

---

### P2b — RAG 指标（RAG_QUERY_COUNT / RAG_QUERY_LATENCY）未埋点 🟡

**文件**：`fastapi-service/app/services/langchain_rag.py`

**问题**：`metrics.py` 中定义了 `RAG_QUERY_COUNT` 和 `RAG_QUERY_LATENCY`，但 `langchain_rag.py` 的 `query()` 方法没有调用它们。Grafana 看板里没有 RAG 相关面板，但 Prometheus 也采集不到任何 RAG 数据。

**修复方案**：在 `langchain_rag.py` 的 `query()` 方法中添加埋点（参照 llm_client.py 的写法）：

```python
# query() 方法开头
import time as _time
_rag_start = _time.monotonic()
_rag_status = "success"

try:
    from app.core.metrics import RAG_QUERY_COUNT, RAG_QUERY_LATENCY
    _has_metrics = True
except ImportError:
    _has_metrics = False

# query() 方法末尾（finally）
if _has_metrics:
    RAG_QUERY_COUNT.labels(status=_rag_status).inc()
    RAG_QUERY_LATENCY.observe(_time.monotonic() - _rag_start)
```

---

### P3a — 流式端点 ACTIVE_REQUESTS 在异常路径可能不递减 🔵

**文件**：`fastapi-service/app/api/chat.py`，流式端点（约第 167-252 行）

**问题**：`ACTIVE_REQUESTS.dec()` 在 `generate_stream()` 内部的 finally 块之外。当 `generate_stream()` 本身被中断（客户端断开连接）时，`finally` 块是否一定被执行取决于 ASGI 框架对 generator 关闭的处理方式，存在泄漏风险。

**建议**：将 `ACTIVE_REQUESTS.dec()` 移入 `generate_stream()` 的 finally 块：

```python
async def generate_stream():
    try:
        ...
    except Exception as e:
        ...
    finally:
        duration = time.monotonic() - _start
        REQUEST_COUNT.labels(intent=_intent, status="success").inc()
        REQUEST_LATENCY.labels(intent=_intent).observe(duration)
        ACTIVE_REQUESTS.dec()
```

---

### P3b — intent 提取逻辑脆弱（字符串匹配 SSE 事件） 🔵

**文件**：`fastapi-service/app/api/chat.py`，流式端点（约第 213-221 行）

**问题**：

```python
if "tool_call" in event:
    _intent = "tool_execution"
elif "thinking" in event and "knowledge" in event.lower():
    _intent = "knowledge_qa"
```

这是对原始 SSE 字符串做字符串匹配，非常脆弱：
- `"knowledge"` 这个词在其他事件中也可能出现
- `_intent` 只有在最后一个 `elif` 才赋值为 `"general_chat"`，如果没有 `response` 事件（如超时/错误），intent 标签为 `"unknown"`，Grafana 数据会有噪音

低优先级，不影响指标收集的基本功能，但数据准确性稍差。

---

## 各任务验收状态

| 任务 | 状态 | 说明 |
|------|------|------|
| T1 密码硬编码 | ✅ 完成 | config.py MYSQL_PASSWORD 已清空 |
| T2 Prometheus 指标 | ⚠️ 基本完成，有 Bug | task_executor 有 NameError，RAG 指标未埋点 |
| T2 Grafana 看板 | ✅ 完成 | datasources/prometheus.yml + dashboards/ai-service.json，8 个面板 |
| T3 流式 RAG 方法 | ✅ 完成 | stream_query() + langchain_rag_stream_query() 已实现 |
| T3 流式 RAG 接入 | ⚠️ 未接入 SSE | 方法存在但未被 LangGraph 调用 |

---

## 必须修复后才能上线

| 优先级 | 问题 | 文件 | 修复量 |
|--------|------|------|--------|
| 🔴 P1 | task_executor `start_time` NameError | `task_executor.py` 第 320 行附近 | 加 1 行 |
| 🟡 P2a | 流式 RAG 未接入 SSE | `langgraph_agent.py` | 中等改动 |
| 🟡 P2b | RAG 指标未埋点 | `langchain_rag.py` | 约 15 行 |

P3 级别的两个问题可以上线后修复，不阻塞部署。

---

## 下一步

1. **立即修复 P1**（task_executor.py 补回 `start_time = datetime.now()`）
2. **修复 P2b**（RAG 指标埋点，约 15 行）
3. **P2a 流式 RAG 接入**：暂时接受"写好了但未接入"的现状，优先上线监控；流式接入作为下一个独立任务
4. **提交 + 推送**，然后启动 Docker 验证 `/metrics` 端点和 Grafana 看板
