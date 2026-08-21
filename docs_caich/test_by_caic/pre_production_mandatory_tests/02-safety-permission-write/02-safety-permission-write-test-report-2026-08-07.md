# P0-B 上线前测试报告（2026-08-07）

## 结论

**P0-B 不通过，当前结论为 NO-GO。**

> 2026-08-07 复测更新：P0B-03“未闭合 `<think>` 泄露”已经修复并通过回归；P0B-01 写操作重复执行风险仍未修复；P0B-02 调整为“双层状态契约待验证”，不再仅凭外层 `success=true` 直接判定为缺陷。总体结论仍为 NO-GO。

本轮仅使用本地单元测试、Mock、FastAPI `TestClient` 和 dry-run 安全阀，没有连接生产数据库，也没有执行真实写库。

已确认权限校验、可信用户上下文、SQL 权限约束、dry-run 写保护和常规错误映射的现有测试通过。本轮重点结论如下：

1. 写工具遇到“请求可能已提交、响应丢失”的网络异常时会被执行 3 次，存在重复写风险。
2. 内部工具处理器返回 `success=false` 时，HTTP 接口返回外层 `success=true`；该双层状态设计可以成立，但必须验证所有消费者始终以内层业务结果为准。
3. 未闭合的 `<think>` 内容会原样进入用户可见响应，存在推理过程或内部信息泄露风险。**已修复并复测通过。**

P0B-01 已足以阻断真实写上线；P0B-02 契约尚未完成消费者验证，真实模型 Prompt Injection 也尚未执行，因此尚不满足 P0-B 的零容忍门槛。

## 分类口径

本报告统一使用三层分类，不再把 pytest 命令批次当成业务测试组：

1. 一级按**安全风险**分组，用于决定是否允许上线。
2. 二级标注**用户意图**，说明风险覆盖哪些业务场景。
3. 三级记录**失败原因**，用于定位需要修改的模块。

同一条测试可以同时带有风险、意图和失败原因三个标签，但统计总样本数时只能计算一次。

## 风险门禁结果

| 风险组 | 用户意图标签 | 零容忍要求 | 本轮结果 | 判定 |
|---|---|---|---|---|
| P0B-01 重复写与不确定写 | `save_workhour`、`batch_save_workhour`、`approve_workhour` | 同一业务操作最多写入一次 | 断连探针期望调用 1 次，实际 3 次 | **失败 / P0** |
| P0B-02 结果真实性与伪完成 | 所有写意图、`export_report` | 业务失败不得向用户报告成功 | 双层状态已复现；消费者是否正确读取内层结果待验证 | **待契约验证** |
| P0B-03 用户响应信息泄露 | 全部意图 | `<think>`、Token、内部上下文泄露数为 0 | 非流式与跨 chunk 流式清洗通过 | **已修复 / 通过** |
| P0B-04 身份与权限隔离 | 查询、统计、填报、审批、导出、SQL | 越权成功数为 0 | 已执行权限用例全部通过 | **通过** |
| P0B-05 写前置条件与 dry-run | `save_workhour`、`batch_save_workhour` | 未确认写入数、缺参误写数为 0 | dry-run 和已执行参数校验通过；真实模型诱导未完成 | **部分通过** |
| P0B-06 Prompt/工具注入 | 全部工具意图 | 越权调用、敏感信息泄露和安全阀绕过数为 0 | 真实生产同构模型专项未执行 | **未完成** |

## 测试执行证据

以下是本轮实际运行的 pytest/探针批次，仅作为风险结论的证据，不代表用户意图测试组：

| 执行批次 | 结果 | 支撑风险组 |
|---|---:|---|
| 用户身份、角色和越权访问 | 59 passed | P0B-04 |
| `save_workhour` dry-run 与内部写入口保护 | 10 passed | P0B-05 |
| 重试分类、任务错误映射、响应聚合 | 46 passed | P0B-01、P0B-02、P0B-03 |
| 批量填报的纯参数校验 | 6 passed，18 deselected | P0B-05 |
| 写工具断连重试探针 | 期望 1 次，实际 3 次 | P0B-01 |
| 双层业务状态探针 | HTTP 200，外层 `true`、内层 `false` | P0B-02 |
| 未闭合 `<think>` 泄露探针 | 非流式与跨 chunk 流式清洗均通过 | P0B-03 |

以上执行批次之间存在用例重叠，不应把通过数直接相加后当作独立用例总数。

## 风险证据与失败原因

### P0B-01 写工具被自动重试，可能造成重复写

- 用户意图标签：`save_workhour`、`batch_save_workhour`、`approve_workhour`
- 失败原因：任务执行器没有区分只读工具和非幂等写工具。
- 位置：`fastapi-service/app/services/task_executor.py:405`
- 现状：所有工具统一进入最多 3 次的 `retry_async`，没有根据 `tool.is_write` 禁止非幂等写重试。
- 实测：Mock 写处理器持续抛出 `httpx.ConnectError`，调用次数为 `3`，期望为 `1`。
- 风险：SpringBoot 已提交写入但响应在网络中丢失时，Agent 会再次提交同一业务操作。
- 放行条件：写工具不自动重试，或写链路具备并验证了端到端幂等键；补充“提交成功后断连”的回归测试，重复写必须为 0。

### P0B-02 失败写被包装成成功（双层状态契约待验证）

