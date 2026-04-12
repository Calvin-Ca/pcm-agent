# AI 智能助手 — 升级路线与优化建议

> 更新日期：2026-04-02
> 当前版本：1.1（已完成 MVP + Memory + RAG + 审计日志 + Function Calling + 参数解析层）

---

## 一、现状总结

已完成的核心能力：

| 模块 | 状态 | 说明 |
|------|------|------|
| 工时查询 / 项目查询 / 统计 | ✅ | Tool 调用 SpringBoot API |
| 周报生成 / 工时填报 | ✅ | Tool + 引导式多轮对话 |
| 知识库问答 (RAG) | ✅ | BM25 + Milvus 混合检索 + Reranker |
| 短期会话记忆 | ✅ | Redis，30分钟TTL，最近5轮 |
| 长期用户记忆 | ✅ | Redis + BM25 + 时间衰减 |
| Prompt 管理 | ✅ | YAML 热更新，LangChain 模板 |
| 审计日志 | ✅ | conversation_logs + ai_sessions 表 |
| LangGraph 编排 | ✅ | llm_with_tools（Function Calling）→ execute_tool/rag/llm/clarify，IntentRouter 降级为 fallback |
| Function Calling 架构 | ✅ | qwen-plus + tools schema，一次调用完成意图+参数提取（2026-03-31）|
| 统一参数解析层 | ✅ | param_resolver.py：项目名/成员名→ID，含进程级缓存（2026-04-01）|

---

## 二、~~已知 Bug~~（均已修复，2026-04-01）

### 2.1 ✅ 查工时返回全员数据（已修复）

**修复**：`query_timesheet.py` 第143行加 `elif params.user_id` fallback，无 `member_name` 时强制传当前用户 `memberId`。

> 详见 `docs/changelog/2026-04-01.md`

---

### 2.2 ✅ 填工时把项目名当项目 ID（已修复）

**修复**：新建 `param_resolver.py`，`save_workhour.py` 在发 API 前调用 `resolve_project_id()`，自动将项目名转为数字 ID。

> 详见 `docs/changelog/2026-04-01.md`

---

### 2.3 ✅ 参数处理逻辑分散（已修复）

**修复**：新建 `app/services/param_resolver.py` 统一处理项目名→ID、成员名→ID，含进程级内存缓存。

---

## 三、AI 能力扩展策略

### 核心问题：每增加一个能力都要写一个 Tool 吗？

不一定。根据能力类型选择不同方案，而不是都走 Tool：

```
用户问题
    │
    ├── 查规则/流程/制度  →  RAG（加文档，零开发）
    ├── 单次原子操作       →  Tool（有权限控制的写操作）
    ├── 接口种类多         →  MCP Server（批量接入）
    └── 复杂数据分析       →  Code Interpreter（LLM生成SQL/逻辑）
```

**边界原则**：
- AI 负责理解意图、选择工具、组合结果
- 后端负责执行、鉴权、数据校验
- Tool 只是 HTTP 包装，业务逻辑永远在后端

---

## 四、近期可落地的能力扩展

### 3.1 工时审核（Tool 方案）

**理由**：审核是写操作，有审批流，必须走 Tool 做权限控制。

```
新 Tool: approve_workhour
参数: workhour_id, action (approve/reject), comment
权限: 仅 deptAdmin 及以上
调用: POST /api/workhour/approve
```

涉及改动：
1. 新建 `fastapi-service/app/tools/approve_workhour.py`
2. 在 `task_executor.py` 补充权限判断（仅管理员可调用）
3. 在 `intent_router.py` 的 `tools_desc` 中加入描述

**预估工作量**：3-4 小时

---

### 3.2 导出报表（Tool 方案）

**理由**：报表种类有限，格式固定，走 Tool 调 SpringBoot 的 export 接口即可。

```
新 Tool: export_report
参数: report_type (workhour/project/department), date_range, format (excel/pdf)
调用: GET /api/report/export?...
返回: 下载链接或 base64 文件内容
```

**预估工作量**：2-3 小时

---

### 3.3 考勤异常查询（RAG + Tool 混合）

- 考勤规则/标准 → RAG（把制度文档加入 `knowledge-base/`）
- 查询某人考勤记录 → Tool（调 `/api/attendance/*`）

只需写一个新 Tool，规则说明部分直接走已有 RAG，零额外开发。

---

### 3.4 知识库扩充（零开发成本）

直接往 `knowledge-base/` 目录放文档，重启后自动加载（或调 `/api/rag/reload`）：

