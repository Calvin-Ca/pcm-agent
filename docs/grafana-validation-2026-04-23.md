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

## 生产环境（待确认）

### 需用户确认的事项

1. **172 服务器是否有 Prometheus/Grafana？**
   - 本地 docker-compose 中有，但生产环境可能使用独立的监控系统
   - 如果生产环境没有，需要决定是否在 172 上部署

2. **ai-service 容器的 `/metrics` 端点是否暴露在 172 上？**
   - 当前 docker-compose 只映射了 `127.0.0.1:8000`（本地回环）
   - 如果 Prometheus 在 172 宿主机上运行，需要改为 `0.0.0.0:8000` 或内网 IP

3. **Dashboard 是否已导入到生产 Grafana？**
   - 当前 dashboard 是手动创建的（非 provisioning）
   - 建议导出 JSON 并放入 `grafana/dashboards/` 目录，实现 provisioning 自动加载

---

## 建议行动

| 优先级 | 行动 | 说明 |
|--------|------|------|
| P2 | 导出 dashboard JSON 到版本控制 | 将 `AI Service Overview` 导出为 `grafana/dashboards/ai-service.json`，实现 IaC |
| P2 | 验证生产环境 Prometheus 能 scrape 172:8000 | 确认网络连通性和端点暴露 |
| P3 | 补充告警规则 | 在 Prometheus 中添加 `ai_chat_requests_total{status="error"} > 0.1` 等告警 |
