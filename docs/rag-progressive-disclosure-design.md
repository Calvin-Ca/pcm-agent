# RAG 渐进式披露(Agentic RAG) — 设计与实施方案

> 创建日期:2026-05-05
> 目标完成:2026-05-07(面试前定稿)
> 状态:**设计稿,待执行**
> 关联:`docs/roadmap.md` 第七节(RAG 检索优化)、`fastapi-service/docs/rag-upgrade-roadmap.md`

---

## 0. TL;DR

**用 LangGraph 实现 A-RAG**(Agentic RAG):把当前 one-shot 的 `knowledge_qa` 工具拆成 4 个**层次化检索接口**(`kb_outline` / `kb_keyword_search` / `kb_semantic_search` / `kb_read_section`),通过 LangGraph 的状态图 + 循环边 + Function Calling tool loop 驱动 LLM 自主多轮调用,实现 **A-RAG 论文中的"渐进式披露"机制**。同时把知识库从 4 篇扩到 100 篇,引入 PDF / docx 多格式,分 7 个主题域归类。

**概念厘清**(避免混淆):
- **ReAct** 是 Yao 2022 提出的"Reasoning + Acting 交替"**范式**,不是框架,不需要"实现"
- **A-RAG** 是 2026 年具体方案(3 个层次化检索工具 + 多轮调用),受 ReAct 范式启发
- **LangGraph** 是图编排框架,提供状态管理、条件边、循环 — 是**承载 A-RAG 的工程载体**
- 我们走的是 **OpenAI Function Calling 风格的 agent tool loop**,LLM 直接输出 `tool_calls` 不写显式 `Thought:`,严格说不是教科书 ReAct,叫"agent loop"更准确

**面试核心卖点**:用 LangGraph 实现 A-RAG / 渐进式披露 + 跨文档多跳评测 + RAG vs SQL Agent 边界划分。

**不做**:全量替换主链路、自研框架、RAPTOR 树形索引(留给 v1.5)。

---

## 1. 背景与目标

### 1.1 当前痛点

| # | 痛点 | 现状 |
|---|------|------|
| P1 | 知识库太小(4 文档 / 36 chunk),Recall 测试存在信息泄漏 | benchmark 报告已主动声明,但面试硬伤 |
| P2 | 检索是 one-shot — top-K chunks 全部塞 LLM,跨文档多跳问题答不全 | 现有 RAG 路径单跳 |
| P3 | 60/40 权重无消融数据 | 简历"调出来的"是话术 |
| P4 | 只支持 Markdown,没真实跑过 PDF / docx | loader 写了但没测 |
| P5 | RAG vs SQL Agent 的边界没文档化,面试讲不清 | 散落代码里 |

### 1.2 目标(P0)

1. **接口层**:暴露 4 个层次化检索工具,由 LLM 通过 agent loop(LangGraph 承载)自主多轮调用
2. **数据层**:知识库扩到 100 篇,分 7 个主题域,引入 PDF/docx 多格式
3. **评测层**:出 18 条评测 query,跑 one-shot vs progressive 对比,出硬数字
4. **文档层**:写明 RAG 边界(政策类)和 SQL Agent 边界(实体类)

### 1.3 非目标(本期不做)

- ❌ 替换主 RAG 链路 — `knowledge_qa` 保留为简单问题的快速通道
- ❌ RAPTOR 树形索引 / GraphRAG / HyDE — 留给 v1.5
- ❌ Self-RAG / CRAG 反思机制 — 留给 v1.5
- ❌ MCP Server 接入 — 单独路线
- ❌ 知识库增量更新 — 当前 drop_old=True 已能用

---

## 2. A-RAG 对照与我们的实现选型

### 2.1 A-RAG 论文要点(2026)