| 文档类型 | 示例 |
|----------|------|
| 考勤制度 | 考勤异常认定标准、打卡规则 |
| 审核流程 | 工时审核 SOP、补录流程 |
| 报表说明 | 各报表字段含义、使用说明 |
| HR 政策 | 年假、调休、加班补偿政策 |

---

## 五、中期升级方向

### 4.1 MCP Server 接入（解决接口扩展问题）

**适用场景**：当 Tool 数量超过 10 个，或后端接口频繁变动时。

**方案**：将 SpringBoot OpenAPI（Swagger）自动转换为 MCP Server，AI 自动发现所有可用接口。

```
SpringBoot Swagger JSON
        ↓
   MCP Server（自动生成工具描述）
        ↓
   AI（MCP Client）自动选择合适接口调用
```

**优点**：后端新增接口后，AI 无需代码改动即可使用
**注意**：需要做接口白名单控制，避免 AI 调用危险接口
**参考库**：`langchain-mcp-adapters`（已在 LangChain 生态中支持）

**预估工作量**：1-2 天（含接口白名单配置）

---

### 4.2 复杂多步骤任务（激活 PlannerAgent）

当前 `complex_request` 意图直接降级到 LLM 对话，未真正启用规划器。

**激活步骤**：
1. 在 LangGraph 图中添加 `plan_and_execute` 节点，调用已有的 `PlannerAgent`
2. 路由 `complex_request` → `plan_and_execute` → 并行执行多个 Tool
3. 汇总结果后生成最终回答

**解锁能力示例**：
- "查一下本月所有项目的工时，找出工时最多的前3个，生成对比报告"
- "帮我生成本周周报，同时查一下上周工时和本周有没有差异"

**预估工作量**：1 天

---

### 4.3 数据分析 / Code Interpreter 方向

**适用场景**：用户要自定义统计逻辑，超出现有 Tool 能力范围。

**方案**：让 LLM 生成 SQL 或 Python 数据处理代码，在沙箱中执行后返回结果。

```
用户："统计各部门近三个月工时趋势，按工时下降幅度排序"
  ↓
LLM 生成 SQL → 安全沙箱执行 → 返回数据 → LLM 生成图表描述
```

**注意事项**：
- 必须使用只读数据库连接
- SQL 需白名单过滤（禁止 UPDATE/DELETE/DROP）
- 考虑用 LangChain 的 `SQLDatabaseChain` 或 `create_sql_agent`

**预估工作量**：2-3 天（含安全控制）

---

## 六、技术债与优化项

### 5.4 统一参数校验层（中期重构）

**背景**：查工时/填工时 Bug 的共同根因是参数在多层之间流转时缺乏统一校验。

**方案**：在工具调用前增加一个参数解析层：

```
LLM 提取参数（可能含名称/错误类型）
        ↓
[参数解析层 — 新增]
  · 项目名 → 项目 ID（调 SpringBoot 查询）
  · 成员名 → 成员 ID（调 SpringBoot 查询）
  · 类型校验（用 Pydantic Model）
  · 缺参检测 → 触发追问
        ↓
工具执行（只接受已验证的 ID 和正确类型）
```

**涉及改动**：新增 `app/services/param_resolver.py`，各 Tool 的 handler 改为只接受解析后的参数。

**预估工作量**：1 天

---

### 原有技术债与优化项

按优先级整理，建议在每次迭代中穿插处理：

### 高优先级（影响稳定性）

| # | 问题 | 建议 | 改动量 |
|---|------|------|--------|
| 1 | 数据库密码硬编码在 `config.py` | 改为必填环境变量，去掉默认值 | 小 |
| 2 | 工具注册失败无感知 | 在 lifespan 中验证预期工具全部注册 | 小 |
| 3 | 工具调用无重试 | `execute_single_task` 加指数退避（最多3次） | 中 |

### 中优先级（可维护性）

| # | 问题 | 建议 | 改动量 |
|---|------|------|--------|
| 4 | 各 Tool 重复 ~150 行样板代码 | 创建 `tools/base.py` 基类 | 中 |
| 5 | 意图关键词散落在 `IntentRouter.__init__` | 提取到 `config/intent_rules.yaml` | 中 |
| 6 | LLM 调用缺少统一重试/限流 | `LLMClient.call_with_json_response()` 封装 | 中 |
| 7 | `log_conversation()` 参数过多（15+个） | 用 Pydantic 模型封装 | 小 |
| 8 | 记忆服务各自管理 Redis Key | 创建 `MemoryStore` 抽象接口，支持未来切换存储后端（如 PostgreSQL） | 中 |

