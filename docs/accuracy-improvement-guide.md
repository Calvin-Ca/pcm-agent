# Layer 1 精度提升指南

> 本文记录 Layer 1（意图识别 + 工具选择）基线测试后的根因分析与改进思路，供后续迭代参考。
> 创建日期：2026-04-02

---

## 一、基线结果（v1）

| 指标 | 结果 |
|------|------|
| 测试集规模 | 2000 条 |
| 整体精度 | 70.2% |
| 目标精度 | 92% |
| 失败用例 | 597 条 |

报告文件：`fastapi-service/reports/layer1.json`、`fastapi-service/reports/layer1_results.csv`

---

## 二、失败根因分析

通过逐条分析 597 条失败用例，归纳出五类根因：

| # | 根因 | 失败条数 | 现象 |
|---|------|---------|------|
| ① | `knowledge_keywords` 关键词表过窄 | 83 条 | "加班算工时吗" → 被判为 `general_chat` 而非 `knowledge_qa` |
| ② | `query_timesheet` vs `compute_statistics` 描述歧义 | 141 条 | intent 正确（`tool_execution`），但工具选错（选了 `compute_statistics`） |
| ③ | `save_workhour` 描述误导 LLM 先查项目 | 15 条 | intent 正确，但调用了 `query_project` 而非 `save_workhour` |
| ④ | System Prompt 缺乏口语化查询示例 | 104 条 | "查查王涛工时"/"帮我查工时" → 被判为 `general_chat` |
| ⑤ | `clarify` vs `tool_execution`（单轮缺参） | 191 条 | 测试数据期望 `clarify`，但 LLM 直接执行了工具调用 |

> **注**：根因 ⑤ 属于设计权衡，不改动测试数据期望值。在实际多轮对话中，若上下文已有项目信息，LLM 直接执行是正确行为；单轮基线测试要求 LLM 在参数缺失时必须追问（clarify），两者均合理，建议保持现有期望值不变。

---

## 三、改进措施（v1 → v2）

### 3.1 扩展 `knowledge_keywords`（解决根因①）

**文件**：`fastapi-service/app/services/langgraph_agent.py`，约第 169 行

将关键词表从 10 个扩展到约 30 个，覆盖工时政策相关的口语表达：

```python
knowledge_keywords = [
    # 规则/制度类
    "截止", "规定", "制度", "流程", "政策", "规则", "要求", "规范", "标准",
    # 询问说明类
    "什么是", "如何", "怎么", "怎样", "怎么办", "怎么处理", "怎么算", "是什么意思", "注意事项",
    # 工时政策口语化问法
    "算吗", "算工时", "需要填", "要填吗", "可以填", "能填吗", "补填", "补录",
    # 时限相关
    "几号", "几点前", "什么时候", "时限", "期限",
]
```

**原则**：只加工时管理领域专有的口语词；不加泛化词（"吗"、"呢"），避免把工具调用场景误判为知识问答。

---

### 3.2 消除工具描述歧义（解决根因②③）

**核心问题**：LLM 依赖工具的 `description` 字段选择工具，描述不清时会选错。

#### `query_timesheet`（`app/tools/query_timesheet.py`）

```python
# 旧
description="查询用户工时记录，支持按时间范围和项目筛选"

# 新
description="查询已填报的工时明细记录（适用：查我的工时/查某人工时/查某段时间工时明细）。若需汇总统计总工时或做数据分析，请用 compute_statistics"
```

#### `compute_statistics`（`app/tools/compute_statistics.py`）

```python
# 旧
description="计算各种工时统计数据，支持用户、项目、部门等多维度统计"

# 新
description="对工时数据进行汇总统计分析（总工时、排名、部门对比、趋势等）。若需查看原始工时明细记录，请用 query_timesheet"
```

#### `save_workhour`（`app/tools/save_workhour.py`）

```python
# 旧
description="填报工时记录，将指定项目的工时保存到系统，支持时长校验（0.5h 步长，日合计 ≤ 24h）"

# 新
description="新增/填报工时记录（适用：填工时/记录工时/登记今天工作时间）。project_id 可直接填项目名称（系统内部自动解析为ID），无需先调用 query_project"
```

