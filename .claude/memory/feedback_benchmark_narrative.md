---
name: 基准测试简历叙事约束
 description: 基于 2026-04-24/25 三轮基准测试的真实数据，约束简历中 FC/SQL Agent/RAG 指标的写法，避免注水数字
type: feedback
---

## 规则

### Function Calling 延迟
- **绝对不要写** "FC 延迟下降 X%" 或 "降幅 XX%"
- 本地 vLLM + qwen3-8b 环境下，FC（B 模式）整体**不慢于**两次 LLM 调用（A 模式）
- 真实数字：save 类持平（P50 +0.7%），query 类慢 37~44%（受 prefill 主导），kb 类慢 21%
- id=5/8 的 query 被误路由到 sql_query（生产 bug），剔除后 query 仍慢 37.5%
- **简历正确写法**：强调架构价值（单次调用消除误差传播）+ save 持平作为正面点 + 托管 API 场景下预期缩短 20~40%（理论值，无实测）

**Why:** 三轮测试（v1→v2→v3→v4）数据一致：B 模式在本地 vLLM 下没有延迟优势。v1 数据失真（A/B 不公平对照），v2/v3/v4 诚实记录后 B 更慢。面试官追问"降了多少"时必须有合理解释。

**How to apply:** 任何涉及"FC 性能"的简历修改、PR 描述、文档更新前，先核对本规则。

### SQL Agent 安全拦截
- **绝对不要写** "综合拦截率 100%"
- 真实数字：硬规则拦截 5/20 = 25%，LLM 语义改写 15/20 = 75%
- 硬规则 = 真安全防御（语句类型白名单 + 危险关键字黑名单 + 表白名单 + 列黑名单）
- LLM 改写 = 辅助层，非可靠防御（换 temperature/prompt injection 可能漏掉）
- **简历正确写法**：三层安全校验 + 硬规则覆盖 DDL/DML/敏感列/跨库，LLM 语义改写作为辅助层单独说明

**Why:** v1 报告把"LLM 把 DELETE 改成 SELECT"统计为"拦截成功"，这是注水。v2 修正后拆分为 hard_blocked vs rewritten。

**How to apply:** 任何涉及"SQL Agent 安全"的对外表述，必须区分硬规则和 LLM 改写。

### RAG Recall
- Hybrid 98% < Milvus 100% 是**正常结果**（4 文档小库下 BM25 噪音主导）
- 必须加解释："小规模知识库下 BM25 噪音主导，生产规模 20+ 文档后 Hybrid 预期反超"
- 测试集从文档直接构造 → Recall 虚高，面试被问时要诚实说明

**Why:** 面试官看到 98% < 100% 会质疑 Hybrid 设计，不加解释显得有 bug。

**How to apply:** 简历/文档中写 RAG 指标时，必须同时写解释句。
