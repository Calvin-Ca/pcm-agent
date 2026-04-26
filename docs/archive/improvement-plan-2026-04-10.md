# AI Service 改进计划

**基于测试报告**：
- `docs/test-reports/layer1-ollama-qwen3-8b-2026-04-09.md`（精度，46.4% 失败率）
- `docs/test-reports/performance-benchmark-ollama-2026-04-09.md`（性能，RAG 10.71s）

**目标**：精度失败率 ≤30%，RAG TTFT ≤3s，主链路 TTFT ≤2s

---

## 优先级总览

| 编号 | 任务 | 优先级 | 预期收益 | 难度 |
|------|------|--------|---------|------|
| A | RAG 延迟优化 | P0 | TTFT 10.71s→<3s | 低 |
| B | Prompt 调优（保守意图） | P0 | 失败率 -15~20pp | 中 |
| C | 工具描述区分 | P1 | tool_name 错误 -50% | 低 |
| D | num_ctx 自适应 | P2 | TTFT 轻微改善 | 低 |

---

## A. RAG 延迟优化（P0）

**当前问题**：RAG 场景 TTFT=10.71s，是 chat 场景的 6.8 倍。瓶颈在 Milvus 向量检索 + CrossEncoder Reranker。

**文件**：`fastapi-service/app/services/langchain_rag.py`

### A-1. 降低 Reranker top_k（必做，改一行）

**当前行为**：向量召回 top_k=20 条，全部送入 CrossEncoder 重排。CrossEncoder 是逐对计算，20 条耗时最多。

**修改方式**：找到 `top_k` 或 `rerank_top_k` 参数，改为 5。

```python
# 修改前（大约）
retriever = vectorstore.as_retriever(search_kwargs={"k": 20})

# 修改后
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
```

> 如果代码中 BM25 和向量检索分开配置 top_k，两个都改为 5。

**验证方式**：修改后重新请求一条知识问答，观察 TTFT 是否降低到 3s 以内。

---

### A-2. 评估是否去掉 CrossEncoder Reranker（可选，改动较大）

**背景**：CrossEncoder（cross-encoder/ms-marco-MiniLM-L-6-v2）是精排模型，可以提升召回质量，但耗时显著。

**如何评估**：
1. 注释掉 Reranker，直接用向量召回的 top_k 结果
2. 手动提问 5-10 条知识问答，判断答案质量是否明显变差
3. 如果质量无明显下降，可以去掉 Reranker

**适用条件**：知识库文档结构清晰、语义区分度高时，去掉 Reranker 影响不大。

---

## B. Prompt 调优（P0）

**当前问题**：565 条 tool_execution 被误判为 general_chat。根因是 System Prompt 对短文本/模糊表达没有明确引导，qwen3 遇到不确定时默认保守回 general_chat。

**文件**：`prompts/` 目录下的意图分类相关 yaml 文件（需先确认文件名）。

### B-1. 在 System Prompt 中加入意图偏向规则

在现有 System Prompt 的意图分类说明里，加入以下规则（放在意图定义之后）：

```
【意图判断优先级规则】
1. 当用户输入包含任何以下信号时，优先识别为 tool_execution，不要回复 general_chat：
   - 人名（如"张三"、"小王"、"何工"等称谓）
   - 时间词（"今天"、"本周"、"上月"、"昨天"等）
   - 动词（"查"、"看"、"统计"、"填"、"报"、"录"等）
   - 项目名或部门名

2. 即使输入极短（如"查工时"、"看一下"），只要能推断出业务意图，优先选择工具调用。

3. 仅当用户明确表达问候、闲聊或与工时系统完全无关时，才识别为 general_chat。
```

---

### B-2. 针对 qobp（查他人项目工时）补 few-shot 示例

**问题**：qobp 类别失败率 100%（30/30），模型完全不能识别。

在 Prompt 中补充以下 few-shot 示例对（放在现有示例之后）：

```yaml
- input: "张三在A项目填了多少工时"
  intent: tool_execution
  tool: query_timesheet
  params: {memberName: "张三", projectName: "A项目"}

- input: "查一下李四参与智慧城市项目的工时记录"
  intent: tool_execution
  tool: query_timesheet
  params: {memberName: "李四", projectName: "智慧城市"}

- input: "王五最近在哪个项目填报了工时"
  intent: tool_execution
  tool: query_timesheet
  params: {memberName: "王五"}
```

