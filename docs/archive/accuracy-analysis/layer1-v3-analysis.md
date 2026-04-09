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

## 二、v3 (full prompt) 数据分析

### 2.1 分类别失败统计

| 前缀 | 总数 | old 失败 | new 失败 | 变化 | old 失败率 | new 失败率 |
|------|------|---------|---------|------|-----------|-----------|
| **swhs** (简单填报) | 50 | 28 | **50** | **+22** | 56% | **100%** |
| **swhr** (带备注填报) | 60 | 0 | **19** | **+19** | 0% | **31.7%** |
| **swhp** (带项目填报) | 80 | 11 | **30** | **+19** | 13.8% | **37.5%** |
| **swh** (填报综合) | 250 | 122 | **159** | **+37** | 48.8% | **63.6%** |
| **ec** (边缘用例) | 200 | 71 | **120** | **+49** | 35.5% | **60%** |
| **qp** (项目查询) | 200 | 23 | **53** | **+30** | 11.5% | **26.5%** |
| swhm (多日填报) | 60 | 57 | 60 | +3 | 95% | 100% |
| qsbp (按项目查自己) | 50 | 47 | **21** | **-26** | 94% | **42%** ✅ |
| qsm (查自己本月) | 80 | 33 | **21** | **-12** | 41.2% | **26.2%** ✅ |
| qspm (查自己上月) | 80 | 25 | **17** | **-8** | 31.2% | **21.2%** ✅ |
| qsl | 60 | 12 | **6** | **-6** | 20% | **10%** ✅ |

**净效果**：退步 +133 条，改善 -100 条 → 净增 133 条失败。

### 2.2 退步的测试用例（passed→failed）按子类型统计

| 子类型 | 数量 | 典型输入 |
|--------|------|---------|
| save_workhour_simple | 22 | "填报今天8小时"、"帮我填一下今天8小时" |
| implicit_self | 20 | "帮我过一遍填报的工时情况"、"看看工时数据" |
| save_workhour_with_project | 19 | "移动端改版昨天3小时"、"ERP升级明天8小时" |
| save_workhour_with_remark | 19 | "项目名称需解析，描述为文档整理" |
| mixed_intent | 17 | "查下工时顺便看看项目"、"工时情况怎么样项目有哪些" |
| query_list_fillable | 17 | "查看工时可填报的项目"、"能填报的项目都有哪些" |
| ambiguous_query_timesheet | 15 | "工时情况"、"工时填报了多少"、"工时记录" |
| query_by_name | 11 | "查下移动端改版"、"查ERP升级" |
| query_project_info | 9 | "移动端改版项目什么时候开始的" |

### 2.3 退步的核心模式

**几乎所有退步都是：期望 `tool_execution`，实际返回 `general_chat`**

这说明 full prompt 让 LLM 变得**过度保守**——应该调用工具的场景，LLM 选择了不调用工具、直接用文字回复。

---

## 三、根因分析

### 根因一：`save_workhour` 缺参追问规则让 LLM 提前拦截（影响 ~60 条）

system.yaml 第56-61行的规则：

```yaml
【关键】填报工时缺参必须追问：
- 必填参数：project_id（项目）、date（日期）、duration（时长）
- 三者缺一不可。若有任一缺失，必须用一句话追问，不得猜测默认值。
```

**问题**：LLM 在 Function Calling 层面看到这条规则后，对于 `save_workhour` 类用户输入（如"填报今天8小时"，缺少 project_id），不再调用 `save_workhour` 工具，而是**直接在文字回复中追问**。

这导致 `finish_reason=stop`（而非 `tool_calls`），代码逻辑走到第166-188行的 general_chat 分支。

**实际上**，代码已有缺参追问的逻辑（第141-157行）——当 LLM 调用了 save_workhour 但参数不全时，会自动返回 `intent=clarify`。但 LLM 在 prompt 层面就把追问做了，绕过了代码的处理。

### 根因二：决策树 + Few-shot 使 LLM 过度"思考"（影响 ~50 条）

mini prompt 只说"你是工时管理智能助手"，LLM 看到工具列表就直接按工具名称匹配用户意图——简单粗暴但有效。

full prompt 加入了 ~300 字的 IF-THEN 决策树 + ~200 字的 Few-shot 示例后，LLM 开始"按规则思考"：
- "工时情况" → 决策树说需要有"查"+"工时"才走 query_timesheet → 缺少"查"字 → 不匹配 → general_chat
- "查下移动端改版" → 没有"工时"关键词 → 不匹配任何工具 → general_chat
- "帮我过一遍填报的工时情况" → 模糊表达，决策树无法精确匹配 → general_chat

**核心矛盾**：Function Calling 模式下，LLM 本身就擅长根据工具 schema 判断该调哪个工具。额外的决策树不但没帮忙，反而**限制了 LLM 的工具选择能力**。

### 根因三：项目查询类工具描述缺乏覆盖（影响 ~30 条）

"查下移动端改版"、"能填报的项目都有哪些" 这类输入，LLM 无法将其与 `query_project` 工具关联，因为 `query_project` 的工具描述可能不包含"可填报项目列表"等语义。

### 附：原文档提到的"RAG 过度触发"问题

原文档将 off_topic→knowledge_qa 列为主要问题，但实际数据显示：
- `gc`（general_chat）类别仅 +1 条退步
- **没有 `search_knowledge` 工具**——知识问答走的是关键词匹配（langgraph_agent.py 第169-179行）
- 所谓的"RAG 过度触发"在 v3 数据中不是主要矛盾