### 低优先级（完善）

| # | 问题 | 建议 | 改动量 |
|---|------|------|--------|
| 9 | `EmbeddingService` 未被引用 | 移至 `deprecated/` 或删除 | 小 |
| 10 | `PlannerAgent` 初始化但未使用 | 配合 4.2 节真正启用，或暂时移除 | 中 |
| 11 | 上下文快照粗暴截断 | 实现渐进式压缩（先汇总，再裁剪，最后截断） | 中 |
| 12 | 部分异常用空 `pass` 吞掉 | 统一改为 `logger.warning(...)` + 返回降级默认值（`session_memory.py` 等） | 小 |
| 13 | 关键服务缺乏自动化测试 | 创建 `tests/unit/` 和 `tests/integration/` 测试套件，覆盖意图路由、任务执行、记忆管理 | 中 |

---

## 七、RAG 检索优化

> 详细的技术方案和代码示例见 [`fastapi-service/docs/rag-upgrade-roadmap.md`](../fastapi-service/docs/rag-upgrade-roadmap.md)

| 优化项 | 效果 | 改动量 | 建议时机 |
|--------|------|--------|----------|
| 中文分词（jieba） | BM25 对专业术语更准 | 小（`pip install jieba`） | 立即可做 |
| 知识库增量更新 | 重启不再全量重建 | 中 | 知识库文档较多时 |
| ContextualCompressionRetriever | 减少无关内容传给 LLM | 小（~10行） | 回答质量不够时 |
| 流式 RAG 输出 | 知识问答不再"卡住" | 中（改两个文件） | 用户体验优化时 |
| ParentDocumentRetriever | 检索更完整 | 大（重构分块逻辑） | 知识库内容较长时 |

---

## 八、未启动的 tasks.md 任务

### P1 — 建议尽快完成

| Task | 内容 | 预估 |
|------|------|------|
| 50-54 | 可观测性：Prometheus + Grafana + OpenTelemetry | 5-7 天 |
| 65 | 最终验收和交付 | 0.5 天 |

**50-54 特别说明**：系统已上线但缺乏监控，出问题时排查困难。建议优先完成 50.1-50.3（Prometheus 指标收集），Grafana 和 OpenTelemetry 可以延后。

### P2 — 按业务需要决定

| Task | 内容 | 建议 |
|------|------|------|
| 59-60 | 风险评估 Tool（项目进度/成本超支） | 需与产品确认需求再做 |
| 15.1-15.2 | SpringBoot 侧工具管理接口 | 目前直接管理 AI 服务即可，暂缓 |
| 13.4 | 网关层单元测试 | 有集成测试覆盖，可降低优先级 |

---

## 九、✅ 核心架构升级：Function Calling 改造（已完成，2026-03-31）

> **背景**：助手"不聪明"的根因不是某个工具的 Bug，而是意图识别架构本身。改造已完成，详见 `docs/changelog/2026-03-31.md`。

### 当前架构的三个瓶颈

| 瓶颈 | 现状 | 影响 |
|------|------|------|
| 两步分离 | 意图分类（qwen-flash）+ 参数提取（qwen-plus）分两次 LLM 调用 | 上下文割裂，参数经常提错或漏提 |
| System Prompt 过弱 | 仅3行通用描述，不含用户身份/工具 schema/默认行为 | LLM 不知道"查工时"默认查自己，不知道缺什么参数该追问 |
| 规则匹配臃肿 | 800+ 行关键词评分逻辑，与 LLM 结果互相冲突 | 维护困难，每加一个工具要改多处，且有时覆盖 LLM 的正确判断 |

### 改造方案：切换到 Function Calling

qwen-plus 支持 OpenAI 兼容的 `tools` 参数（函数调用），可以在 **一次 LLM 调用中同时完成意图识别 + 参数提取 + 缺参追问**。

```
改造前（两步拆分，容易出错）：
  用户: "查一下张三本周的工时"
    → LLM1(flash): 意图分类 → tool_execution / query_timesheet
    → LLM2(plus): 参数提取 → {start_date, member_name}
    → user_id 注入（3处逻辑各自为政）
    → 执行工具

改造后（一步到位）：
  用户: "查一下张三本周的工时"
    → LLM(plus) + tools=[query_timesheet, save_workhour, ...]
    → tool_call: query_timesheet(member_name="张三", start_date="2026-03-30", end_date="2026-03-31")
    → 执行工具

  用户: "查工时"（缺参数）
    → LLM(plus) 自动回复: "请问您要查哪个时间段的工时？本周还是本月？"
```

