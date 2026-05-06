# [Agent F 派单] Phase 2 + 3:层次化工具实现 + Agent Loop 改造

> **使用方式**:把下面 `==== PROMPT START ====` 到 `==== PROMPT END ====` 之间的全部内容复制给 IDE coding agent(Claude Code / Cursor / Cline)。
> **何时启动**:立即可启动,不依赖文档生成 agent。
> **预估工时**:4-6 小时。

==== PROMPT START ====

# 角色

你是工时管理系统 ai-service 仓库的资深后端工程师。任务是按照设计文档实现 RAG 渐进式披露的核心改造 — Phase 2(层次化检索工具)+ Phase 3(LangGraph agent loop)。

# 项目根目录

`E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service`

如果你在其他工作目录,先 cd 进去。

# 必读文件(动手前先读完)

1. **设计文档**(权威依据):
   - `docs/rag-progressive-disclosure-design.md` — **重点读 §2.2 / §4 / §5**
2. **现有 RAG / Agent 代码**(理解上下文):
   - `fastapi-service/app/services/langchain_rag.py`(理解 BM25 / Milvus retriever 接口)
   - `fastapi-service/app/services/langgraph_agent.py`(理解 StateGraph 节点拓扑、AgentState)
   - `fastapi-service/app/services/tool_registry.py`(理解 Tool 注册机制)
   - `fastapi-service/app/services/task_executor.py`(理解 Tool 执行流程)
3. **现有 Tool 参考**(模仿其结构):
   - `fastapi-service/app/tools/query_timesheet.py` — 一个标准 Tool 写法
   - `fastapi-service/app/tools/__init__.py` — Tool 注册位置
4. **prompt 配置**:
   - `prompts/system.yaml`(要在里面加 multi-step retrieval guidance)

# 任务范围

## Phase 2:实现 4 个层次化检索工具

### 2.1 新建底层工具集模块

**文件**:`fastapi-service/app/services/kb_navigator.py`(新建)

实现以下纯函数(不依赖任何 Tool/Registry 框架):

```python
# 大致接口(具体参数请参考 design doc §4):

async def get_outline(category: str | None = None) -> dict:
    """读 knowledge-base 下所有 .md 文件的 frontmatter + h1/h2,返回轻量大纲。
    返回结构:{"documents": [{"file": "...", "title": "...", "h2": [...], "tags": [...], "category": "..."}]}
    实现思路:
      - 扫描 knowledge-base/ 目录(递归)
      - 解析每个文件的 YAML frontmatter
      - 提取所有 h2 标题(用正则 ^## .+)
      - 如果 category 不为 None,过滤掉不匹配的
      - 缓存结果(进程级 dict,文件 mtime 没变就用缓存)
    """

async def keyword_search(query: str, category: str | None = None, top_k: int = 5) -> list:
    """直接复用 langchain_rag 里已有的 BM25Retriever,加上 category metadata 过滤。
    返回 [{"file": "...", "section": "...", "score": ..., "snippet": "..."}, ...]"""

async def semantic_search(query: str, category: str | None = None, top_k: int = 5) -> list:
    """直接复用 langchain_rag 里 Milvus vector retriever,加上 category metadata 过滤。
    返回结构同 keyword_search。"""

async def read_section(file: str, section: str, include_neighbors: bool = True) -> dict:
    """读指定文档的指定 h2 章节(完整内容),如果 include_neighbors=True 还返回前后相邻 h2 章节。
    返回 {"file": "...", "section": "...", "content": "...", "neighbors": [{"section":..., "content":...}]}
    实现思路:
      - 路径必须在 knowledge-base/ 之下,防止越权读其他文件(security check)
      - 用正则切 ## h2 标题
      - 找到匹配的 section,取它和前后 ±1 个 h2 块
    """
```

**安全约束**:
- `read_section` 的 file 参数必须做路径校验,**禁止 `../` 越权**(用 `Path.resolve()` 检查是否在 knowledge-base 目录下)
- 所有函数都要 try-except,失败返回明确的错误结构而非抛异常上去

### 2.2 实现 4 个 Tool 包装

每个 Tool 一个文件,放到 `fastapi-service/app/tools/`:

| 文件 | Tool name |
|------|----------|
| `kb_outline.py` | kb_outline |
| `kb_keyword_search.py` | kb_keyword_search |
| `kb_semantic_search.py` | kb_semantic_search |
| `kb_read_section.py` | kb_read_section |

**Tool schema 严格按 design doc §4.1 写**(JSON Schema 格式)。每个 Tool 内部调用对应的 `kb_navigator.*` 函数。

