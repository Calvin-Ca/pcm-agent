# 05 性能、容量与可观测性

## 1. 性能必须拆分

总耗时必须拆为：

```text
总 E2E
  = Agent 纯编排开销
  + LLM 排队与推理
  + RAG 检索
  + Tool/SpringBoot 等待
  + SSE 传输
```

SpringBoot 等待时间可以作为共享指标，但必须单独标记，不能全部归入 Agent 编排耗时。

## 2. 必测场景

- 健康检查。
- 普通聊天。
- 单次 Function Calling 直接回答。
- 单个只读工具。
- 单个写工具 dry-run。
- RAG one-shot。
- A-RAG 多步查询（如生产启用）。
- SQL Agent（如生产启用）。
- 批量工时解析。
- 多工具复杂任务。
- 长会话和接近上下文上限的请求。

## 3. 指标与建议门槛

| 指标 | 建议门槛 |
|---|---:|
| 健康检查 P95 | `< 500 ms` |
| SSE 建连/首事件 P95 | `< 1 s` |
| Agent 纯编排开销 P95 | `< 500 ms` |
| Agent 5xx 错误率 | `< 1%` |
| SSE 正常完成率 | `>= 99%` |
| 普通聊天/单工具 E2E P95 | `< 30 s` |
| RAG/SQL/复杂任务 E2E P95 | `< 60 s` |
| OOM、进程崩溃 | `0` |

“首字节”可能只是 SSE `start` 事件，必须同时记录第一个用户可见回答 token 的有效 TTFT。

历史本地 vLLM 基线不能直接作为新版本结论。发布前先在生产等价环境采集 P50/P95/P99，再冻结为本次版本门槛。

## 4. 容量与稳定性

- 以预计峰值并发的 `1.5 倍` 进行容量验证。
- 至少包含 10、20、50 并发阶梯压测；如实际峰值更高则相应增加。
- 每个阶梯记录吞吐量、错误率、TTFT、E2E、排队时间、Token/s 和资源利用率。
- 最终容量档至少持续 30 分钟。
- 另做 4～8 小时稳定性测试，观察内存、连接、句柄和 GPU 显存趋势。
- 验证限流、最大并发和超时配置有效。
- 不能只用 `hello` 压测；流量比例应接近真实业务分布。

稳定性阻断条件：

- 任何 OOM、进程崩溃或无界内存增长。
- 错误率持续超过 1%。
- 随并发增加出现不可恢复的延迟恶化。
- LLM 或 Tool 超时造成任务永久占用。

## 5. Token 与容量成本

按场景统计：

- prompt/completion token P50/P95。
- 每个用户任务的 LLM 调用次数。
- Function Calling tools schema 占用。
- RAG 上下文 token。
- A-RAG 平均循环次数与 token。
- 批量请求 token 截断率。
- 本地 vLLM 的 GPU 时间与排队时间；托管模型则计算单请求成本和日预算。

## 6. 上线监控

现有指标：

- `ai_chat_requests_total`
- `ai_chat_request_duration_seconds`
- `ai_chat_active_requests`
- `ai_tool_calls_total`
- `ai_tool_call_duration_seconds`
- `ai_llm_calls_total`
- `ai_llm_call_duration_seconds`
- `ai_llm_tokens_total`
- `ai_rag_queries_total`
- `ai_rag_query_duration_seconds`

上线前应补充或能从 trace 计算：

- 有效 TTFT。
- Agent 纯编排时间和下游等待时间。
- LLM 排队时间。
- Function Calling 文本降级次数及解析结果。
- 规则路由/备用模型降级次数。
- Agent 循环次数、重复调用和撞顶次数。
- RAG 空召回率、Milvus→FAISS 降级次数。
- 权限拒绝和写安全阀命中次数。
- SSE 异常中断次数。
- 每请求模型调用次数和 Token。

## 7. 告警验收

必须通过人工制造故障验证告警，而不是只确认面板存在：

- 5xx 错误率超过阈值。
- LLM P95 或错误率异常。
- Tool P95 或超时率异常。
- RAG no-results/error 异常升高。
- 活跃请求持续堆积。
- 进程重启、OOM 或容器不可用。
- Redis、Milvus、模型服务和 SpringBoot 不可达。

每条告警需记录触发条件、通知渠道、负责人和恢复判定。

## 8. 现有性能测试

本轮生产等价环境性能测试、RAG 瓶颈定位、本地/API 模式对比和 500 人容量评估见：

- [performance-test-report-2026-08-10.md](performance-test-report-2026-08-10.md)

```powershell
cd fastapi-service
..\.venv\Scripts\python.exe -m pytest tests/performance/test_response_time.py -v -s
```

```powershell
cd fastapi-service
locust -f tests/performance/locustfile.py `
  --host=http://localhost:8000 `
  --headless -u 50 -r 5 --run-time 30m `
  --html=reports/agent-preprod-load.html
```

现有响应时间测试的 TTFB 可能只测到首个 SSE 字节，正式报告必须补有效 TTFT。
