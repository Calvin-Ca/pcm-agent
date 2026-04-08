# Phase 3 代码评审报告

> 评审日期：2026-04-08（第二轮）
> 评审范围：T1（密码硬编码）、T2（Prometheus 指标）、T3（流式 RAG）
> T4（vLLM）尚未实施，不在本次评审范围

---

## 总体评价

第二轮修复质量良好。P1 Bug 已修复，流式 RAG 接入（P2a）已完成且实现思路正确。
剩余 1 个 **P2 问题**（RAG 指标未埋点）、1 个 **P3 注意事项**（流式端点指标计时问题）。

---

## 问题状态总览

| 编号 | 问题 | 第一轮 | 第二轮 |
|------|------|--------|--------|
| P1 | task_executor `start_time` NameError | 🔴 存在 | ✅ 已修复 |
| P2a | 流式 RAG 未接入 SSE | 🟡 未接入 | ✅ 已接入 |
| P2b | RAG 指标未埋点 | 🟡 未修复 | 🟡 仍未修复 |
| P3a | ACTIVE_REQUESTS 异常路径泄漏 | 🔵 存在 | ✅ 已修复（移入外层 finally） |
| P3b | intent 字符串匹配脆弱 | 🔵 存在 | 🔵 未改动（可接受） |

---

## 已修复项确认

### P1 — task_executor start_time ✅

`start_time = datetime.now()` 已在 `_tool_start` 下方补回，注释清晰：

```python
_tool_start = _time_module.monotonic()
start_time = datetime.now()  # 被后续 except 块引用，保留
```

### P2a — 流式 RAG 接入 ✅

实现思路正确：在 `stream_agent_response` 的 `astream` 循环中，检测到 `node_name == "execute_rag"` 且 `_streaming_rag_active` 为 True 时，改用 `langchain_rag_stream_query()` 实时 yield chunk，跳过非流式的 `rag_result` 处理。

**一个值得注意的细节**：当 `_streaming_rag_active=True` 时，流式 RAG 查询是在 `execute_rag` 节点完成**之后**才触发的（`astream` 返回的 chunk 是节点执行后的 state delta）。也就是说：

```
LangGraph 内部：node_execute_rag 先完整跑完（等待 LangChain chain.invoke 完成）
→ 才发出 chunk {"execute_rag": state_delta}
→ stream_agent_response 检测到后，再调用 langchain_rag_stream_query 做第二次查询
```

实际上做了**两次检索 + 两次 LLM 生成**（node_execute_rag 跑一次，langchain_rag_stream_query 又跑一次），用户感受到的延迟没有减少，只是最终输出变成流式的。

这不算 Bug，因为用户能看到逐字输出，体验确实有改善。但如果想真正减少延迟，需要用 `astream_events` 替代 `astream`，在节点**开始执行前**就拦截并绕过——改动较大，可作为后续优化项。

### P3a — ACTIVE_REQUESTS ✅

`ACTIVE_REQUESTS.dec()` 已移入外层 `finally` 块，确保异常路径也会递减。

---

## 仍存在的问题

### P2b — RAG 指标仍未埋点 🟡

`metrics.py` 中定义的 `RAG_QUERY_COUNT` 和 `RAG_QUERY_LATENCY` 仍未在 `langchain_rag.py` 的 `query()` 或 `stream_query()` 方法中调用。Grafana 采集不到任何 RAG 维度数据。

**修复位置**：`fastapi-service/app/services/langchain_rag.py`，`query()` 方法（约第 348 行）

**修复内容**（约 12 行，可在 `query()` 方法头尾各加几行）：

```python
async def query(self, question: str) -> Dict[str, Any]:
    import time as _t
    _rag_start = _t.monotonic()
    _rag_status = "success"
    try:
        from app.core.metrics import RAG_QUERY_COUNT, RAG_QUERY_LATENCY
        _has_rag_metrics = True
    except ImportError:
        _has_rag_metrics = False

    try:
        # ... 现有逻辑不变 ...
        if not docs:
            _rag_status = "no_results"
            # ... 返回"未找到信息"
        # ... 生成答案 ...
        return { "success": True, ... }

    except Exception as e:
        _rag_status = "error"
        # ... 现有异常处理 ...

    finally:
        if _has_rag_metrics:
            RAG_QUERY_COUNT.labels(status=_rag_status).inc()
            RAG_QUERY_LATENCY.observe(_t.monotonic() - _rag_start)
```

### P3b — 流式端点指标计时问题 🔵（注意事项，非 Bug）

`chat_stream` 函数的 `finally` 块在 `return StreamingResponse(...)` **之后立即执行**，不是在客户端读完所有流之后执行。因此：

- `REQUEST_LATENCY` 记录的是"构建 StreamingResponse 对象"的耗时（通常 < 1ms），不是"完整流式响应"的耗时
- `ACTIVE_REQUESTS` 计数也会在流还没发完时就递减

这是 SSE/流式响应的固有限制，无法在当前架构下完美解决（需要在 `generate_stream()` 的 finally 里做才准确，但此时拿不到外层 `_start`）。

**影响评估**：`REQUEST_LATENCY` 对流式端点意义不大，主要看非流式端点的数据就够了。`ACTIVE_REQUESTS` 误差也可接受。不影响上线。

---

## 验收状态

| 任务 | 状态 | 备注 |
|------|------|------|
| T1 密码硬编码 | ✅ 完成 | config.py MYSQL_PASSWORD 默认值已清空 |
| T2 Prometheus 端点 | ✅ 完成 | /metrics 端点已挂载，5 类指标已定义 |
| T2 请求/工具/LLM 指标埋点 | ✅ 完成 | chat.py / task_executor.py / llm_client.py |
| T2 RAG 指标埋点 | 🟡 未完成 | langchain_rag.py 待补 |
| T2 Grafana 看板 | ✅ 完成 | 8 个面板，datasource 已配置 |
| T3 stream_query() 方法 | ✅ 完成 | langchain_rag.py 已实现 |
| T3 流式 RAG 接入 SSE | ✅ 完成 | langgraph_agent.py 已接入（注意：有双次查询问题） |

---

## 可以上线的条件

当前状态**可以上线**，建议：

1. **上线前必做**：修复 P2b（RAG 指标埋点，约 12 行），否则 Grafana 的 RAG 面板无数据
2. **上线后观察**：P3b（流式端点计时不准）属于已知限制，观察非流式端点的延迟数据即可
3. **后续优化**：流式 RAG 的双次查询问题，用 `astream_events` 替代 `astream` 彻底解决

---

## 后续待做事项

| 优先级 | 事项 | 预估 |
|--------|------|------|
| 🟡 立即 | P2b RAG 指标埋点 | 15 分钟 |
| 🔵 上线后 | 流式端点计时改进（astream_events） | 半天 |
| 🔵 上线后 | T4 vLLM 本地部署 | 0.5-1d（依赖 GPU 服务器） |
