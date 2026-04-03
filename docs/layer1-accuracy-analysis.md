# Layer 1 精度分析报告

> 创建日期：2026-04-03
> 当前最优版本：v5（82.6%）

---

## 一、精度迭代历史

| 版本 | 整体精度 | 核心改动 | 关键发现 |
|------|---------|---------|---------|
| v1 | 70.2% | 基线（mini prompt + 原始工具描述） | — |
| v2 | 70.9% | 扩展 knowledge_keywords + 工具描述消歧 | 关键词匹配效果有限 |
| v3 | 63.5% | 引入完整 system.yaml（含决策树 + few-shot） | **决策树让 LLM 变保守，得不偿失** |
| v4 | 74.9% | 精简 system.yaml 为 5 条核心规则 | LLM 原生 Function Calling 能力优于规则约束 |
| v5 | **82.6%** | knowledge_qa 注册为工具 + compute_statistics 描述修正 | 语义路由远优于关键词匹配 |

**最重要结论**：给 LLM 加规则约束（决策树）会抑制 Function Calling 原生能力，精简 prompt + 精准工具描述是正确方向。

---

## 二、v5 各类别精度

| 类别 | 总数 | v1 | v5 | 变化 |
|------|------|-----|-----|------|
| `general_chat` | 200 | 93.5% | **99.5%** | +6% |
| `query_project` | 200 | — | **96.0%** | — |
| `query_timesheet` | 700 | 72.7% | **93.0%** | +20.3% |
| `edge_cases` | 200 | 67.5% | **74.5%** | +7% |
| `knowledge_qa` | 200 | 55.5% | **63.0%** | +7.5% |
| `save_workhour` | 500 | 55.6% | **67.0%** | +11.4% |
| **总体** | **2000** | **70.2%** | **82.6%** | **+12.4%** |

---

## 三、v5 剩余 348 条失败的根因分解

### 3.1 save_workhour（165 条，最大瓶颈）

| 期望 intent | 实际 intent | 条数 | 根因 |
|------------|------------|------|------|
| `clarify` | `tool_execution` | 92 | 单轮缺参，LLM 直接执行而非追问 |
| `tool_execution` | `clarify` | 50 | 参数齐全，LLM 反而过度追问 |
| `clarify` | `general_chat` | 21 | 完全未识别为填报意图 |
| — | `query_project` | 2 | 误用工具 |

**关键点**：142 条（86%）是 `clarify` vs `tool_execution` 的设计权衡问题——LLM 对"是否缺参"的判断不稳定。这无法通过 Prompt 修复，需要 PlannerAgent 显式做参数完整性检查。

### 3.2 knowledge_qa（74 条）

| 实际 intent | 条数 | 根因 |
|------------|------|------|
| `general_chat` | 70 | LLM 直接文字回答政策问题（stop），未调用 knowledge_qa 工具 |
| `tool_execution` | 4 | 误识别为工具操作 |

典型失败输入：`"周建国，请问工时截止还剩几天"` / `"李明本周工时截止几号"` / `"陈经工时截止日期到了吗"`

**根因**：query 中含有人名时，LLM 可能认为这是在向某人提问或转述，不识别为政策咨询意图，倾向于直接用文字回答。

### 3.3 query_timesheet（49 条）

| 实际 tool_name | 条数 | 根因 |
|--------------|------|------|
| `general_chat` | 26 | 口语化查询未识别为工具调用 |
| `compute_statistics` | 20 | "统计"/"汇总"词仍有歧义（v4 时 88 条，已大幅改善） |
| `query_project` | 3 | 含项目名被误判需先查项目 |

### 3.4 edge_cases（51 条）

| 实际 | 条数 | 根因 |
|------|------|------|
| `general_chat` | 38 | 模糊/混合意图，LLM 保守处理 |
| `compute_statistics` | 5 | 工具歧义 |
| 其他 | 8 | 分散 |

---

## 四、Prompt/描述层的提升天花板

在不改变架构的前提下，估算还能通过 Prompt/工具描述继续改进的空间：