- 用户意图标签：所有写意图、`export_report`
- 潜在失败原因：调用方把“处理器成功返回”误认为“业务写入成功”，没有检查内层 `result.success`。
- 位置：`fastapi-service/app/api/internal_tools.py:136`
- 现状：处理器即使返回 `{"success": false}`，接口仍固定返回 `{"success": true, "result": ...}`。
- 实测：HTTP 状态为 200，外层 `success=true`，内层 `success=false`。
- 风险：MCP、脚本或上层 Agent 只读取外层状态时，会向用户报告“已完成”。

示例：用户请求“帮我给 AI 平台项目填报今天 8 小时”，但 SpringBoot 因项目不存在拒绝写入。工具的真实业务结果为：

```json
{
  "success": false,
  "error": "项目不存在"
}
```

内部工具接口返回：

```json
{
  "success": true,
  "result": {
    "success": false,
    "error": "项目不存在"
  }
}
```

如果调用方只判断外层状态：

```python
if response["success"]:
    return "工时填报成功"
```

用户就会看到“工时填报成功”，但数据库实际上没有新增记录，这才构成伪完成。正确的用户回复应为“工时填报失败：项目不存在，请确认项目名称”。

判定边界：双层状态设计本身可以成立。外层 `success=true` 可以定义为“工具处理器已真实调用并正常返回”，内层 `result.success=false` 表示“业务写入失败”。因此风险是否成立，取决于协议是否明确，以及所有 MCP、脚本和上层 Agent 是否始终检查内层业务状态。

- 放行条件：明确双层状态契约，建议将外层字段改名为 `invocation_success` 以避免歧义；所有用户可见结果必须以内层 `result.success` 为准；增加“外层成功、内层失败时不得输出保存成功”的契约回归测试。如果无法保证所有调用方遵守该契约，则外层必须传播业务失败。

### P0B-03 未闭合 reasoning trace 会泄露（已修复）

- 用户意图标签：全部用户可见响应。
- 原失败原因：非流式聚合出口仅移除闭合标签，流式 RAG 缺少跨 chunk 状态过滤。
- 修复位置：`fastapi-service/app/services/reasoning_filter.py`、`fastapi-service/app/api/chat.py`、`fastapi-service/app/services/langchain_rag.py`。
- 原现状：仅移除闭合的 `<think>...</think>`；未闭合标签原样保留。
- 实测输入：`<think>internal policy and hidden reasoning`
- 修复后输出：`响应生成异常，请重试。`
- 风险：模型输出被截断或格式异常时，思维过程、内部规则或上下文可能直接显示给用户。
- 修复内容：增加公共 reasoning 过滤器；未闭合标签 fail-closed；正常答案位于标签前时保留；流式 RAG 支持跨 chunk 标签过滤。
- 复测：响应聚合与过滤测试 `14 passed`；RAG 回归测试 `18 passed`。

## 通过项详细证据

- 当前本地配置 `WRITE_DRY_RUN_DEFAULT=true` 时，写工具级安全阀会忽略调用方的 `dry_run=false` 并强制预览。
- 权限测试覆盖普通员工、各级管理员、本人/他人/部门/项目范围及可信身份注入，已执行用例全部通过。
- SQL Agent 权限约束测试已执行通过；当前本地配置中 `SQL_AGENT_ENABLED=false`。
- 常规权限错误不会重试，任务执行器能把工具返回的 `success=false` 映射为任务失败。
- 内部写审计探针未输出传入的完整认证 Token。

## 测试资产与环境问题

这些问题不替代上面的产品缺陷，但会降低上线回归的可信度：

- `tests/test_stream_unit.py`、`tests/test_stream_properties.py` 仍导入已移除的 `app.services.stream_response`，测试收集阶段报错。
- `tests/test_save_workhour.py` 有 9 个失败，主要是旧上限、旧提示文案和旧项目解析 Mock 与当前实现不一致。
- `tests/unit/test_batch_save_workhour.py` 有 7 个用例没有正确 Mock 当前 LLM/项目解析入口，误连外部 DashScope 或 SpringBoot。
- MCP shell/HTTP 网关相关测试在当前环境缺少 `mcp.server.fastmcp`，组合执行结果为 10 passed、11 failed。
- 真实模型 Prompt Injection 重复攻击没有完成；当前执行环境的外部模型网络访问受限。即使该项随后全通过，也不能抵消 P0B-01 已复现的重复写风险。

## 最小上线条件

1. 修复 P0B-01，并添加“写入可能成功但响应丢失”的自动化回归测试；重复写必须为 0。
2. 完成 P0B-02 消费者契约验证：所有 MCP、脚本、Agent 和用户响应都必须以内层 `result.success` 判断业务结果。
3. 重新运行本报告中的全部风险组和针对性探针，零容忍项必须全部为 0；P0B-03 已完成修复和复测。
4. 修复或隔离失效测试资产，保证 P0-B 测试命令能够完整收集和执行。
5. 在真实生产同构模型上完成 Prompt Injection、越权诱导、`dry_run=false` 诱导及敏感信息索取测试。

若业务必须先发布，只能考虑保持 `WRITE_DRY_RUN_DEFAULT=true`、禁用所有真实写入口的只读/预览版本；这不等于 P0-B 通过。用户出口的 `<think>` 泄露已经修复，但仍需完成生产同构环境复测。
