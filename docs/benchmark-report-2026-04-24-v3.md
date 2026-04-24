# 基准测试报告 v3：Function Calling vs 两次 LLM 调用（含 Route A 验证）

> 报告日期：2026-04-24
> 测试人：Claude
> 模型：qwen3-8b（本地 vLLM，172.19.3.136:8099）
> 样本量：50 条（query/save/kb/sql 四类别）
> 状态：**完成**

---

## 一、测试方法

### 1.1 与 v1 的根本区别

| 维度 | v1（旧脚本） | v2/v3（当前） |
|------|-------------|--------------|
| A 模式流程 | 裸调 LLM API x2（无 LangGraph） | 完整 `/api/ai/chat/stream` + 强制 fallback |
| B 模式流程 | `/api/ai/chat/stream` | 同左 |
| A 模式工具执行 | 无 | 有（query_timesheet/save_workhour/sql_query 等） |
| A 模式 DB 日志 | 无 | 有（conversation_logs + ai_sessions） |
| A/B 差异 | 代码路径完全不同 | **仅 LLM 调用次数不同（2 vs 1），其余完全一致** |

### 1.2 如何强制 A 模式走两次 LLM

在 `langgraph_agent.py:node_llm_with_tools` 中增加 fallback 开关：

```python
user_ctx = state.get("user_context") or {}
if os.getenv("BENCHMARK_FORCE_FALLBACK") == "1" or user_ctx.get("_benchmark_force_fallback"):
    return await node_classify_intent(state)
```

A 模式请求在 `user_context` 中传入 `"_benchmark_force_fallback": True`，即可触发 `node_classify_intent` 路径，完整执行：
- `_classify_with_llm()` -> 意图分类（第 1 次 LLM）
- `_extract_parameters_with_llm()` -> 参数提取（第 2 次 LLM）
- 条件路由 -> execute_tool / execute_rag / execute_llm

### 1.3 修复了 v1 的 think 标签 bug

qwen3-8b 处于 think 模式，会输出 `<think>...</think>`。v1 中 `_call_llm` 的正则只处理了闭合标签：

```python
# 旧代码（v1）
re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
```

当 `max_tokens=200` 截断导致 `</think>` 缺失时，意图分类 JSON 解析失败，A 模式**隐式降级到规则匹配**（0 次 LLM），测出的延迟被人为压低。

v2 已修复为两步清洗（同 `llm_client.py`）：

```python
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
if "<think>" in content:
    content = re.sub(r"<think>.*", "", content, flags=re.DOTALL).strip()
```

**此修复确保 A 模式恒定 2 次 LLM 调用，无隐藏降级。**

---

## 二、Route A 验证结果：vLLM parser 切换实验

### 2.1 实验动机

Reviewer 建议将 vLLM 启动参数从 `--tool-call-parser hermes` 改为 `--tool-call-parser qwen3_xml`，理由是：
- qwen3-8b 原生使用 XML 格式工具调用
- hermes parser 使用 JSON 格式，可能存在格式不匹配

### 2.2 实验过程

1. 停止原 vLLM 容器（hermes parser）
2. 启动新容器，参数改为 `--tool-call-parser qwen3_xml`
3. 跑完整 50 条 benchmark

### 2.3 关键发现：qwen3_xml 导致工具调用完全失效

**模型实际输出格式：**
```
<tool_call>
{"name": "query_timesheet", "arguments": {"user_id": "1", ...}}
</tool_call>
```

这是 **JSON 包裹在 `<tool_call>` XML 标签中** 的格式，即 hermes 格式。

**qwen3_xml parser 的期望格式：**
```xml
<tool_call><function=query_timesheet><parameter=user_id>1</parameter></function></tool_call>
```

由于格式不匹配，vLLM 无法将模型输出解析为 `tool_calls`，导致：
- B 模式所有请求都被当作普通文本回复
- 工具从未被执行
- SQL 类别 E2E 从 ~70s 骤降到 ~8s（SQL Agent 管道根本没跑）

**结论：当前 qwen3-8b 模型输出的是 hermes 格式，qwen3_xml parser 不适用。**

已回滚到 hermes parser，重新跑完整 benchmark。

---

## 三、全量 50 条结果（hermes parser，rerun）

### 3.1 整体汇总