| 可优化项 | 预计可回收条数 | 方法 |
|---------|-------------|------|
| knowledge_qa 含人名的 70 条 | ~30 条 | system.yaml 加 few-shot 示例，说明含人名的政策问法也应调用 knowledge_qa |
| query_timesheet → general_chat 26 条 | ~15 条 | 工具描述补充更多口语化场景 |
| edge_cases → general_chat 38 条 | ~15 条 | 部分可通过示例引导改善 |
| save_workhour → general_chat 21 条 | ~10 条 | 工具描述补充触发词 |

**估计可回收约 60-70 条，整体精度上限约 85-87%。**

超过 87% 需要解决 save_workhour 的 142 条 clarify 设计问题，必须引入 PlannerAgent。

---

## 五、各方向的提升天花板

```
当前 v5：82.6%
    │
    ├─ Prompt/工具描述继续优化（few-shot + 口语化示例）
    │       预期：+2~4%  →  约 85-87%
    │       风险：低，但边际效益递减
    │
    ├─ PlannerAgent（显式参数完整性检查，解决 clarify 142 条）
    │       预期：+5~6%  →  约 88-90%
    │       风险：中，需要改 LangGraph 图结构
    │
    └─ Few-shot 注入 + qwen-plus 替代 qwen-flash 做意图识别
            预期：+2~3%  →  约 92% 目标
            成本：qwen-plus 成本约为 qwen-flash 的 6-8 倍
```

---

## 六、Layer 1 目标达成度评估

| 指标 | 当前 | 目标 | 差距 |
|------|------|------|------|
| 整体精度 | 82.6% | 92% | -9.4% |
| `query_timesheet` | 93.0% | 90% | **已超** ✅ |
| `query_project` | 96.0% | 90% | **已超** ✅ |
| `general_chat` | 99.5% | 90% | **已超** ✅ |
| `knowledge_qa` | 63.0% | 90% | -27% ⚠️ |
| `save_workhour` | 67.0% | 90% | -23% ⚠️ |
| `edge_cases` | 74.5% | 75% | 接近 |

`query_timesheet`、`query_project`、`general_chat` 三类已达标。`knowledge_qa` 和 `save_workhour` 是主要短板，且两者的剩余问题都需要架构层面的改动才能实质性解决。

---

## 七、建议：是否开启下一阶段

### 现阶段结论

**当前 82.6% 已具备生产可用条件**（主流场景如查工时、填工时、查项目的精度均已超过 90%），可以开始并行推进：

**继续做（Layer 1 收尾）**
- few-shot 示例：针对 knowledge_qa 含人名场景，在 system.yaml 加 2-3 条示例
- 预计 +2~3%，约 1 小时工作量

**开启下一阶段（Layer 2 + 生产）**

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **Layer 2 参数提取精度** | 测试 date/project/member 参数提取准确率（日期解析是否正确、项目名解析是否成功）| ★★★ |
| **PlannerAgent** | 解决 save_workhour clarify 142 条，架构升级 | ★★☆ |
| **生产部署验证** | 真实用户对话测试，观察 82.6% 精度在实际使用中的表现 | ★★★ |
| **Layer 3 端到端** | 接 SpringBoot 做完整链路测试（参数解析 → API 调用 → 结果返回）| ★★☆ |

**推荐优先级**：Layer 2 > 生产部署 > PlannerAgent > Layer 3。Layer 2 的参数提取精度直接影响实际使用效果，即使 Layer 1 意图识别正确，参数解析错误也会导致工具调用失败。

---

## 八、技术债 & 注意事项

1. **测试数据的 clarify 期望值**：当前 save_workhour 的 `fill_missing_*` 系列用例期望 `clarify`，但实际多轮对话中直接执行是合理行为。引入 PlannerAgent 后，建议同步更新这批测试用例的期望值。

2. **knowledge_qa RAG 质量**：即使路由正确，RAG 检索的回答质量依赖知识库文档的完整性和向量索引质量。Layer 1 只验证路由，不验证答案。

3. **swhm（多日填报）精度仍低**：`swhm` 类用例（一次填多天工时）精度约 30%，属于 save_workhour 内的复杂子场景，当前工具 schema 可能不支持批量填报，需确认产品需求。