真正的主要矛盾是 **tool_execution → general_chat 的大量退步**。

---

## 四、解决方案

### 方案 A：精简 system.yaml，让 Function Calling 发挥原生能力（推荐）

**核心思路**：system prompt 只提供"语境信息"（用户信息、日期、角色），不要教 LLM 怎么选工具。工具选择交给 Function Calling 原生能力 + 工具描述。

```yaml
template: |
  你是「工时管理智能助手」，专为企业工时管理系统服务。

  ## 当前用户信息
  - 用户ID：{user_id}
  - 姓名：{user_name}
  - 角色：{entity_type}（employee=普通员工 | deptAdmin=部门管理员 | superAdmin=超级管理员）
  - 部门ID：{department_id}
  - 今天：{today}（本周 {week_start} 至 {week_end}，上周 {last_week_start} 至 {last_week_end}）

  ## 核心规则
  1. 用户请求涉及工时查询、填报、统计、项目查询时，必须调用对应工具，不要用文字回答。
  2. 如果用户查询工时但未指定对象，默认查询当前用户自己。
  3. 如果用户填报工时但缺少参数（项目/日期/时长），仍然调用 save_workhour，传入已有参数。
  4. 日期解析："上个月"=上月1日至月末；"本周"={week_start}至{week_end}；"上周"={last_week_start}至{last_week_end}；"今天"={today}。
  5. 与工时管理无关的话题（天气、代码、娱乐等），直接回复。
```

**关键改动**：
- **删除**：IF-THEN 决策树（~300字）
- **删除**：knowledge_qa few-shot 示例（~200字）
- **删除**："缺参必须追问"的 prompt 级规则（改为依赖代码层的 clarify 逻辑）
- **修改**：第3条明确告诉 LLM "缺参也要调用工具"，让代码层处理追问
- **保留**：用户信息、日期解析规则、角色说明

### 方案 B：优化工具描述，增强 LLM 的工具选择信号

在各工具的 description 中加入更多语义覆盖：

**save_workhour**：
```python
description="""填报/记录工时。
当用户说"填"、"记"、"登记"、"填报"等词搭配时间/项目/工时时调用此工具。
即使参数不完整也应调用，系统会自动追问缺失参数。"""
```

**query_project**：
```python
description="""查询项目信息，包括：
- 按项目名称查询项目详情（成员、负责人、起止时间）
- 查询用户可填报工时的项目列表
当用户说"查项目"、"哪些项目"、"可填报的项目"、"项目有哪些"时调用。"""
```

**query_timesheet**：
```python
description="""查询工时记录。
当用户说"查工时"、"看工时"、"工时情况"、"工时多少"、"填报情况"等时调用。
支持查自己或他人的工时（通过姓名指定）。"""
```

### 方案 C：knowledge_qa 关键词匹配范围收窄

当前 `langgraph_agent.py` 第169-177行的关键词列表过宽，部分关键词（如"怎么"、"什么是"）会误判：

```python
# "什么时候"匹配了"移动端改版项目什么时候开始的" → 应该走 query_project
# 建议：knowledge_qa 的关键词匹配只在 finish_reason=stop（LLM 未调用工具）时生效
# 并且排除已包含项目名/人名的查询
```

### 推荐执行顺序

1. **先做方案 A**（精简 prompt）— 预期减少 ~100 条退步，因为移除了让 LLM 变保守的规则
2. **再做方案 B**（优化工具描述）— 预期减少 ~30 条退步，增强工具匹配覆盖
3. **最后做方案 C**（收窄关键词）— 预期减少 ~10 条 knowledge_qa 误判

---

## 五、预期效果

| 方案 | 影响范围 | 预期减少失败 | 风险 |
|------|---------|-------------|------|
| A: 精简 prompt | swhs/swhr/swhp/ec 全面改善 | ~100+ 条 | 可能损失 qsbp 等改善（需验证） |
| B: 优化工具描述 | qp/ec 改善 | ~30 条 | 低风险 |
| C: 收窄关键词 | qp_info/ec 小幅改善 | ~10 条 | 可能漏掉正常知识问答 |

**目标**：A+B 组合后，full prompt 通过率应超过 mini prompt 的 70.1%，达到 ~75%+。

---

## 六、为什么不建议原文档的方案

原文档建议"收窄 RAG 工具描述"作为首要方案，但存在以下问题：

1. **没有 search_knowledge 工具**——知识问答不是通过 Function Calling 触发的，而是代码层的关键词匹配
2. **RAG 过度触发不是主要矛盾**——主要退步是 tool_execution→general_chat（占 ~90%），而非 general_chat→knowledge_qa
3. **否定式规则（"严禁调用"）会进一步加剧保守倾向**——LLM 已经太保守了，再加禁止规则会更糟

---

## 七、相关文件

- 测试框架：`fastapi-service/tests/test_classification_accuracy.py`（build_state 已修复）
- System Prompt：`fastapi-service/app/prompts/system.yaml`（需精简）
- LangGraph 主节点：`fastapi-service/app/services/langgraph_agent.py`（第97-188行，含缺参追问逻辑）
- 工具定义：`fastapi-service/app/tools/*.py`（需优化 description）
- 测试报告：`fastapi-service/reports/layer1_v3.json`（full prompt 结果）
- 测试报告：`fastapi-service/reports/layer1_v3_old.json`（mini prompt 结果）
