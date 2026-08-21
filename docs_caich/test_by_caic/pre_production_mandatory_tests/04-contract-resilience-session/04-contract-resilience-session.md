# 04 接口契约、故障恢复、SSE 与会话

## 1. SpringBoot 接口契约

Agent 团队不验证 SpringBoot 内部实现，但必须验证客户端适配层：

- URL、HTTP Method、Content-Type 和请求体字段正确。
- Authorization、用户、角色和部门上下文正确透传。
- 日期、时区、数字、布尔值和空值编码正确。
- 成功、空结果、业务 4xx、权限 403、服务 5xx 和非 JSON 响应均可解析。
- 下游响应新增无关字段时保持兼容；缺少必需字段时明确失败。
- 超时被限制，异常不会泄露内部堆栈。
- 只读请求可按策略重试；非幂等写请求不得盲目自动重试。
- 工具失败必须向上返回 `success=false`，不能被 Agent 汇总成成功。

每个生产工具至少具备一组成功、一组空结果、一组 4xx、一组 5xx 和一组超时契约测试。

## 2. 依赖故障矩阵

| 故障 | 预期行为 | 阻断条件 |
|---|---|---|
| CHAT LLM 超时/5xx | 有限重试后失败或规则降级 | 无限等待、重试风暴、500 |
| PLANNER LLM 不可用 | 回退 CHAT LLM 或简化路径 | 整个服务不可用 |
| Embedding 不可用 | RAG 明确不可用 | 编造知识答案 |
| Milvus 不可用 | 按设计降级 FAISS并告警 | 静默空召回 |
| Redis 不可用 | 无记忆模式继续或明确失败 | 所有聊天不可用 |
| SpringBoot 超时 | 工具失败，写请求不盲重试 | 输出成功或重复写调用 |
| SpringBoot 非法响应 | 结构化错误 | 未处理异常、500 |
| 客户端断开 SSE | 释放任务和连接 | 后台任务泄漏或继续重复写 |

## 3. SSE 协议

必须验证：

- 正常路径事件顺序合法：`start → thinking/tool_call/response → done`。
- 错误路径包含 `error` 并最终结束。
- chunk 聚合后与非流式接口答案一致。
- RAG 来源 footer 不会覆盖或丢失答案正文。
- 多个 response chunk 不重复、不乱序、不丢失。
- JSON、中文、换行和 emoji 编码正确。
- 客户端取消、网络断开和超时后资源被释放。
- 用户可见消息不包含 reasoning trace、内部 JSON 和 UUID。

要求 SSE 正常完成率 `>= 99%`，协议结构错误率为 `0%`。

## 4. 会话记忆与隔离

- 同一 session 的历史按顺序注入。
- 不同 session、不同用户之间无记忆串线。
- 参数继承、覆盖和清除正确。
- Redis TTL 到期后行为符合预期。
- 最近 10 轮限制和长上下文截断不丢失当前任务关键参数。
- 长期记忆只注入与当前用户相关的数据。
- Redis 故障时不影响无记忆单轮请求。
- 同一个 session 并发请求有明确的顺序或冲突处理策略。

门槛：跨用户/跨 session 污染率为 `0%`，上下文污染率 `< 2%`。

## 5. 重试和幂等

- LLM 网络瞬时错误按照配置重试，非瞬时 4xx 不重试。
- 重试期间并发信号量有效，不产生请求洪峰。
- Tool 只读请求重试后结果不重复聚合。
- 写工具超时场景默认状态未知，Agent 不自动再次提交并声称成功。
- SSE 重连不重复执行上一轮写工具。

## 6. 必跑现有测试

```powershell
cd fastapi-service
..\.venv\Scripts\python.exe -m pytest `
  tests/test_llm_client_retry.py `
  tests/test_retry_util.py `
  tests/test_task_executor_retry.py `
  tests/test_task_executor.py `
  tests/test_session_memory.py `
  tests/test_user_memory.py `
  tests/test_stream_unit.py `
  tests/test_stream_properties.py `
  tests/test_chat_response_aggregation.py `
  tests/test_integration.py -v
```
