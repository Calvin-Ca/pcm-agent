# Grafana 看板验证报告 — 2026-04-23

## 本地开发环境验证

### Prometheus 状态

| 检查项 | 结果 | 说明 |
|--------|------|------|
| ai-service target | ✅ UP | `ai-service:8000/metrics`，last scrape 正常 |
| 指标列表 | ✅ 完整 | 21 个 ai_* 指标全部注册 |
| 实时指标 | ⚠️ 部分为空 | `ai_chat_active_requests=0`（无活跃请求）；counter 类型指标需实际流量 |

### Grafana Dashboard — "AI Service Overview"

| 检查项 | 结果 | 说明 |
|--------|------|------|
| Dashboard 存在 | ✅ | uid=`ai-service-overview`，创建于 2026-04-09 |
| Datasource | ✅ | Prometheus (`http://prometheus:9090`) |
| 面板数量 | 8 个 | 见下方详细列表 |
| PromQL 语法 | ✅ | 全部 8 个查询语法正确 |
| 指标名称匹配 | ✅ | 与 Prometheus 实际指标名称一致 |

### 面板清单与指标对照

| # | 面板名称 | PromQL 查询 | 指标存在 | 数据可用 |
|---|----------|-------------|----------|----------|
| 1 | 请求 QPS | `rate(ai_chat_requests_total[5m])` | ✅ | ⚠️ 需流量 |
| 2 | 请求延迟 P95 | `histogram_quantile(0.95, rate(ai_chat_request_duration_seconds_bucket[5m]))` | ✅ | ⚠️ 需流量 |
| 3 | 错误率 | `rate(ai_chat_requests_total{status="error"}) / rate(ai_chat_requests_total)` | ✅ | ⚠️ 需流量 |
| 4 | 活跃请求数 | `ai_chat_active_requests` | ✅ | ✅ 当前=0 |
| 5 | 工具调用分布 | `sum by (tool_name) (ai_tool_calls_total)` | ✅ | ⚠️ 需流量 |
| 6 | 工具调用延迟 P95 | `histogram_quantile(0.95, rate(ai_tool_call_duration_seconds_bucket[5m]))` | ✅ | ⚠️ 需流量 |
| 7 | LLM 调用延迟 P95 | `histogram_quantile(0.95, rate(ai_llm_call_duration_seconds_bucket[5m]))` | ✅ | ⚠️ 需流量 |
| 8 | 意图分布 | `sum by (intent) (ai_chat_requests_total)` | ✅ | ⚠️ 需流量 |

> **⚠️ 需流量**：counter/histogram 类型指标需要 ai-service 处理实际请求后才能产生非零数据。本地刚启动无流量，属于正常现象。

### 潜在断点

| 断点 | 风险等级 | 说明 |
|------|----------|------|
| counter 指标在 `rate()` 中可能为 0 | 低 | 无流量时的正常表现，有流量后自动恢复 |
| `ai_chat_requests_total{status="error"}` 标签 | 中 | 需确认代码中 error 状态时确实设置了 `status="error"` 标签 |
| histogram bucket 覆盖范围 | 低 | 默认 bucket 可能不覆盖超长延迟（如 LLM 调用 >30s） |

---

## 生产环境实测（2026-04-23 E2E 流量后）

### 监控栈部署状态

| 组件 | 生产 172 运行中？ | 说明 |
|------|------------------|------|
| `ai-assistant-service` `/metrics/` | ✅ | 端点需带尾部斜杠（无斜杠返回 307），21 个 `ai_*` 指标都已注册 |
| `prometheus` 容器 | ❌ **未启动** | `docker-compose.prod.yml` 用 `profiles: monitoring` 默认跳过 |
| `grafana` 容器 | ❌ **未启动** | 同上 |
| Provisioning 配置 | ✅ 已就位 | `grafana/dashboards/ai-service.json` 和 `grafana/datasources/prometheus.yml` 在 git，启动 profile 后会自动加载 |

**结论**：生产监控栈从未启动过，dashboard 也从未在 172 实际访问过。启动命令：

```bash
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose --profile monitoring up -d prometheus grafana"
```

### E2E 流量后 `/metrics` 真实值（9 个用例跑完后 ~5 分钟）

**✅ 正常采集：**

