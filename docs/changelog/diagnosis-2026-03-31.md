---
name: 助手"笨"的根因诊断与改造方案
description: 2026-03-31 深度诊断：为什么助手查工时不知道查谁、填工时提取不出要点，以及 Function Calling 改造方案
type: project
---

# 助手"笨"的根因诊断与改造方案

> 写于 2026-03-31，基于对 intent_router.py / langgraph_agent.py / prompts/*.yaml / query_timesheet.py 的完整阅读

---

## 一、现象

用户反馈两个典型问题：

1. **查工时**：说"查一下我的工时"，助手不知道查谁，有时返回全员数据
2. **填工时**：多轮对话提供了项目名、日期、时长，助手却没法把这些信息整合起来，参数提取失败

---

## 二、根因分析

问题不是某个工具的 Bug，而是 **意图识别的架构设计** 从一开始就有三个结构性缺陷。

---

### 缺陷 1：意图识别和参数提取是"两步分离"的

**现状**（`intent_router.py` 的流程）：

```
用户说 "查一下张三本周的工时"
  ↓
【第一次 LLM 调用】qwen-flash（轻量模型）
  只做意图分类：这是 tool_execution，工具是 query_timesheet
  ↓
【第二次 LLM 调用】qwen-plus
  只做参数提取：从消息中提取 {start_date, end_date, member_name}
  ↓
【代码层】
  手动注入 user_id（三处逻辑各自为政）
  ↓
执行工具
```

**为什么会出错？**

- **两次调用互相不知道对方的结果**。第一次调用只输出了意图类型，第二次调用只看用户的原始消息，看不到第一次的分类上下文。
- **第一次用的是轻量模型 qwen-flash**，理解力弱，容易分错类。比如"查一下工时情况"被分成 `general_chat` 而不是 `tool_execution`，导致根本不会走参数提取那步。
- **多轮对话时尤其容易丢失上下文**。用户在第一轮说"查张三的工时"，第二轮说"上周呢"，第二次参数提取时只看第二轮消息，不一定能正确继承"张三"这个指代。

---

### 缺陷 2：System Prompt 太弱，LLM 缺乏"环境感知"

**现状**（`system.yaml` 全文）：

```
你是一个专业的企业工时管理助手。
请用简洁、友好的方式回答用户问题。
如果涉及工时管理相关功能，可以引导用户使用对应的功能。
```

**LLM 从这段 prompt 中能知道什么？**

- 知道自己是个助手
- 知道要友好

**LLM 不知道什么？**

| 缺失信息 | 影响 |
|----------|------|
| 当前用户是谁（user_id、姓名、部门） | 说"查我的工时"时，LLM 不知道"我"是谁 |
| 可用工具有哪些、每个工具的参数 schema | LLM 无法主动引导用户提供正确参数 |
| 默认行为规则 | "查工时"不指定人 → 默认查自己；LLM 不知道这个规则，随机处理 |
| 缺参时该怎么做 | 没有参数时应该追问，但 LLM 不知道哪些参数是必填的 |
| 对话历史的权重 | 不知道要从历史对话中继承上下文（如人名、时间范围） |

**直接影响**：LLM 在不知道"当前是谁、工具需要什么"的情况下做参数提取，就像让一个不知道表单字段的人去帮你填表，必然填错。

---

### 缺陷 3：800 行规则匹配是"历史包袱"，且会覆盖 LLM 的正确判断

**现状**（`intent_router.py` 第 643-1045 行）：

```python
# 关键词评分系统，每加一个工具就要在这里加关键词
ts_score = 0.0  # query_timesheet 得分
cs_score = 0.0  # compute_statistics 得分
qp_score = 0.0  # query_project 得分
# ... 大量权重计算 ...
best_tool, best_score, best_match = max(scores, key=lambda x: x[1])
```

**问题**：
- 这套规则是给"LLM 不可用时"的降级方案，但实际上在 LLM 调用之前/之后都在干预路由结果
- 规则之间会互相冲突（"工时"同时是 query_timesheet 和 compute_statistics 的关键词）
- 每新增一个工具，需要在多处同时修改：关键词列表、评分逻辑、参数提取正则
- 规则匹配的结果有时比 LLM 的判断更差，但代码逻辑导致规则结果优先

---

## 三、改造方案：切换到 Function Calling

### 核心思路

qwen-plus 支持 OpenAI 兼容的 `tools` 参数（函数调用）。这意味着可以把工具的参数 schema 直接告诉 LLM，让 LLM 在 **一次调用中同时完成**：

1. 判断用户是否需要调用工具（相当于意图分类）
2. 决定调用哪个工具（相当于工具选择）
3. 提取所有参数（相当于参数提取）
4. 发现参数缺失时，用自然语言追问（相当于 clarify_node）

### 改造后的流程

```
改造前（两步分离，4步各自为政）：
  用户消息
    → LLM1(flash): 意图分类
    → LLM2(plus): 参数提取
    → 代码: user_id 注入（3处）
    → 执行工具

改造后（一步到位）：
  用户消息 + 当前用户信息 + 工具schema
    → LLM(plus) with tools
    → 情况A：返回 tool_call → 直接执行，参数已完整
    → 情况B：返回文本 → 直接给用户（追问/闲聊/知识问答）
```

### 具体变化

**System Prompt 变成什么样**：

```yaml
你是工时管理系统的 AI 助手。

当前用户信息：
- 用户ID：{user_id}
- 姓名：{user_name}
- 部门：{department}
- 角色：{role}

行为规则：
- 用户说"查工时"但未指定人时，默认查当前用户（user_id: {user_id}）
- 填工时必须有：项目（名称或ID）、日期、时长，缺一必须追问
- 时间词解析：今天={today}，本周={week_start}至{today}，本月={month_start}至{today}

可用工具（通过 tools 参数传入，LLM 自动选择）：
- query_timesheet：查询工时记录
- save_workhour：填报工时
- query_project：查询项目信息
- compute_statistics：统计分析
- generate_weekly_report：生成周报
```

**Function Calling 调用代码结构**：

```python
# app/services/function_calling.py（新建）

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_timesheet",
            "description": "查询工时记录。用户说'查工时'、'我上周做了什么'等时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "member_name": {"type": "string", "description": "要查询的人名，不填则查当前用户"}
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    # ... 其他工具 ...
]

async def call_with_tools(messages, user_context) -> dict:
    """
    一次 LLM 调用完成意图识别 + 参数提取
    返回：{"type": "tool_call", "tool_name": "...", "params": {...}}
         或 {"type": "text", "content": "..."}
    """
    response = await llm_client.chat(
        messages=messages,
        tools=TOOL_SCHEMAS,
        tool_choice="auto"
    )
    if response.tool_calls:
        return {"type": "tool_call", ...}
    else:
        return {"type": "text", "content": response.content}
```

**LangGraph 流程简化**：

```
改造前：classify_intent → execute_tool | execute_rag | execute_llm | clarify_node
改造后：llm_with_tools → execute_tool_call（有工具调用时）
                       → return_text（无工具调用时，含追问/闲聊/知识问答）
```

### 改造工作量

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | 增强 System Prompt（`system.yaml` + 注入用户信息） | 0.5 天 |
| 2 | 新建 `function_calling.py`，定义 5 个工具的 schema | 1 天 |
| 3 | 改造 `langgraph_agent.py` 的节点编排 | 0.5 天 |
| 4 | 清理 `intent_router.py` 旧逻辑，保留为 fallback | 0.5 天 |
| **合计** | | **2-3 天** |

### 改造后的扩展方式

```python
# 改造前：新增一个工具需要改 4 个文件
# intent_router.py → 加关键词（3处）、加 tools_desc、加参数提取 prompt
# langgraph_agent.py → 可能改注入逻辑

# 改造后：只需 1 处
# app/tools/xxx.py → 写工具逻辑 + 声明 function schema
# tool_registry 自动收集 → LLM 自动发现并调用
```

---

## 四、为什么不先修 Bug，再改架构？

两个 Bug（查工时返回全员、填工时项目名当ID）的修复方案（见 roadmap.md 2.1/2.2节）都是"打补丁"：

- 查工时 Bug：在 `query_timesheet.py` 第143行加 fallback
- 填工时 Bug：在 `save_workhour.py` 加名称→ID转换

**修了之后助手还是不够聪明**，因为根因没解决：
- 下一个工具还是可能提错参数
- 多轮对话还是可能丢失上下文
- 每增加一个功能还是需要改多处

**先改架构的好处**：
- Function Calling 改造后，查工时默认查自己的问题通过 System Prompt 自然解决，不用再打补丁
- 后续每个工具只需要写 schema，工具级 Bug 的概率大幅降低

---

## 五、结论

这是一个从"能用"到"好用"的关键跃升。当前系统的架构是"规则 + LLM 辅助"，改造后变成"LLM 主导 + 规则降级保底"。这个方向改动量不大但影响深远。

**How to apply:** 每次遇到"助手理解不对"的问题，先问是不是 System Prompt 或 Function Calling 的问题，而不是直接去改工具层代码。
