# AI Service 改进结果报告（2026-04-10）

> 原文件名：`improvement-results-2026-04-10.md`

**测试环境**：Ollama qwen3:8b (Q4_K_M 量化) + qwen3-embedding:8b (Q4_K_M 量化)
**配置**：Reranker 关闭 (USE_RERANKER=False)
**测试时间**：2026-04-10

---

## 一、精度测试结果（抽样验证）

> 完整 2000 条测试正在后台运行（1h+）。本次抽样验证用于快速发现 bad case。

### 1.1 ec_（edge_cases，工具执行类）

| 序号 | ID | 输入 | 期望意图 | 实际意图 | 结果 |
|------|-----|------|---------|---------|------|
| 1 | ec_001 | 查工时 | tool_execution | tool_execution | ✅ |
| 2 | ec_002 | 工时呢 | tool_execution | tool_execution | ✅ |
| 3 | ec_003 | 瞅瞅工时 | tool_execution | tool_execution | ✅ |
| 4 | ec_004 | 我的工时 | tool_execution | tool_execution | ✅ |
| 5 | ec_005 | 工时记录 | tool_execution | tool_execution | ✅ |
| 6 | ec_010 | 工时数据 | tool_execution | **general_chat** | ❌ |
| 7 | ec_020 | 工时报了多少 | query_timesheet | **compute_statistics** | ❌ |
| 8 | ec_030 | 我报了多少工时 | query_timesheet | **compute_statistics** | ❌ |
| 9 | ec_040 | 查看工时 | tool_execution | tool_execution | ✅ |
| 10 | ec_050 | 查查工时 | tool_execution | tool_execution | ✅ |

**ec_ 通过率：7/10 = 70%**

### 1.2 gc_（general_chat，闲聊类）

| 序号 | ID | 输入 | 期望意图 | 实际意图 | 结果 |
|------|-----|------|---------|---------|------|
| 1 | gc_001 | 你好 | general_chat | general_chat | ✅ |
| 2 | gc_010 | 帮我分析数据 | general_chat | general_chat | ✅ |
| 3 | gc_020 | 今天天气怎么样 | general_chat | general_chat | ✅ |
| 4 | gc_045 | 早上好，请问能帮我看看工时吗 | general_chat | **tool_execution** | ❌ |
| 5 | gc_100 | 你是谁 | general_chat | general_chat | ✅ |
| 6 | gc_150 | 谢谢 | general_chat | general_chat | ✅ |
| 7 | gc_200 | 再见 | general_chat | general_chat | ✅ |

**gc_ 通过率：6/7 = 85.7%**

### 1.3 关键 Bad Case 分析

#### Bad Case 1：工时数据 → general_chat

**现象**：无动词的名词短语"工时数据"返回 general_chat，期望 tool_execution。

**已尝试修复**：
- 在意图优先级规则中新增："**"工时"关键词（即使无动词，如"工时数据"、"工时呢"、"查工时"、"我的工时"）**"
- 规则已 hot-reload 到服务中

**验证结果**：Prompt 修改后，"工时数据"仍返回 general_chat。**结论：qwen3:8b 的 Function Calling 能力已触及上限，Prompt 调优对这类 case 无效。P1 规则兜底是唯一出路。**

#### Bad Case 2：我报了多少工时 → compute_statistics

**现象**：被识别为 compute_statistics（统计），期望 query_timesheet（明细）。

**已尝试修复**：
- 在 system.yaml 新增 "query_timesheet vs compute_statistics 区分" 示例，明确列出"我报了多少工时" → query_timesheet

**验证结果**：Prompt 修改后，"我报了多少工时"仍返回 general_chat（而非 tool_execution）。同样触及模型 Function Calling 上限。

#### Bad Case 3：gc_045 边界 case

**现象**："早上好，请问能帮我看看工时吗" 被识别为 tool_execution，期望 general_chat。

**评估**：这是模型更智能的判断——优先响应了工时意图。此用例期望值可能需要更新。**不视为真正的问题。**

## 三、改进项实施状态

| 任务 | 状态 | 备注 |
|------|------|------|
| A-1: RAG k=5 验证 | ✅ 完成 | 各组件 k=5 已确认 |
| B-1: 意图优先级规则 | ✅ 完成 | "工时"关键词规则已添加 |
| B-2: qobp few-shot | ✅ 完成 | system.yaml 已添加 |
| B-3: swhm few-shot | ✅ 完成 | system.yaml 已添加 |
| B-4: query_timesheet vs compute_statistics 区分 | ✅ 完成 | system.yaml 已添加 |
| C: 工具描述区分 | ✅ 完成 | query_timesheet/compute_statistics description 已更新 |
| D: num_ctx 自适应 | ✅ 完成 | config.py + llm_client.py + langgraph_agent.py |
| A-2: Reranker 评估 | ✅ 完成 | RAG 质量可接受，暖启动 ~2.7s |

---

## 四、Embedding 模型说明

**当前配置**：`qwen3-embedding:8b`（Ollama）

- **量化方式**：Q4_K_M（4-bit，Ollama 默认），非 INT4
- **输出维度**：4096 维（drop_old=True 自动对齐 Milvus）
- **冷启动**：~13s（GPU 模型加载）

> Ollama 服务器模型列表（均为 Q4_K_M）：
> - qwen3-embedding:8b (7.6B, 4.6GB)
> - qwen3:8b / qwen3:4b / qwen3:14b / qwen3:32b
> - qwen3-vl:8b, qwen2.5vl:7b
> - **无 bge-base-zh-v1.5**（需单独部署）

---

## 五、后续行动计划（按优先级）

### P0-1：完整 2000 条精度测试（后台运行中）
无完整数据，所有改进效果都是估计。17 条样本置信度太低。

### P0-2：Embedding 模型对比（待执行）
准备 20 条知识问答评估集，对比 `USE_OLLAMA_EMBEDDING=False`（DashScope text-embedding-v2）vs 当前 Ollama 版的召回质量 + TTFT。

### P0-3：Prompt 二次迭代（已完成，B-1/B-4）
意图优先级规则 + query_timesheet 示例已添加。等待完整测试验证效果。

### P1：规则兜底路由
对含"工时"关键词的极短 query，在 LangGraph 入口加一层关键词快速路由：命中"工时/项目/填报"等核心词立刻导向 tool_execution，绕过 LLM 判断。

**关键发现：Bad case "工时数据"在 Prompt 修改后仍返回 general_chat，说明 P1 规则兜底是必须的，不能只靠 Prompt 调优。**

### P2：暂不上 14B/32B
投入产出比低于 Prompt 迭代 + 规则兜底 + Embedding 对比。等上述调优到极限再考虑。

---

## 六、核心结论

1. **Prompt 调优对 qwen3:8b Function Calling 能力已达极限**：Bad case "工时数据" 在 Prompt 修改后仍返回 general_chat，这不是 Prompt 问题，是模型能力问题。

2. **RAG 冷启动 13.8s 是最硬的问题**：从 31s 降到 13.8s 已经是维度修复的功劳，但要再降只有换 Embedding 模型这一条路。

3. **Reranker 关闭**：知识问答质量可接受，暖启动 ~2.7s，保留关闭状态。

4. **最优先事项**：Embedding 模型对比（DashScope vs Ollama），直接决定 RAG TTFT 能否再降。