---

### B-3. 针对 swhm（多天填报）补 few-shot 示例

**问题**：swhm 类别失败率 100%（60/60）。

```yaml
- input: "帮我把周一到周五每天都填8小时工时，项目是系统维护"
  intent: tool_execution
  tool: save_workhour
  params: {dateRange: "周一-周五", hours: 8, projectName: "系统维护"}

- input: "这周三天都在做需求调研，帮我填一下工时"
  intent: tool_execution
  tool: save_workhour
  params: {days: 3, projectName: "需求调研"}
```

---

## C. 工具描述区分（P1）

**当前问题**：75 条 query_timesheet 被误判为 compute_statistics，两者在"统计"这个词上语义重叠。

**文件**：`app/services/tool_registry.py` 或各工具文件中的工具描述字段（`description`）。

### C-1. 修改 query_timesheet 的工具描述

```python
# 修改前（大约）
description="查询工时记录"

# 修改后
description="查询工时填报明细记录，返回具体的每条工时条目（日期、项目、小时数）。适用于：查某人/自己的工时记录、查某个时间段的填报情况。"
```

### C-2. 修改 compute_statistics 的工具描述

```python
# 修改前（大约）
description="统计工时数据"

# 修改后
description="对工时数据进行汇总统计计算，返回合计、均值、排名等聚合数据。适用于：统计总工时、部门工时排名、项目工时占比分析。不返回明细条目。"
```

**关键区分点**：query_timesheet 返回"明细条目"，compute_statistics 返回"汇总聚合数据"。在描述中体现这个差异，模型选工具时会更准确。

---

## D. num_ctx 自适应（P2）

**当前问题**：num_ctx=4096 在中等长度输入（500-1000 chars）表现不稳定，全局固定值不合理。

**文件**：`fastapi-service/app/core/config.py` 和调用 Ollama 的地方（langraph_agent.py 中的 LLM 初始化）。

### D-1. 在 config.py 中加配置项

```python
# 短对话（历史轮数 ≤5）用 4096，长对话用 8192
LLM_NUM_CTX_SHORT: int = 4096
LLM_NUM_CTX_LONG: int = 8192
```

### D-2. 在 agent 中根据历史长度动态选择

```python
# 估算历史 token 数（粗略：字符数 / 2）
history_chars = sum(len(m.content) for m in state["messages"])
num_ctx = settings.LLM_NUM_CTX_LONG if history_chars > 2000 else settings.LLM_NUM_CTX_SHORT
```

---

## 验证计划

每项改动完成后，按以下方式验证效果：

| 任务 | 验证方式 | 通过标准 |
|------|---------|---------|
| A-1 RAG top_k | 手动问 3 条知识问答，看 TTFT | TTFT <3s |
| A-2 去掉 Reranker | 手动问 10 条知识问答，评估答案质量 | 主观判断无明显退化 |
| B Prompt 调优 | 重跑精度测试（全量或抽样 500 条） | 失败率 ≤30% |
| C 工具描述 | 重跑精度测试中 tool_name 部分 | query_timesheet 混淆降低 50% |
| D num_ctx | Layer 1 benchmark 抽测 | 中等长度 TTFT 稳定性提升 |

---

## 执行顺序建议

```
第1步：A-1（RAG top_k，5分钟改动）→ 手动验证TTFT
第2步：B-1 + B-2 + B-3（Prompt 调优，并行做）→ 重跑精度测试
第3步：C（工具描述，10分钟）→ 抽样验证
第4步：A-2（是否去掉 Reranker，评估后决定）
第5步：D（num_ctx，按需）
```

---

## 后续评估节点

- **Prompt 调优后失败率若仍 >35%**：考虑申请 qwen3:14b 模型，在 GPU 服务器上加载
- **RAG 优化后若仍 >5s**：考虑切换 embedding 到 qwen3-embedding:8b（需重建 Milvus 索引，改动较大）
- **并发超过 10 人**：申请管理员开启 `OLLAMA_NUM_PARALLEL=4`

---

*计划制定时间: 2026-04-10*
*基于测试数据: 2026-04-09*