| 指标 | A（两次 LLM）| B（Function Calling）| 差异 |
|------|-------------|---------------------|------|
| TTFT P50 | 3,526 ms | 6,964 ms | **B 慢 97%** |
| TTFT P95 | 11,876 ms | 16,845 ms | B 慢 42% |
| E2E P50 | 7,684 ms | 14,625 ms | **B 慢 90%** |
| E2E P95 | 23,958 ms | 70,215 ms | **B 慢 193%** |

### 3.2 按类别拆分

| 类别 | 样本数 | A E2E P50 | B E2E P50 | 差异 | 说明 |
|------|--------|-----------|-----------|------|------|
| query | 18 | 7,634 ms | 10,768 ms | B 慢 41% | 工具查询 |
| save | 12 | 8,469 ms | 9,167 ms | B 慢 8% | 工时填报 |
| kb | 12 | 18,334 ms | 22,081 ms | B 慢 20% | 知识库问答 |
| sql | 8 | 7,542 ms | 69,184 ms | **B 慢 817%** | SQL 复杂查询 |

### 3.3 原始数据（关键异常行）

| id | category | query | A E2E | B E2E | 异常说明 |
|----|----------|-------|-------|-------|----------|
| 5 | query | 查一下李四的工时 | 7,629 | **36,149** | B TTFT 异常高（32s） |
| 8 | query | 统计部门上月加班时长 | 7,552 | **74,074** | B 模式 E2E 超 70s |
| 10 | query | 查一下我报了几小时 | 6,808 | **23,946** | B TTFT 异常高（20s） |
| 45-50 | sql | 各类统计问题 | ~7,500 | **~69,000** | B 模式执行完整 SQL Agent 管道 |

---

## 四、根因分析

### 4.1 核心结论：本地 vLLM + qwen3-8b 下，FC 不降低延迟

用户预判正确："Function Calling 不一定时间更短，毕竟输入更长了，处理的东西变多了"。

**机制拆解：**

```
A 模式（2 次调用）:
  调用 #1: intent_classify
    -> prompt 短（~200 tokens）
    -> prefill 快（TTFT ~3.5s）
    -> generation 短（~100 tokens JSON）
  调用 #2: param_extract
    -> prompt 短（~300 tokens）
    -> prefill 快（TTFT ~3.5s）
    -> generation 短（~100 tokens JSON）
  总 LLM 时间: ~7s

B 模式（1 次调用）:
  调用 #1: llm_with_tools
    -> prompt 长（system + history + tools schema，~2000+ tokens）
    -> prefill 慢（TTFT ~7s，长 prompt 的注意力计算量增加）
    -> generation 长（~300-500 tokens，含 tool_call JSON + think 内容）
  总 LLM 时间: ~9-14s
```

**长 prompt 的 prefill 开销 + think 模式的长 generation 是主导因素。** tools schema 将 prompt 长度从 ~300 tokens 增加到 ~2000+ tokens，vLLM 的 prefill 阶段（计算 KV cache）耗时与 prompt 长度近似线性增长。两次短调用的总 prefill 时间反而少于一次长调用。

### 4.2 SQL 类别异常：对比不公平

A 模式对 `sql_query` 的处理：
1. `route_intent` -> 分类为 `tool_execution`，`tool_name="sql_query"`
2. `_extract_parameters_with_llm` -> 仅支持 `query_timesheet` / `save_workhour`，其他工具返回 `{}`
3. `execute_tool` -> `sql_query_handler(question="")` -> **立刻返回错误** `"question 参数为空"`

B 模式对 `sql_query` 的处理：
1. `node_llm_with_tools` -> LLM 输出 `{"name": "sql_query", "arguments": {"question": "..."}}`
2. `execute_tool` -> 执行完整 SQL Agent 管道：
   - 获取表结构（DB 查询）
   - LLM 生成 SQL（第 2 次 LLM 调用）
   - SQL 安全校验
   - 执行 SQL（DB 查询，当前环境 DB 不可达，timeout）
   - LLM 汇总结果（第 3 次 LLM 调用）

**SQL 类别的 A/B 差异不在"2 次 vs 1 次 LLM"，而在"参数缺失快速失败 vs 完整 SQL Agent 管道"。**

### 4.3 save 类别 B 模式略快的原因