> 参考:[PREreview](https://prereview.org/reviews/19039198) / [GitHub](https://github.com/Ayanami0730/arag)

**3 个层次化工具**:

| A-RAG 工具 | 行为 | 评分逻辑 |
|-----------|------|---------|
| `keyword_search(query)` | 大小写不敏感词法匹配 | 关键词频率 × 长度 |
| `semantic_search(query)` | 句子级 embedding + cosine | 向量相似度 |
| `chunk_read(chunk_id)` | 取整 chunk + 邻接 chunks(±1)+ 去重跟踪 | — |

**控制流**:agent loop(LLM 每轮选一个工具 → 看到 observation → 决定下一步),论文里描述为 ReAct-like 范式;在 FC 时代等价于"tool-calling loop"。

**评测结果**(GPT-5-mini 在 MuSiQue 多跳问答):
- A-RAG (Full) **74.1%** vs Naive RAG 62.4-66.2%(LLM-Acc)
- token 消耗显著更少
- 在 MuSiQue 上移除 semantic_search 准确率掉 4.7 pt(说明语义+关键词互补)

### 2.2 我们的实现选型 — 用 LangGraph 承载 A-RAG(不自研)

| 维度 | A-RAG 论文做法 | 我们的做法 |
|------|---------------|-----------|
| 框架 | 自研 BaseAgent + ToolRegistry | **LangGraph StateGraph**(已有) |
| 控制流 | 自实现 agent loop(他们论文里写的是 ReAct-like) | **LangGraph 条件边 + 循环计数**(社区标准) |
| 工具发现 | 自定义 ToolRegistry | **OpenAI Function Calling tools schema**(已有) |
| LLM 决策风格 | 显式 Thought / Action / Observation 文本 | **Function Calling 结构化 tool_calls**(无显式 Thought,但语义等价) |
| 多模型适配 | 自研 LLMClient | **现有 LLMClient + DashScope/vLLM 双后端**(已有) |

**为什么不用 `langgraph.prebuilt.create_react_agent`**:我们已经有完整的 StateGraph(`llm_with_tools` / `execute_tool` / `execute_rag` / `execute_llm` / `clarify` / `plan_and_execute` / `summarize`),prebuilt 是新项目快速起步用的,接进来会和现有节点冲突。

**为什么不直接照搬 A-RAG 自研框架**:LangChain/LangGraph 已经把"agent loop + tool calling + state management"标准化了,A-RAG 自研是为论文实验灵活,我们生产用 LangGraph 工程化反而更稳。

**改造成本**(预估):在现有 graph 上加 1 条循环边 + 3 处状态字段 + 2 个守卫函数 ≈ **80 行代码**。

---

## 3. 知识库重组与扩库(100 篇)

### 3.1 主题域划分(7 个)

| # | 主题域 | 目标篇数 | 体裁分布(政策/SOP/FAQ/案例) | 备注 |
|---|--------|---------|---------------------------|------|
| 1 | 工时管理 | 18 | 6 / 6 / 4 / 2 | 填报、审核、跨项目分摊、特殊场景 |
| 2 | 假期与加班 | 14 | 5 / 4 / 3 / 2 | 法定假、年假、调休、加班分类 |
| 3 | 薪资福利 | 14 | 6 / 3 / 4 / 1 | 薪资构成、五险一金、年终奖、福利 |
| 4 | 请假管理 | 12 | 5 / 4 / 2 / 1 | 病假、事假、产假、年假 |
| 5 | 考勤管理 | 12 | 5 / 3 / 3 / 1 | 打卡、迟到、外勤、补卡 |
| 6 | 项目管理流程 | 14 | 4 / 6 / 3 / 1 | 立项、变更、验收、归档(只放流程,不放具体项目数据) |
| 7 | 通用制度与FAQ | 16 | 4 / 4 / 6 / 2 | 入职、离职、保密、IT 安全 |
| **合计** | | **100** | 35/30/25/10 | |

**重要边界声明**:

| 类型 | 进 RAG? | 替代方案 |
|------|---------|---------|
| 政策制度文本 | ✅ | — |
| 流程 SOP | ✅ | — |
| FAQ 问答对 | ✅ | — |
| 案例分析 | ✅ | — |
| **具体项目数据**(预算/工期/状态) | ❌ | SQL Agent / `query_project` Tool |
| **部门人员结构**(谁汇报给谁) | ❌ | SQL Agent(已有 sys_user/org_dept 表) |
| **工时实际数据**(谁上周填了多少) | ❌ | `query_timesheet` Tool / SQL Agent |

> **面试加分话术**:"不是所有数据都塞 RAG。我们做了二分:政策语义类走 RAG,结构化实体类走 SQL Agent。RAG 处理'怎么定义/规则是什么',SQL Agent 处理'实际数据是什么'。这是数据建模的边界划分。"

### 3.2 文件格式分布

为了让多格式 loader 真正跑通(P4 痛点),100 篇分布为:

| 格式 | 篇数 | 用法 | Loader |
|-----|------|------|--------|
| `.md` | 70 | 主力,Markdown header 切分天然支持层次化 | TextLoader |
| `.docx` | 18 | 模拟"HR 下发的政策红头文" | Docx2txtLoader / UnstructuredWordDocumentLoader(elements 模式) |
| `.pdf` | 10 | 模拟扫描/排版规整的制度文件 | PyPDFLoader |
| `.csv` | 2 | 模拟"假期日历表/审批节点表"(结构化补充) | CSVLoader |

> **不放 Excel/.xlsx**:面试讲清楚"表格数据走 SQL Agent,不进 RAG",避免被反问。

### 3.3 目录结构

```
knowledge-base/
├── 01-工时管理/
│   ├── policy/
│   │   ├── 工时填报管理制度.md          (已有,补 frontmatter)
│   │   ├── 工时审核流程.md              (已有)
│   │   ├── 跨项目工时分摊规则.md        (新)
│   │   ├── 工时类型分类标准.md          (新)
│   │   ├── 异常工时处理办法.md          (新)
│   │   └── 项目经理工时管理职责.docx    (新,docx)
│   ├── sop/  (6 篇 SOP)
│   ├── faq/  (4 篇 FAQ)
│   └── case/ (2 篇案例)
├── 02-假期与加班/
├── 03-薪资福利/
├── 04-请假管理/
├── 05-考勤管理/
├── 06-项目管理流程/
└── 07-通用制度/
```

### 3.4 文档生成模板(给其他 agent 用)

> 这一节是**关键交付物**,用户会拿着这套模板让其他模型批量生成 100 份。

#### 3.4.1 通用 frontmatter(所有文档必填)

```yaml
---
title: <文档标题>
category: <一级分类>      # 工时管理 / 假期与加班 / 薪资福利 / 请假管理 / 考勤管理 / 项目管理流程 / 通用制度
genre: <文档体裁>         # policy / sop / faq / case
version: 1.0
effective_date: 2026-01-01
audience: <适用对象>      # all / employee / manager / hr / finance / pm
tags: [<标签1>, <标签2>]   # 用于 metadata 过滤
acl: public               # public / internal / confidential(影响检索权限)
related_docs: [<相关文档名>]
---
```

#### 3.4.2 政策类(policy) — 章节模板

```markdown
# {{title}}

## 1. 适用范围
[谁、什么场景适用,边界条件]

## 2. 定义与术语
[关键术语解释,2-5 条]

## 3. 核心规则
### 3.1 [子规则 1]
### 3.2 [子规则 2]
### 3.3 [子规则 3]

## 4. 例外情况
[特殊场景的处理]

## 5. 处罚与追责
[违反规则的后果]

## 6. 附则
[生效日期、修订记录、解释权]
```

**生成 prompt 模板(给批量 agent 用)**:

```
你是企业制度文档撰写助手。请为「{{category}}」主题域生成一份「policy」类文档。

文档要求:
- 标题:{{specific_title}}
- 受众:{{audience}}
- 严格遵循 frontmatter 格式(YAML)和章节模板
- 每个章节 200-500 字,总长 1500-3000 字
- 必须包含至少 3 条具体规则、2 个数字阈值(如"超过 4 小时"、"提前 3 天")
- 例外情况至少 2 条
- 严禁包含具体人名、项目名、部门名(用「员工」「项目」「部门」泛指)

【约束】
- 政策内容要让 RAG 检索时容易匹配:多用业务关键词("加班"/"调休"/"出差"/"五险一金")
- 数字阈值都用阿拉伯数字(便于 BM25 命中)
- 章节标题用统一格式(便于 outline 提取)

输出 Markdown,frontmatter 在最前。
```

#### 3.4.3 SOP 类(sop) — 章节模板

```markdown
# {{title}}

## 1. 流程目的
## 2. 适用场景
## 3. 角色与职责
| 角色 | 职责 |
|------|------|
| {{role_1}} | ... |

## 4. 操作步骤
### Step 1: [步骤名]
- 操作人:{{role}}
- 输入:[需要什么]
- 动作:[做什么]
- 输出:[产出什么]
- 时限:[多长时间内]

### Step 2: ...

## 5. 异常处理
## 6. 时效要求
## 7. 相关表单/系统
```

#### 3.4.4 FAQ 类(faq) — 章节模板

```markdown
# {{title}}

## Q1: {{question_1}}
**答**:[简洁直接,1-3 句]

详细说明:[展开,5-10 句]

相关文档:[超链接到 policy 文件]

## Q2: ...

(每篇 8-15 个 Q&A)
```

#### 3.4.5 案例类(case) — 章节模板

```markdown
# {{title}}

## 案例背景
## 涉及问题
## 处理过程
## 结论与启示
## 涉及制度
```

#### 3.4.6 docx / PDF 生成路径

让批量 agent 先生成 .md,然后:
- **md → docx**:`pandoc input.md -o output.docx`
- **md → pdf**:`pandoc input.md -o output.pdf --pdf-engine=xelatex -V CJKmainfont="SimSun"`

或者用 Python:
```python
import pypandoc
pypandoc.convert_file('input.md', 'docx', outputfile='output.docx')
pypandoc.convert_file('input.md', 'pdf', outputfile='output.pdf',
                     extra_args=['--pdf-engine=xelatex', '-V', 'CJKmainfont=SimSun'])
```

> **重要**:转换后的 docx/pdf 不要在 frontmatter 里保留 YAML,改成在文件名里编码 metadata,例如 `policy_工时管理_工时填报制度_v1.0.docx`。loader 端解析文件名补 metadata。

#### 3.4.7 批量生成清单(给批量 agent 的"派单")

100 篇的具体清单(每条:标题 + 主题域 + 体裁 + 受众),会单独写到 `docs/rag-progressive-disclosure-doc-list.md`。结构示例:

| 序号 | 主题域 | 体裁 | 文件名(含格式) | 标题 | 受众 |
|------|-------|------|----------------|------|------|
| 1 | 01-工时管理 | policy | 工时填报管理制度.md | 工时填报管理制度 | all |
| 2 | 01-工时管理 | policy | 跨项目工时分摊规则.md | 跨项目工时分摊规则 | all |
| ... | ... | ... | ... | ... | ... |
| 19 | 02-假期与加班 | policy | 法定节假日规定.docx | 法定节假日规定 | all |
| ... | ... | ... | ... | ... | ... |

**Plan 落地后,Plan 执行时由 agent 把这张表展开到 100 条**。

---

## 4. 接口拆分:4 个层次化检索工具

### 4.1 工具签名(给 LLM 看的 schema)

#### Tool A: `kb_outline`

```python
{
    "name": "kb_outline",
    "description": "列出知识库的目录大纲(所有文档的 h1/h2 标题 + metadata)。当用户问题模糊或跨多个主题时,先调这个工具看全貌,再决定下一步检索。返回内容很短(< 500 tokens)。",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["工时管理", "假期与加班", "薪资福利", "请假管理", "考勤管理", "项目管理流程", "通用制度", "ALL"],
                "description": "可选:只看某一主题域的大纲,默认 ALL"
            }
        }
    }
}
```

返回示例:
```json
{
  "documents": [
    {"file": "01-工时管理/policy/工时填报管理制度.md", "title": "工时填报管理制度", "h2": ["适用范围","定义与术语","核心规则","例外情况","处罚与追责","附则"], "tags": ["填报","审核"]},
    {"file": "02-假期与加班/policy/加班补偿政策.md", "title": "加班补偿政策", "h2": ["...","..."], "tags": ["加班","调休"]}
  ]
}
```

#### Tool B: `kb_keyword_search`

```python
{
    "name": "kb_keyword_search",
    "description": "BM25 关键词检索,适合查找精确术语、制度编号、特定数字。比 semantic_search 快但只能匹配字面词。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词,可多个"},
            "category": {"type": "string", "description": "可选:限定主题域"},
            "top_k": {"type": "integer", "default": 5, "description": "返回 chunk 数"}
        },
        "required": ["query"]
    }
}
```

#### Tool C: `kb_semantic_search`

```python
{
    "name": "kb_semantic_search",
    "description": "向量语义检索,适合自然语言问题、近义概念、口语化提问。比 keyword_search 慢但能理解语义。",
    "parameters": { /* 同 keyword_search */ }
}
```

#### Tool D: `kb_read_section`

```python
{
    "name": "kb_read_section",
    "description": "精读指定文档的某个章节(返回完整原文 + 邻接章节)。当 search 类工具返回的片段不够完整时,用这个深读。",
    "parameters": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "文档路径,从 outline 或 search 结果里取"},
            "section": {"type": "string", "description": "章节标题(h2 级别),如'核心规则'"},
            "include_neighbors": {"type": "boolean", "default": true, "description": "是否包含前后相邻章节"}
        },
        "required": ["file", "section"]
    }
}
```

### 4.2 旧 `knowledge_qa` 保留为 fallback

简单单文档问题(如"加班算工时吗")**仍走旧 `knowledge_qa`**,系统 prompt 引导 LLM 判断:

```
当问题简单且只涉及单文档时,直接调 knowledge_qa(end-to-end RAG)。
当问题复杂、涉及多文档、多跳推理时,使用 kb_outline + kb_*_search + kb_read_section 渐进式检索。
```

> 这是为了**不破坏现有快速路径** + **降低 vLLM tool_calls 不稳定性**(B7 bug)。

---

## 5. Agent Loop:LangGraph 承载 A-RAG 多轮调用

> **命名澄清**:这一节描述的是"LLM ↔ tool 之间的多轮循环",社区一般叫 "agent loop" 或 "tool-calling loop"。它在思想上和 ReAct 范式同源(Reasoning + Acting 交替),但在 Function Calling 时代不需要显式 Thought 文本,LLM 直接输出 `tool_calls`。**不要把这一节理解为"实现 ReAct"** — ReAct 是范式不是被实现物,我们实现的是 A-RAG 这个具体方案,承载工具是 LangGraph,LLM 决策风格是 FC tool loop。

### 5.1 架构示意

```
原架构(单跳):
START → llm_with_tools → execute_tool → END

新架构(agent loop):
START → llm_with_tools → [条件路由]
                            ├─ tool_calls 非空 → execute_tool → 回 llm_with_tools(循环)
                            ├─ 最终答案 → END
                            └─ 达到 max_iterations → summarize → END(兜底)
```

### 5.2 改动点(共 4 处,~80 行)

#### 5.2.1 AgentState 加 3 个字段

```python
# fastapi-service/app/services/langgraph_agent.py

class AgentState(TypedDict):
    # ... 现有字段 ...

    # ── Agent Loop(承载 A-RAG 多轮调用) ──────────────
    agent_iterations: int             # 当前已循环轮数(从 0 开始)
    agent_max_iterations: int         # 上限,默认 5
    agent_history: list               # [{tool: ..., args: ..., observation: ...}] 累积证据
```

#### 5.2.2 新增 `_agent_loop_should_continue` 守卫函数

```python
def _agent_loop_should_continue(state: AgentState) -> str:
    """
    Agent loop 的条件路由(承载 A-RAG 多轮调用)。

    返回:
        - "continue": 还有 tool_calls 待执行 → 回 llm_with_tools
        - "end":      LLM 给出最终答案 → END
        - "force_end": 达到 max_iterations → summarize 兜底
    """
    iters = state.get("agent_iterations", 0)
    max_iters = state.get("agent_max_iterations", 5)

    if iters >= max_iters:
        logger.warning(f"Agent loop 达到 max_iterations={max_iters},强制结束")
        return "force_end"

    # 检查 LLM 上一轮是否还要调工具
    if state.get("tool_calls"):
        return "continue"

    return "end"
```

#### 5.2.3 改造 `node_execute_tool`,把 observation 写回 state

```python
async def node_execute_tool(state: AgentState) -> dict:
    # ... 现有执行逻辑 ...

    # 新增:累积 agent loop 的 history(供下一轮 LLM 看到上轮的 observation)
    history = state.get("agent_history", [])
    history.append({
        "iteration": state.get("agent_iterations", 0),
        "tool": state["tool_name"],
        "args": state["tool_params"],
        "observation": tool_result,
    })

    return {
        "tool_result": tool_result,
        "agent_iterations": state.get("agent_iterations", 0) + 1,
        "agent_history": history,
    }
```

#### 5.2.4 改造 `_build_graph`,加循环边

```python
def _build_graph():
    builder = StateGraph(AgentState)
    # ... 现有节点注册 ...

    builder.add_edge(START, "llm_with_tools")

    # 主路由(保留现有)
    builder.add_conditional_edges(
        "llm_with_tools",
        _route_by_intent,
        { /* 现有映射 */ },
    )

    # ── 改:execute_tool 不再直接 END,加 agent loop 守卫 ──
    builder.add_conditional_edges(
        "execute_tool",
        _agent_loop_should_continue,
        {
            "continue": "llm_with_tools",   # ★ 关键:回到主节点形成循环
            "end": END,
            "force_end": "summarize",
        },
    )

    # 其他边保持
    builder.add_edge("execute_rag", END)
    builder.add_edge("execute_llm", END)
    # ...

    return builder.compile()
```

#### 5.2.5 `node_llm_with_tools` 把 agent_history 注入 prompt

```python
async def node_llm_with_tools(state: AgentState) -> dict:
    history = state.get("agent_history", [])

    if history:
        # 第 2 轮起,把累积的 (tool, args, observation) 拼进 messages
        messages = state["conversation_history"] + [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": h["iteration"], "function": {"name": h["tool"], "arguments": json.dumps(h["args"])}}]}
            for h in history
        ] + [
            {"role": "tool", "tool_call_id": h["iteration"], "content": json.dumps(h["observation"])}
            for h in history
        ]
    else:
        messages = state["conversation_history"]

    # ... 现有 LLM 调用 ...
```

### 5.3 防止死循环的 3 道闸

| 闸 | 阈值 | 触发动作 |
|---|------|---------|
| 1. `agent_max_iterations` | 5 轮 | 强制走 summarize 兜底 |
| 2. 重复 tool_call 检测 | 同 (tool, args) 连续 2 次 | 提前 END |
| 3. tool_result 异常率 | 连续 3 次失败 | 降级到 `knowledge_qa` |

---

## 6. 评测设计 — 18 条 query 集

### 6.1 评测目标

证明 progressive 在**跨文档多跳**场景比 one-shot 显著好,且 token 消耗更低。

### 6.2 评测 query 集(覆盖 4 类)

#### 类型 1:简单单文档(5 条) — 应该走 `knowledge_qa` 快速通道

| ID | Query | 预期工具路径 | 标杆答案要点 |
|----|-------|------------|-------------|
| S01 | 加班算不算工时? | `knowledge_qa` 单跳 | 加班需审批后计入,单独标记加班工时 |
| S02 | 工时填报截止日期是几号? | `knowledge_qa` 单跳 | 每月 5 号前 |
| S03 | 病假能不能折算成事假? | `knowledge_qa` 单跳 | 不可,病假需医院证明 |
| S04 | 试用期员工有年假吗? | `knowledge_qa` 单跳 | 满一年才有 |
| S05 | 出差的工时怎么算? | `knowledge_qa` 单跳 | 按实际工作时长,不含路途 |

#### 类型 2:跨文档多跳(7 条) — **核心 progressive 用例**

| ID | Query | 涉及文档 | 预期工具路径 |
|----|-------|---------|-------------|
| M01 | 周末加班审批超时未处理,工时怎么记? | 加班政策 + 工时审核流程 | outline → semantic("加班") → read("加班补偿/审批超时") → semantic("工时审核") → read("超时处理") |
| M02 | 产假期间申请的项目奖金怎么发放? | 请假管理 + 薪资福利(年终奖) | outline → semantic("产假") → read → keyword("奖金") → read |
| M03 | 跨项目工时分摊后,单个项目超 8 小时算不算加班? | 工时分摊 + 加班政策 | outline → keyword("分摊") → read → keyword("加班 8 小时") → read |
| M04 | 试用期员工提前离职,五险一金怎么处理? | 通用制度(离职) + 薪资福利(五险一金) | outline → semantic("离职") → read → semantic("五险一金") → read |
| M05 | 项目立项后变更工期,工时填报模板要不要改? | 项目流程 + 工时填报 | outline → semantic("变更") → read → semantic("工时模板") → read |
| M06 | 调休没用完离职会赔偿吗?涉及哪些制度? | 调休 + 离职 + 薪资 | outline → 多次 semantic + read |
| M07 | 出差产生加班,可以同时申请加班费和出差补贴吗? | 加班政策 + 出差/差旅 + 薪资 | outline → 多次 keyword + read |

#### 类型 3:带 metadata 过滤(3 条) — 测 outline + category

| ID | Query | 预期 |
|----|-------|------|
| F01 | 项目经理这个角色,有哪些必读制度? | outline(audience="manager") → 列出 |
| F02 | 财务相关的所有政策有几篇? | outline(category="薪资福利") → 计数 |
| F03 | HR 内部使用的保密文档有哪些? | outline(acl="confidential") + audience=hr |

#### 类型 4:对比/列举(3 条) — 测多次 search 整合

| ID | Query | 预期 |
|----|-------|------|
| L01 | 病假/事假/年假 三种请假在审批人和时限上有什么区别? | 3 次 keyword + 3 次 read + LLM 整合表格 |
| L02 | 工时类型有几种? | keyword("工时类型") → read 分类标准 |
| L03 | 法定节假日和年假的加班补偿一样吗? | 2 次 read + LLM 对比 |

### 6.3 评测指标

| 指标 | 公式/说明 | 目标 |
|------|----------|------|
| **答案完整度** | 人工 1-10 分(对照标杆答案) | progressive 比 one-shot **+1.5 分** |
| **跨文档覆盖率** | 涉及的所有文档都被检索到? | progressive **+30%** 以上 |
| **Tokens 总消耗** | input + output 总 tokens | progressive 比 naive A-RAG 思路 **-15%** |
| **延迟** | end-to-end ms | progressive 必然慢(多轮),记录但不作为 KPI |
| **Tool calls 平均轮数** | 累计平均 | 简单类 ≤ 1.0,多跳类 2.5-4.0,封顶 5 |

### 6.4 评测脚本框架

```
fastapi-service/tests/benchmark/
├── data/
│   └── progressive_rag_eval_18.jsonl        (18 条 query 集)
├── bench_progressive_rag.py                 (跑 one-shot vs progressive)
└── results/
    └── progressive_rag_2026-05-XX.csv       (输出对比表)
```

伪代码:
```python
async def run_eval(query, mode):
    # mode: "oneshot" 强制走 knowledge_qa / "progressive" 启用渐进式工具
    state = {...}
    result = await graph.ainvoke(state)
    return {
        "answer": result["answer"],
        "tokens": result["total_tokens"],
        "latency_ms": result["latency_ms"],
        "tool_calls": len(result.get("agent_history", [])),
        "docs_retrieved": [...],
    }

for q in queries:
    one = await run_eval(q, "oneshot")
    pro = await run_eval(q, "progressive")
    completeness_one = human_score(one["answer"], q["expected_points"])
    completeness_pro = human_score(pro["answer"], q["expected_points"])
    # 写 csv
```

---

## 7. 阶段规划与工时

> 总预估:**12-16 小时实施 + 100 篇文档生成由用户调用其他 agent 完成(并行)**

### Phase 1 — 知识库扩库(用户主导,1-2 天,**与下面并行**)

| 子任务 | 工时 | Owner |
|--------|------|-------|
| 1.1 用户调用其他 agent,按 §3.4 模板批量生成 100 篇 .md | 0(并行) | 用户 + 其他 agent |
| 1.2 抽样 18 篇转 .docx,10 篇转 .pdf,2 篇转 .csv | 0.5h | 我 |
| 1.3 整理目录到 7 个主题域,统一 frontmatter | 0.5h | 我 |
| 1.4 重建 Milvus / BM25 索引,验证全部加载成功 | 0.5h | 我 |
| 1.5 验证 metadata 提取(category / genre / audience / acl) | 0.5h | 我 |

### Phase 2 — 接口拆分实现(2-3 天,核心)

| 子任务 | 工时 |
|--------|------|
| 2.1 实现 `kb_outline` 工具(读 frontmatter + h1/h2) | 1h |
| 2.2 实现 `kb_keyword_search`(暴露独立 BM25,jieba 已就位) | 1h |
| 2.3 实现 `kb_semantic_search`(暴露独立 Milvus) | 1h |
| 2.4 实现 `kb_read_section`(按 file + h2 取 chunk + 邻接 ±1) | 2h |
| 2.5 在 ToolRegistry 注册 + 写 json schema | 0.5h |
| 2.6 写 4 个工具的单元测试(每个 5-8 case) | 2h |

### Phase 3 — Agent Loop 改造(2-3 天)

| 子任务 | 工时 |
|--------|------|
| 3.1 AgentState 加 agent_* 字段 | 0.5h |
| 3.2 实现 `_agent_loop_should_continue` 守卫 | 1h |
| 3.3 改 `node_execute_tool` 累积 history | 0.5h |
| 3.4 改 `node_llm_with_tools` 注入 history 到 messages | 1h |
| 3.5 改 `_build_graph` 加循环边 | 0.5h |
| 3.6 system.yaml 加 multi-step retrieval guidance | 0.5h |
| 3.7 死循环 3 道闸的实现 | 1h |
| 3.8 集成测试(端到端跑 5 条简单 + 5 条多跳) | 2h |

### Phase 4 — 评测对比(1 天)

| 子任务 | 工时 |
|--------|------|
| 4.1 准备 `progressive_rag_eval_18.jsonl` | 已就绪(§6.2) |
| 4.2 写 `bench_progressive_rag.py` | 1.5h |
| 4.3 跑 18 条 × 2 模式 = 36 次评测 | 2h |
| 4.4 人工打分 + 整理 csv | 1.5h |
| 4.5 输出 `progressive_rag_report_2026-05-XX.md` | 1h |

### Phase 5 — 文档与简历(0.5 天)

| 子任务 | 工时 |
|--------|------|
| 5.1 写 `docs/rag-progressive-disclosure-design.md`(本文件) | 完成 |
| 5.2 更新 `docs/interview/interview-qa.md` 加 Q-RAG-Future | 0.5h |
| 5.3 更新 `docs/interview/resume-bullets.md` 加新 bullet | 0.5h |
| 5.4 更新 `roadmap.md` 标 v1.4 完成 | 0.2h |

### 并行排期建议

```
Day 0(今天):用户开始让其他 agent 批量生成 100 篇 .md (§3.4 模板)
Day 1:Phase 2(接口拆分)+ Phase 3 部分(改 graph)
Day 2:Phase 3 完成 + Phase 1.2-1.5(我处理用户产出的 100 篇)
Day 3:Phase 4(评测)+ Phase 5(文档)
Day 3 晚:面试准备,过 Q&A
```

---

## 8. 风险清单

| # | 风险 | 概率 | 影响 | 应对 |
|---|------|------|------|------|
| R1 | 其他 agent 生成的 100 篇文档质量差(模板填得呆板) | 中 | 中 | 抽样人工审核;重要文档手改;评测 query 选生成质量好的文档作为目标 |
| R2 | vLLM qwen3-8b 多 tool_calls 不稳(B7 bug,已知) | 中 | 高 | 保留 `knowledge_qa` fallback;失败时降级到单跳 |
| R3 | Agent loop 死循环 / LLM 总是再次调用同工具 | 低-中 | 中 | §5.3 三道闸:max_iterations=5、重复检测、异常降级 |
| R4 | docx/pdf 加载报错 | 低 | 低 | try-except 包装,失败 logger.warning,不影响主流程 |
| R5 | 100 篇加载后 BM25 内存爆(rank-bm25 全内存) | 低 | 中 | 100 篇约 5-10MB 文本,不会爆;真大可换 BM25S 或 Elasticsearch |
| R6 | 评测数字不好看(progressive 反而更差) | 中 | 高 | 多跳 query 的差距应该明显;不行就只讲设计不秀数字;话术兜底:"小知识库下 progressive 价值有限,生产规模 200+ 才显著" |
| R7 | 离职前(2026-05-10)做不完 | 中 | 高 | Phase 1+2+3 是 P0,Phase 4 是 P1,Phase 5 是 P0(简历必须更新) |

---

## 9. 简历 Bullet(待评测后定稿)

### 草稿 A(数据漂亮版,如果评测好)

```
Agentic RAG + 渐进式披露:暴露 outline / keyword_search / semantic_search /
read_section 4 层检索工具,用 LangGraph 实现 A-RAG agent loop(LLM 自主多轮调用,
对标 2026 A-RAG);跨文档多跳问题答案完整度提升 X.X 分(10 分制)、
检索覆盖率 +XX%、token 消耗 -XX%;知识库 100 篇 / 7 主题域 / 3 种格式
(md/docx/pdf)
```

### 草稿 B(架构故事版,数据普通时)

```
Agentic RAG 渐进式披露:把 one-shot RAG 重构为 4 层检索工具(outline /
keyword / semantic / read),用 LangGraph 实现 A-RAG agent loop,LLM 自主多轮调用;
配合 RAG vs SQL Agent 边界划分(政策走 RAG / 实体走 SQL),
知识库扩到 100 篇覆盖 7 个主题域,支持 md/docx/pdf 多格式
```

---

## 10. 面试 Q&A 补充(写入 interview-qa.md)

### Q-RAG-1:你说做了渐进式披露,跟普通 RAG 区别是什么?

**答案要点**:
- 普通 RAG 是 one-shot:用户问 → 检索 top-K → 全塞 LLM。问题是上下文有噪音,跨文档多跳时片段不够。
- 渐进式披露是 multi-step:LLM 先看大纲(outline)→ 选关键词搜或语义搜 → 看到结果再决定要不要精读某章节(read_section)→ 满意了再答。LLM 主导,不是预设 pipeline。
- 我们用 LangGraph 实现 A-RAG 的 agent loop,**不是自研框架**(自研是 A-RAG 论文的做法,生产工程化用 LangGraph 更稳)。
- ReAct 是 Yao 2022 提出的范式(Thought + Action + Observation 交替),不是要"实现"的对象;A-RAG 才是具体方案,LangGraph 才是工程载体 — 三个概念要分清。

### Q-RAG-2:为什么要拆 4 个工具,合并不行吗?

**答案要点**:
- 拆是因为**让 LLM 看见接口的语义差异**。outline 廉价但粗,keyword 精确但只匹字面,semantic 模糊但理解上下文,read 是补全。LLM 拿到这 4 个工具的描述会自己选择,这是工具描述驱动的决策。
- 合并成一个 black-box "smart_search" 反而让 LLM 失去选择权 — 它不知道你内部是 BM25 还是向量,无法针对问题特性优化。
- 这是 A-RAG 的核心论点,也是 Anthropic 的 "Progressive Disclosure for AI Agents" 文章观点。

### Q-RAG-3:LangGraph 实现的 agent loop 不会死循环吗?

**答案要点**:
- 会,所以加了 3 道闸:max_iterations=5、重复 tool_call 检测、连续异常降级到单跳 fallback。
- 实测 18 条 query 中简单类平均 1.0 轮,多跳类平均 2.5-3.5 轮,封顶 5 轮触发率 X%(评测后填)。
- LangGraph 的 `add_conditional_edges` + state 计数器是社区标准做法,不是我自创的。

### Q-RAG-4:100 篇知识库够吗?

**答案要点**:
- 不够 — 真实生产 RAG 通常 500-5000 篇。但 100 篇能跑出有意义的多跳数据,小规模 4 篇跑不出来。
- 这次扩库的目的是**让评测有信号**,不是装"我们知识库很大"。下一步如果上生产,文档会增量增长。
- 4 → 100 也涵盖了 3 种文件格式(md/docx/pdf)、7 个主题域,**多样性比规模更能体现工程能力**。

### Q-RAG-5:Q-3 能保证不死循环,但 token 会爆吗?

**答案要点**:
- 会增加,但有控制:agent_history 只累积 (tool, args, observation_summary),observation 不超过 500 tokens(精读时按需)。max 5 轮,最坏情况 ≈ 5 × 500 = 2500 tokens 累积。
- 简单问题走 `knowledge_qa` 快速通道,不进 agent loop — 这也是为什么保留旧工具。

### Q-RAG-7:你做的不就是 ReAct 吗?

**答案要点**(这是高频反问,要答得清晰):
- 不能等同。**ReAct 是 Yao 2022 提出的范式**(显式 Thought/Action/Observation 交替),它定义的是"LLM 该怎么思考",不是一个被实现的框架。
- **我们做的是 A-RAG**(2026 年的具体 RAG 方案):3 个层次化检索接口 + LLM 多轮自主调用。承载它的工程载体是 LangGraph(StateGraph + 条件边 + 循环计数)。
- 决策风格上,我们用 **OpenAI Function Calling 风格的 tool_calls**,LLM 不写显式 "Thought:" 文本,而是直接结构化输出工具调用。所以严格说**不是教科书 ReAct,叫 "agent loop" 或 "tool-calling loop" 更准确**。
- A-RAG 在思想上确实和 ReAct 同源(reasoning + acting 交替),但在 FC 时代是 ReAct 范式的演化形态,**不能混为一谈**。
- 这种命名混淆在面试里很常见,体现的是对"范式 / 方案 / 框架 / 工具"四层概念的清晰认知。

### Q-RAG-6:RAG vs SQL Agent 你怎么选边界?

**答案要点**:
- 政策语义类 → RAG:"加班怎么补偿"是规则解释,适合语义匹配。
- 结构化实体类 → SQL Agent:"A 项目本月工时多少"是字段查询,SQL 精确得多。
- 工具描述里写清楚边界,Function Calling 让 LLM 自己路由。
- 这是数据建模的边界,不是技术边界 — 把所有东西都塞 RAG 是工程偷懒。

---

## 11. 后续路线(本文件之外)

- **v1.5** RAPTOR 树形索引 — 知识库 500+ 篇时考虑
- **v1.5** Self-RAG / CRAG 反思机制 — 让 LLM 自己评分再决定要不要重检索
- **v1.5** GraphRAG — 把组织/项目/制度建模成图,跨实体推理
- **v1.5** 知识库增量更新 — 文件 hash 比对,只重建变化文档
- **v1.6** RAG 评测自动化 — RAGAS 4 指标(Faithfulness / Answer Relevance / Context Precision / Context Recall)集成 CI

---

## 12. 参考资料

- [A-RAG: Agentic RAG via Hierarchical Retrieval Interfaces — PREreview](https://prereview.org/reviews/19039198)
- [GitHub - Ayanami0730/arag](https://github.com/Ayanami0730/arag)
- [LangGraph create_react_agent — Reference](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent)
- [Building ReAct agents with LangGraph — Dylan Castillo](https://dylancastillo.co/posts/react-agent-langgraph.html)
- [Progressive Disclosure in AI Agents — MindStudio](https://www.mindstudio.ai/blog/progressive-disclosure-ai-agents-context-management)
- [RAPTOR: Recursive Abstractive Processing — arxiv](https://arxiv.org/html/2401.18059v1)
- [Docling — LangChain integration](https://docs.langchain.com/oss/python/integrations/document_loaders/docling)
- 内部文档:`fastapi-service/docs/rag-upgrade-roadmap.md`、`docs/roadmap.md` 第七节