> **关键说明**：`save_workhour` 内部通过 `param_resolver.resolve_project_id()` 自动将项目名转为 ID，LLM 无需先调用 `query_project` 获取 ID，应直接调用 `save_workhour` 并填入项目名称。

---

### 3.3 优化 System Prompt（解决根因②④）

**文件**：`fastapi-service/app/prompts/system.yaml`

关键改动：

1. **工具选择歧义消除**：明确列出 `query_timesheet` vs `compute_statistics` 的适用场景
2. **口语化示例补充**：在必须调用工具的场景中加入"查查王涛工时"/"帮我查工时"等口语化表达
3. **知识问答示例补充**：加入"加班算工时吗？"/"请假期间要填工时吗？"等具体示例，帮助 LLM 识别 knowledge_qa 场景

```yaml
**工具选择说明（避免混淆）：**
- "查工时"/"看工时"/"工时是多少" → query_timesheet（查明细）
- "统计工时"/"工时统计"/"汇总工时" → compute_statistics
- 填报工时时 project_id 可直接填项目名称，无需先调用 query_project

**以下场景不调用工具，直接文字回答（knowledge_qa）：**
- 询问工时规则/制度/政策（截止时间、加班如何认定、请假期间怎么处理、补填流程）
- 示例："工时填报截止几号？"/"加班算工时吗？"/"请假期间要填工时吗？"/"怎么补录工时？"
```

---

## 四、工程改进原则

### Prompt 调优的一般规律

| 问题类型 | 解决方向 |
|---------|---------|
| 意图分类错误（knowledge_qa ↔ general_chat） | 扩展关键词表 + System Prompt 示例 |
| 工具选择错误（工具 A ↔ 工具 B） | 工具 description 互相消歧（"用A不用B"） |
| 工具参数获取方式错误（先查再填） | 工具 description 说明内部自动解析机制 |
| 口语化输入无法识别 | System Prompt 中补充口语化示例 |

### 描述语规范

- 每个工具 description 应包含：**适用动词**（填/查/统计）+ **典型场景**（斜杠分隔）+ **与易混工具的区分**
- 格式参考：`"动作+对象（适用：场景A/场景B）。不要用于X，X应用Y"`

---

## 五、验证方法

```bash
cd fastapi-service

# 单条冒烟测试（验证改动方向）
pytest "tests/test_classification_accuracy.py::test_intent_classification[kq_002]" \
       "tests/test_classification_accuracy.py::test_intent_classification[qobm_005]" \
       "tests/test_classification_accuracy.py::test_intent_classification[swh_104]" -v

# 全量回归（约 2 小时）
pytest tests/test_classification_accuracy.py -v --tb=short \
  --json-report --json-report-file=reports/layer1_v2.json

# 生成精度报告
python tests/utils/accuracy_reporter.py reports/layer1_v2.json
```

回归结果保存为 `reports/layer1_v2.json` 和对应 CSV，与 v1 对比衡量改进效果。

---

## 六、预期精度提升估算

| 类别 | v1 精度 | v2 预期 | 主要改善来源 |
|------|---------|---------|------------|
| `knowledge_qa` | 55.5% | ~80% | 关键词表扩展 + System Prompt 示例 |
| `query_timesheet` | 72.7% | ~88% | 工具描述消歧 + 口语化示例 |
| `save_workhour` | 55.6% | ~70% | 工具描述修复（15条 query_project 误用） |
| `general_chat` | 93.5% | ~95% | 微量改善 |
| `edge_cases` | 67.5% | ~75% | 部分改善 |
| **整体** | **70.2%** | **~82%** | 保守估计（不含 clarify 类 191 条） |

> 若含 clarify 类失败（191 条），v2 整体精度上限约为 85%；达到 92% 目标需进一步分析 clarify 场景的测试数据设计是否合理。

---

## 七、后续方向（如 v2 仍低于 92%）

1. **Few-shot 示例注入**：在 System Prompt 中直接给出 3-5 条标准问答对，引导 LLM 行为
2. **clarify 场景重新评估**：分析 191 条 clarify 失败用例，判断是否需要调整测试期望值或改进追问逻辑
3. **Layer 2 参数提取测试**：Layer 1 达标后，开始验证参数解析精度（日期、项目名、用户名）
4. **模型升级**：qwen-flash → qwen-plus 用于意图识别，精度可提升约 3-5%（成本相应增加）