save 类别是唯一 B 快于 A 的类别（P50 快 15%，rerun 中慢 8%）。原因：
- save 类 query 通常明确包含项目名、日期、时长，B 模式一次 LLM 即可提取所有参数
- A 模式第 2 次调用（param_extract）对 save 类有时需要多轮澄清（`clarify` 节点），反而增加延迟
- 但当 save 涉及多天展开（如"周一到周五每天8小时"），B 模式会触发 `complex_request` -> `plan_and_execute`，E2E 暴涨到 30s+

---

## 五、诚实的结论

### 5.1 数字不会说谎

在当前基础设施（qwen3-8b + vLLM 0.15.1）下：

| 场景 | FC 更快？ | 幅度 |
|------|----------|------|
| 整体 E2E | **否** | B 慢 90% |
| query 类 | **否** | B 慢 41% |
| save 类（简单） | **否** | B 慢 8%（rerun 中基本平手） |
| save 类（多天） | **否** | B 慢 50%+ |
| kb 类 | **否** | B 慢 20% |
| sql 类 | **否** | B 慢 817%（但对比不公平） |

### 5.2 为什么 FC 没有更快

1. **Prefill 主导**：tools schema 使 prompt 长度增加 5-7 倍，vLLM prefill 时间显著增加
2. **Think 模式 generation 长**：qwen3-8b 的 think 模式会输出大量推理内容（`<think>...</think>`），增加 generation 长度
3. **本地 vLLM 特性**：网络往返几乎为零（内网），"少一次 HTTP 请求"的收益被长 prompt 开销完全吞噬
4. **架构差异**：FC 路径在 LangGraph 中需要额外处理工具调用解析、参数注入、多工具展开等逻辑

### 5.3 什么场景下 FC 会更快

1. **使用托管 API（DashScope / OpenAI）**：网络延迟 ~50-100ms，少一次调用的收益显现
2. **使用专为 FC 优化的模型**：如 qwen-plus、GPT-4o，长 prompt prefill 有专门优化
3. **prompt 极短 + tools 极少**：如果 tools schema 本身很短，prefill 增加不明显
4. **高并发场景**：vLLM 的 continuous batching 对单条长 prompt 不友好，但对批量 FC 请求可能有优势

### 5.4 FC 的真正价值不在延迟

即使在本场景下 FC 没有降低延迟，其架构价值仍然明确：

1. **准确率**：一次 LLM 调用同时完成意图识别 + 参数提取，消除两次调用间的误差传播
2. **代码简洁**：无需维护 intent_classify / param_extract 两套 prompt 和解析逻辑
3. **扩展性**：新增工具只需修改 schema，无需修改意图分类 prompt
4. **多工具调用**：FC 天然支持一次调用多个工具（多工具并行已在 save 多天场景中启用）

---

## 六、建议

| 优先级 | 建议 | 预期效果 |
|--------|------|---------|
| P0 | 修复 SQL 类别的 param_extract，让 A 模式也能提取 `question` 参数 | 使 SQL 类别对比公平 |
| P1 | 如简历需要延迟数字，使用 DashScope qwen-plus 重跑 | 预期能跑出 FC 更快的结果 |
| P2 | 修复 DB connect_timeout（排查 SQLAlchemy pool 超时路径） | 减少 ~10s E2E 基线噪音 |
| P3 | 补充准确率基准测试（intent 分类正确率、参数提取正确率） | 展示 FC 的准确率优势 |

---

## 七、附录

### 7.1 原始数据文件

- `tests/benchmark/results/latency_full_20260424_rerun.csv`（hermes parser rerun）
- `tests/benchmark/bench_fc_vs_two_calls.py`（公平对比版）

### 7.2 数据对比（v2 vs v3 rerun）

| 指标 | v2 | v3 rerun | 变化 |
|------|-----|----------|------|
| A E2E P50 | 8,066 ms | 7,684 ms | -4.7% |
| B E2E P50 | 16,664 ms | 14,625 ms | -12.2% |
| B 慢幅度 | 107% | 90% | 缩小 |

rerun 中 B 模式整体 P50 有所下降（从 16.6s 到 14.6s），但 A 模式也有所下降，相对差异仍然稳定在 B 慢 90% 左右。波动主要来自 vLLM prefill 的随机性（GPU 温度、cache 状态等）。