**模仿 `query_timesheet.py` 的结构**:
- 同样的 import 顺序
- 同样的 `register_tool` 装饰器(或注册方式 — 看现有 Tool 怎么做就怎么做)
- 同样的错误处理模式

### 2.3 注册到 ToolRegistry

修改:`fastapi-service/app/tools/__init__.py`(或 ToolRegistry 注册的入口处)

把 4 个新 Tool 加进去。**保留 `knowledge_qa` 旧 Tool 不动**(它是 fallback)。

### 2.4 单元测试

**文件**:`fastapi-service/tests/unit/test_kb_navigator.py`(新建)

至少覆盖:
- `get_outline` 在没有 knowledge-base 目录时返回空列表(不抛异常)
- `get_outline` 能正确解析 frontmatter(造一个 mock 文档)
- `get_outline(category="工时管理")` 只返回该 category 的文档
- `read_section` 路径越权(`../etc/passwd`)被拒绝
- `read_section` 章节不存在时返回明确错误
- `read_section(include_neighbors=True)` 返回前后相邻 h2

**文件**:`fastapi-service/tests/unit/test_kb_tools.py`(新建)

至少覆盖:
- 4 个 Tool 的 json_schema 都有 name/description/parameters
- 每个 Tool 能正常调用(mock 底层 kb_navigator)
- 参数缺失时返回参数错误而不是崩溃

跑一遍验证:
```bash
cd fastapi-service
pytest tests/unit/test_kb_navigator.py tests/unit/test_kb_tools.py -v
```

---

## Phase 3:LangGraph Agent Loop 改造

### 3.1 AgentState 加 3 个字段

**文件**:`fastapi-service/app/services/langgraph_agent.py`

```python
class AgentState(TypedDict):
    # ... 保留现有所有字段(向后兼容)...
    # ── Agent Loop(承载 A-RAG 多轮调用) ────────
    agent_iterations: int           # 默认 0
    agent_max_iterations: int       # 默认 5
    agent_history: list             # [{iteration, tool, args, observation}]
```

**重要**:`stream_agent_response` 入口处构造初始 state 时,要给这三个字段赋默认值。

### 3.2 实现 `_agent_loop_should_continue` 守卫

按 design doc §5.2.2 实现。**死循环 3 道闸**(§5.3)全部要落地:

1. `agent_iterations >= agent_max_iterations` → "force_end"
2. **重复 tool_call 检测**:同 `(tool_name, args_json_dumps)` 在最近 3 次出现 ≥ 2 次 → "end"(提前终止)
3. **连续异常**:`agent_history` 最后 3 条都是 error → "end"(降级)

### 3.3 改造 `node_execute_tool` 累积 history

按 design doc §5.2.3。每次执行后:
```python
history = state.get("agent_history", []) + [{"iteration": ..., "tool": ..., "args": ..., "observation": ...}]
return {"tool_result": ..., "agent_iterations": iters+1, "agent_history": history}
```

**注意**:observation 不能放完整大文本(避免 prompt 爆),要截断到 500 tokens 以内(用 `len(json.dumps(observation))` 粗算字符数 / 3 ≈ tokens)。

### 3.4 改造 `node_llm_with_tools` 注入 history

按 design doc §5.2.5。第 2 轮起,把 `agent_history` 拼成 OpenAI messages 格式:

```python
# 把历史的 (tool, args, observation) 转成 assistant tool_calls + tool messages
for h in history:
    messages.append({"role":"assistant","content":None,"tool_calls":[{
        "id":f"call_{h['iteration']}", "type":"function",
        "function":{"name":h["tool"],"arguments":json.dumps(h["args"])}
    }]})
    messages.append({"role":"tool","tool_call_id":f"call_{h['iteration']}",
                     "content":json.dumps(h["observation"], ensure_ascii=False)})
```

### 3.5 改 `_build_graph` 加循环边

按 design doc §5.2.4。把 `execute_tool` 的固定 `add_edge(execute_tool, END)` 替换成 conditional_edges:

```python
builder.add_conditional_edges(
    "execute_tool",
    _agent_loop_should_continue,
    {"continue":"llm_with_tools", "end":END, "force_end":"summarize"},
)
```

**注意**:不要影响其他节点的边(`execute_rag` / `execute_llm` / `clarify_node` 仍然直接 → END)。

### 3.6 prompt 引导

**文件**:`prompts/system.yaml`

加一段 multi-step retrieval guidance(中文,放在 system_prompt 里合适位置):