### 改造步骤

**第一步：增强 System Prompt（0.5 天）**

在 `system.yaml` 中注入：
- 当前用户身份（user_id、姓名、部门、角色）
- 默认行为规则（"查工时"默认查自己、"填工时"必须有项目/日期/时长）
- 可用工具列表及参数 schema

**第二步：实现 Function Calling 调用层（1 天）**

- 新增 `app/services/function_calling.py`，封装 qwen-plus 的 `tools` 参数调用
- 将 5 个工具的参数定义为 OpenAI function schema
- 处理 `tool_calls` 响应：有 tool_call → 执行工具；无 tool_call → 直接返回文本（闲聊/追问/知识问答）

**第三步：简化 LangGraph 流程（0.5 天）**

```
改造前节点：classify_intent → (execute_tool | execute_rag | execute_llm | clarify_node)
改造后节点：llm_with_tools → (execute_tool_call | return_text)
                                    ↓
                              tool_result → llm_summarize → return_text
```

- `classify_intent` 节点改为调用 Function Calling，不再单独分类
- RAG 作为一个"工具"注册（`search_knowledge`），由 LLM 自己决定是否调用
- 移除 `clarify_node`，缺参追问由 LLM 自然语言完成

**第四步：清理旧代码（0.5 天）**

- `intent_router.py` 的 800 行规则匹配降级为紧急 fallback（LLM 不可用时）
- 移除 `_classify_with_llm` + `_extract_parameters_with_llm` 两步流程
- `param_extract.yaml` 合并进 system prompt

### 预估工作量与收益

| 步骤 | 工作量 | 收益 |
|------|--------|------|
| 增强 System Prompt | 0.5 天 | 立即提升准确率，零风险 |
| Function Calling 调用层 | 1 天 | 消除两步分离问题，参数提取准确率大幅提升 |
| 简化 LangGraph | 0.5 天 | 代码量减少 500+ 行，新增工具只需写 schema |
| 清理旧代码 | 0.5 天 | 可维护性提升 |
| **合计** | **2-3 天** | **助手智能程度质的飞跃** |

### 改造后的工具扩展方式

```python
# 改造前：新增一个工具需要改 4 个文件
# 1. app/tools/xxx.py（写工具）
# 2. intent_router.py（加关键词、加 tools_desc）
# 3. intent_router.py（加参数提取 prompt）
# 4. langgraph_agent.py（可能需要改注入逻辑）

# 改造后：只需 1 个文件
# 1. app/tools/xxx.py（写工具 + 声明 function schema）
# tool_registry 自动收集 schema → LLM 自动选择调用
```

---

## 十、修订后的推荐执行顺序