| 指标 | 实测值 | 含义 |
|------|--------|------|
| `ai_tool_calls_total{tool_name="query_timesheet"}` | 5.0 | T1+T9+之前若干次，吻合 |
| `ai_tool_calls_total{tool_name="query_project"}` | 1.0 | T2，吻合 |
| `ai_tool_calls_total{tool_name="compute_statistics"}` | 2.0 | T3+T7 错选，吻合 |
| `ai_tool_call_duration_seconds_sum{query_timesheet}` | 0.6313s | 真实耗时，符合预期 |
| `ai_rag_queries_total{status="success"}` | 1.0 | T6，吻合 |
| `ai_rag_query_duration_seconds_sum` | 5.48s | T6 实际耗时，吻合 |
| `ai_llm_tokens_total{token_type="prompt"}` | 27,120 | E2E 累计，正常 |
| `ai_llm_tokens_total{token_type="completion"}` | 1,679 | 同上 |
| `ai_service_info` | `version="1.2", llm_model="qwen3-8b", phase="3"` | 服务信息正确 |

### 🔴 指标断点列表

| # | 指标 | 实测值 | 期望值 | 严重度 | 根因 |
|---|------|--------|--------|--------|------|
| G1 | `ai_chat_request_duration_seconds_sum{intent="unknown"}` | **0.0067s**（17 次请求总耗时） | 每次 20-30s，17 次应 ~400s | 🔴 严重 | `chat.py:279-283` 的 `finally` 在 StreamingResponse 对象返回那一刻即执行，没等 async generator 消费完。测的是构造响应对象的微秒级耗时 |
| G2 | `ai_chat_requests_total{intent=...}` label | 全部 `intent="unknown"` | tool_execution / knowledge_qa / general_chat / clarify 分桶 | 🟠 高 | 同 G1：`_intent` 在外层 `finally` 读取时尚未被 generator 更新 |
| G3 | `ai_llm_calls_total{call_type="function_calling",status="error"}` | 17.0（**全 error**，duration_sum=0.08s） | qwen-plus 时代为 success；现为 error | 🔴 严重 | vLLM qwen3-8b 缺 `--enable-auto-tool-choice` + `--tool-call-parser`，function calling 路径 resp.status!=200，降级为 generate。这也解释了 E2E T4 为何返回"伪 tool_calls JSON"（对应 e2e-regression 的 E2 根因） |
| G4 | `ai_tool_calls_total{status="error"}` | **0 条记录** | T2（404）、T3/T7（参数校验失败）应至少 3 条 | 🟠 高 | `task_executor.py` 只按 HTTP 层 success 记状态，不看 `result.success` 业务字段 |
| G5 | LLM duration bucket `le="0.5"` 对 function_calling = 17 | 17/17 都在 0.5s 内 | 真实 LLM 调用至少 1-3s | 🟠 中 | 同 G3：压根没真调到推理 |

### 面板级影响

| # | 面板 | 实测状态 | 影响原因 |
|---|------|----------|----------|
| 1 | 请求 QPS | 可算（17 次/10min） | 正常 |
| 2 | 请求延迟 P95 | **严重失真**（显示 <1s，实际 20-30s） | G1 |
| 3 | 错误率 | 显示 0%（实际 4/9 FAIL） | G4 |
| 4 | 活跃请求数 | 正常 | — |
| 5 | 工具调用分布 | 正常（3 个工具有计数） | — |
| 6 | 工具调用延迟 P95 | 正常（histogram 真实） | — |
| 7 | LLM 调用延迟 P95 | **失真**（function_calling 面板全 0.5s 以下） | G3 |
| 8 | 意图分布 | **完全失效**（全 unknown） | G2 |

---

## 建议行动（按优先级）

| 优先级 | 行动 | 说明 |
|--------|------|------|
| P0 | 修 `chat_stream` 埋点位置（G1 + G2） | 把 REQUEST_COUNT / REQUEST_LATENCY 埋点移到 `generate_stream` 内部的 try/finally，而非外层 |
| P0 | 修 vLLM tool parser（G3 + G5） | 启动参数加 `--enable-auto-tool-choice --tool-call-parser hermes`（或对应 qwen3 的 parser），或临时把 CHAT_LLM_API_BASE 指回 DashScope qwen-plus |
| P1 | `task_executor.py` 按 `result.success` 区分工具错误（G4） | 业务失败时 `TOOL_CALL_COUNT.labels(status="error").inc()` |
| P1 | 生产启用 monitoring profile | `docker compose --profile monitoring up -d prometheus grafana`，验证 Prometheus 能 scrape `ai-assistant-service:8000/metrics/`（容器内网） |
| P2 | 补 Prometheus 告警规则 | 如 `ai_chat_requests_total{status="error"} / ai_chat_requests_total > 0.1` 持续 5min |
| P2 | 把 `<think>` 泄漏过滤移到 `llm_client.py` 返回路径 | 影响 Grafana 面板旁观察不到，但和 E2E 一起修更合算 |