```
## 知识库检索策略

回答企业制度类问题时,有两种检索方式:

1. **简单单文档问题**(如"加班算工时吗"):直接调 `knowledge_qa` 工具,一次拿到答案。

2. **复杂或跨文档问题**(如"周末加班审批超时未处理,工时怎么记?"):用渐进式检索 —
   - 先 `kb_outline` 看大纲(可选,问题模糊或涉及多主题域时用)
   - 再 `kb_keyword_search` 或 `kb_semantic_search` 找相关章节
   - 最后 `kb_read_section` 精读关键章节
   - 信息不全时继续追加 search/read,信息够了直接生成回答

判断标准:问题里出现"和"/"同时"/"对比"/"涉及多个"等多跳信号,或者初次检索结果不完整,就走渐进式;否则走 knowledge_qa 快速通道。
```

### 3.7 集成测试

**文件**:`fastapi-service/tests/integration/test_progressive_rag.py`(新建)

至少 5 个用例:
1. 简单问题"加班算工时吗" → 应该走 `knowledge_qa`(单跳),`agent_iterations` 应 ≤ 1
2. 跨文档问题"周末加班审批超时,工时怎么记?" → 应该有 ≥ 2 次 tool calls,且至少包含 `kb_*` 类工具
3. **强制 max_iterations** 测试:mock LLM 永远返回 tool_call → 应该在 5 轮后强制终止,走 summarize
4. **重复 tool_call 检测**:mock LLM 连续返回相同 tool_call → 应该提前 "end"
5. **回退到 knowledge_qa**:mock kb_navigator 抛异常 3 次 → 应该降级到 knowledge_qa

跑测试:
```bash
cd fastapi-service
pytest tests/integration/test_progressive_rag.py -v
```

---

# 验收标准

完成所有以下条件后,任务才算完成:

- [ ] §2.1-2.4 全部实现,`pytest tests/unit/test_kb_*.py -v` 全过
- [ ] §3.1-3.6 全部实现,`pytest tests/integration/test_progressive_rag.py -v` 全过
- [ ] **服务能正常启动**:`cd fastapi-service && python main.py`(允许 vLLM/Milvus 连不上的警告,但不能因新代码报错)
- [ ] **冒烟测试**:`curl -X POST http://localhost:8000/api/ai/chat/stream -d '{"message":"加班算工时吗"}'` 返回正常
- [ ] **现有功能不回归**:`pytest tests/unit/ -v` 全过(不只是新加的)

# 不要做的事

- ❌ 不要修改 `docs/rag-progressive-disclosure-design.md`(那是 spec)
- ❌ 不要删除/重命名现有 `knowledge_qa` Tool(它是 fallback)
- ❌ 不要修改 `query_timesheet.py` 等其他业务 Tool
- ❌ 不要引入新的 Python 依赖(用现有 langchain / langgraph / pyyaml 即可)
- ❌ 不要预先扩库,知识库扩展由 Agent E 负责
- ❌ 不要跑评测,评测由 Agent G 负责

# Commit 规范

每个子任务一个 commit。建议粒度:

```
feat(progressive-rag): 新增 kb_navigator 底层模块 + 单元测试
feat(progressive-rag): 新增 4 个层次化检索 Tool 包装 + 注册
feat(progressive-rag): AgentState 增加 agent_loop 字段
feat(progressive-rag): execute_tool 累积 history,llm_with_tools 注入 history
feat(progressive-rag): _build_graph 加循环边 + 死循环 3 道闸
feat(progressive-rag): system.yaml 加 multi-step retrieval guidance
test(progressive-rag): 集成测试覆盖 5 个核心场景
```

每条 commit message 要带:
- 一句话说明做了什么
- 关键文件路径
- 不要带 emoji

# 完成后报告

执行完毕后,请输出一份简报:

```markdown
## Phase 2+3 改造完成报告

### 改动文件
- 新增:[文件清单]
- 修改:[文件清单]

### 测试结果
- pytest tests/unit/test_kb_*.py: ✅ N passed
- pytest tests/integration/test_progressive_rag.py: ✅ N passed
- 全量 pytest tests/unit/: ✅ N passed(无回归)

### 服务冒烟
- main.py 启动:[OK / 失败原因]
- /api/ai/chat/stream:[OK / 失败原因]

### 偏离设计的地方(如果有)
- [设计文档说 X,实际改为 Y,原因 ...]

### 下一步
- 等待 Agent E 完成知识库扩库
- 等待 Agent G 跑评测
```

==== PROMPT END ====