```
✅ 已完成（L2：Tool Agent）
  ├── ✅ Function Calling 架构改造（2026-03-31）
  ├── ✅ 增强 System Prompt — 注入用户身份、默认行为、工具说明
  ├── ✅ 统一参数校验层 param_resolver.py（2026-04-01）
  └── ✅ Bug 修复：查工时全员数据 / 填工时项目名当ID

🔴 第一阶段（4.2 ~ 4.4）— 精度达标 + 业务补齐
  ├── 精度 v2 回归测试（已有改进：关键词扩展+描述消歧+System Prompt）
  ├── 分析 191 条 clarify 失败用例，调整测试期望或补 Few-shot
  ├── 目标：整体精度 85%+（不含 clarify 争议项达 90%+）
  ├── ✅ 工时审核 Tool — approve_workhour（2026-04-03）
  │   调用 POST /api/workhour/batch-approve，支持 deptAdmin+ 及项目负责人
  └── ✅ jieba 中文分词接入 BM25 + 补充知识库文档（2026-04-03）
      ├── langchain_rag.py: BM25Retriever preprocess_func=jieba
      ├── user_memory.py: _tokenize 换 jieba（含 ImportError 降级）
      └── knowledge-base/: 新增《工时审核流程》《假期与加班政策》

✅ 第二阶段（4.7 ~ 4.11）— L3：DeepSearch / 多步推理（已完成）
  ├── ✅ 激活 PlannerAgent + execution loop（2026-04-03）
  │   ├── node_llm_with_tools: ≥2 个 tool_calls → TaskPlan 并行执行
  │   ├── node_plan_and_execute: 路径A(multi_tool_calls直接执行)/路径B(PlannerAgent生成计划)
  │   ├── node_summarize: plan_results → LLM 综合分析 → 自然语言回答
  │   ├── AgentState: task_plan / plan_results 字段
  │   └── system.yaml: multi_tool_guidance 引导 LLM 一次调用多工具
  ├── ✅ export_report Tool — 工时报表导出（2026-04-07）
  │   调用 GET /api/workhour/export/project-simple，存 /tmp/workhour_exports/
  ├── ✅ knowledge_qa / export_report / approve_workhour few-shot 消歧（2026-04-07）
  └── ✅ Layer 3 集成测试（2026-04-07）
      fastapi-service/tests/test_layer3_integration.py，8 个场景，通过 8/8

✅ 第三阶段（4.8）— 稳定性 + 监控 + 体验（已完成）
  ├── ✅ 修复数据库密码硬编码（2026-04-08）
  │   config.py MYSQL_PASSWORD 默认值清空，强制 .env 注入
  ├── ✅ Prometheus 指标收集 + Grafana 看板（2026-04-08）
  │   /metrics 端点 + 11 项指标 + Grafana 8 面板看板
  │   埋点：chat.py / task_executor.py / llm_client.py / langchain_rag.py
  ├── ✅ 流式 RAG 输出（2026-04-08）
  │   stream_query() + langchain_rag_stream_query()
  │   langgraph_agent 检测 execute_rag 节点时拦截改为流式输出
  │   ⚠ 当前为"伪流式"（双次查询），后续可用 astream_events 彻底优化
  └── ⏳ vLLM 本地部署 — 待 GPU 服务器就绪后实施
      代码零改动，仅改 .env CHAT_LLM_API_BASE 指向本地推理服务

🔵 中长期（按需）— L4/L5 方向
  ├── MCP Server 接入（工具 > 10 个时，自动发现 SpringBoot 接口）
  ├── Multi-Agent 角色协作（等有明确的多角色业务场景再做，避免过度设计）
  ├── Code Interpreter Python 沙箱（SQL Agent 之后的进阶）
  ├── 自动任务执行（定时分析异常 + 通知，Autonomous Agent 方向）
  └── 记忆升级 Memory 2.0（用户画像 + 行为模式 + 偏好学习）
```

### AI 能力等级参照（来自 GPT 评审，结合项目实际调整）

```
L1  RAG 问答           ← 已完成
L2  Tool Agent         ← 已完成（Function Calling + 5 个工具 + RAG）
L3  DeepSearch Agent   ← 已完成（PlannerAgent 激活 + 多步规划 + 并行执行）
                       剩余：SQL Agent / 导出报表 Tool
L4  Multi-Agent        ← 中长期（等业务场景驱动，不提前做）
L5  Autonomous Agent   ← 远期（定时任务 + 自动执行 + 通知）
```

---

## 附：让 AI 更聪明的成本对比（修订版，2026-04-02）

| 手段 | 智能提升 | 开发成本 | 建议 |
|------|---------|---------|------|
| ~~Function Calling 改造~~ | ★★★★★ | ~~2-3天~~ | ✅ 已完成 |
| ~~增强 System Prompt~~ | ★★★★ | ~~0.5天~~ | ✅ 已完成 |
| ~~统一参数校验层~~ | ★★★ | ~~1天~~ | ✅ 已完成 |
| ~~工时审核 Tool~~ | ★★★ | ~~3h~~ | ✅ 已完成 |
| ~~jieba 接入 BM25~~ | ★★ | ~~1h~~ | ✅ 已完成 |
| ~~PlannerAgent + loop~~ | ★★★★★ | ~~已完成~~ | ✅ 已完成 |
| **精度调优（v2 回归）** | ★★★★ | ~~2天~~ | ✅ 已完成（87%精度） |
| **SQL Agent（DeepSearch）** | ★★★★★ | ~~1.5天~~ | ✅ 已完成 |
| **LLM 本地部署（vLLM）** | ★★★★ | 0.5-1天 | **🟢 第三阶段** |
| Self-Reflection | ★★★ | 0.5天 | 🟢 第三阶段 |
| MCP Server 批量接入 | ★★★★ | 1-2天 | 🔵 工具 > 10 个时 |
| Multi-Agent | ★★★ | 3-5天 | 🔵 等业务场景驱动 |
| Code Interpreter 沙箱 | ★★★★★ | 2-3天 | 🔵 SQL Agent 之后 |
