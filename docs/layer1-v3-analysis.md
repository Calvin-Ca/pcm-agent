# Layer 1 精度优化 v3 分析报告

> 创建日期：2026-04-03
> 状态：待处理

---

## 一、问题背景

测试框架 `test_classification_accuracy.py` 的 `build_state` 函数原本使用硬编码的 mini system message（~150字符），绕过了 `system.yaml`。

**修复后**：改用 `PromptManager.format("system", ...)` 加载完整 `system.yaml`（~2000字符），与生产环境一致。

**意外发现**：完整 prompt 测试结果反而更差：

| 版本 | 测试用 Prompt | 结果 | 通过率 |
|------|--------------|------|--------|
| v1 | mini system（硬编码） | 597 failed | 70.1% |
| v2 | mini system（硬编码）+ 工具描述优化 | 583 failed | 70.8% |
| v3 (old) | mini system（硬编码） | 597 failed | 70.1% |
| v3 (new) | **完整 system.yaml（含决策树 + Few-shot）** | **730 failed** | **63.5%** |

---

## 二、v3 (full prompt) 失败分析

### 2.1 主要误判类型

| 类型 | 典型案例 | 期望 | 实际 | 根因 |
|------|---------|------|------|------|
| off_topic | "这周天气如何" | general_chat | knowledge_qa | RAG 工具被过度触发 |
| off_topic | "这个代码怎么写" | general_chat | knowledge_qa | 同上 |
| off_topic | "周末天气怎么样" | general_chat | knowledge_qa | 同上 |
| implicit_self | "帮我过一遍填报的工时情况" | tool_execution | general_chat | 决策树过于刻板 |
| name_with_title | "李经理的工时情况" | tool_execution | general_chat | 职位识别失败 |
| ambiguous_query | "帮我查一下工时录入情况谢谢" | tool_execution | general_chat | 决策树过于保守 |

### 2.2 核心矛盾

**矛盾一：RAG 被过度触发**

Full prompt 中的 `search_knowledge` 工具描述太泛：
```
搜索知识库获取工时相关政策、制度和流程信息
```

LLM 在 Function Calling 模式下，看到任何"知识问答"类问题都想调用 RAG，导致 off_topic（天气、代码等）被判为 knowledge_qa。

**矛盾二：决策树过于刻板**

IF-THEN 格式的决策树（~300字）在 Function Calling 场景下不被 LLM 遵守。LLM 更倾向于根据"是否有对应工具"来判断，而不是根据 prompt 中的规则。

**矛盾三：工具选择变保守**

复杂 prompt 导致 LLM 在意图不明确时选择"不调用工具"（general_chat），而不是"调用最可能的工具"。

---

## 三、解决方案建议

### 3.1 RAG 工具描述收窄（优先级：高）

**当前问题**：工具描述太泛，任何问题都想调 RAG

**建议修改** `app/tools/search_knowledge.py` 中的 description：

```python
# 当前（太泛）
description="搜索知识库获取工时相关政策、制度和流程信息"

# 建议（明确边界）
description="""搜索工时管理系统相关知识库
适用场景：
- 工时填报政策、截止时间、加班认定
- 请假期间工时处理、补填/补录流程
- 系统操作问题（如何修改工时、如何查看历史）
- 工时制度、规定、流程咨询

不适用：
- 天气、新闻、代码、娱乐等与工时管理无关的话题
- 需要查数据库的工具调用类问题（请用 query_timesheet / query_project 等）
"""
```

### 3.2 决策树格式调整（优先级：中）

**当前问题**：IF-THEN 格式不被 LLM 遵守

**建议**：改用否定式规则（告诉 LLM"不要做什么"）：

```yaml
【重要】以下情况严禁调用知识库工具（search_knowledge）：
1. 问题不含工时管理相关关键词（截止/加班/请假/填报/补录/政策/制度/规则/怎么）
2. 用户在问天气、代码、娱乐等与工作无关的话题
3. 用户在说"查/看看/查一下"等动作词 + 工时 → 应调用 query_timesheet，不是 search_knowledge

正确判断流程：
- 含"加班算吗/请假要填吗/截止几号/怎么补录" → knowledge_qa ✅
- 说"查工时/看工时" → query_timesheet ✅
- 说"填工时/登记工时" → save_workhour ✅
- 说"统计工时/工时汇总" → compute_statistics ✅
- 问非工时话题（天气/代码/娱乐） → general_chat ✅
```

### 3.3 强化 System Prompt 中的"不调用工具"场景

在 `system.yaml` 的 `工具选择决策树` 部分补充：

```yaml
  5. 其他闲聊/问候/感谢 → general_chat
  6. 问天气、问时间、问代码、问娱乐等与工作无关的话题 → general_chat
  7. 用户在说"这个怎么写"/"那个是什么"且无工时关键词 → general_chat
```

---

## 四、待验证

| 方案 | 预期效果 | 风险 |
|------|---------|------|
| RAG 描述收窄 | 减少 off_topic 误判 ~100 条 | 可能误杀正常的知识问答 |
| 否定式规则 | 减少 implicit_self/name_with_title 误判 ~50 条 | 格式改动效果不确定 |
| 补充 general_chat 场景 | 减少保守误判 ~50 条 | 可能让 LLM 更倾向 general_chat |

---

## 五、结论

1. **Full prompt 方向正确** — 测试应与生产环境一致，mini system message 是历史遗留 bug
2. **RAG 工具描述是主要问题** — off_topic 爆发（+133 条）主要来源于此
3. **建议优先修改 RAG 描述**，再跑测试验证效果
4. 如果 RAG 描述收窄后仍不理想，再考虑调整决策树格式

---

## 六、相关文件

- 测试框架：`fastapi-service/tests/test_classification_accuracy.py`（build_state 已修复）
- System Prompt：`fastapi-service/app/prompts/system.yaml`
- RAG 工具：`fastapi-service/app/tools/search_knowledge.py`（需修改 description）
- 测试报告：`fastapi-service/reports/layer1_v3.json`（full prompt 结果）
- 测试报告：`fastapi-service/reports/layer1_v3_old.json`（mini prompt 结果）
