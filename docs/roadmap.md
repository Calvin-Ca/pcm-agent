# AI 智能助手 — 升级路线与优化建议

> 更新日期：2026-04-23
> 当前版本：1.2（已完成 L1~L3 全部 + 稳定性 + vLLM 部署 + SQL Agent，准备上线）

---

## ⚠️ 待办（P0，2026-05-10 前完成）

- **简历指标基准测试**：RAG Recall@K / Function Calling 延迟对比 / SQL Agent 准确率等 5 项基准测试，用于补齐个人简历可核验指标。详见 `docs/benchmarks/tasks-2026-04.md`

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

## 二、已知 Bug（均已修复，2026-04-01）

三个历史 Bug（查工时返回全员数据、填工时把项目名当项目 ID、参数处理逻辑分散）已于 2026-04-01 全部修复。修复方式是新增 `app/services/param_resolver.py` 统一参数解析层，并在各工具中接入。详见 `docs/changelog/2026-04-01.md`。

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

### 3.1 ✅ 工时审核（已完成，2026-04-03）

新增 `approve_workhour` Tool，调用 `POST /api/workhour/batch-approve`，支持 deptAdmin+ 及项目负责人操作。

---

### 3.2 ✅ 导出报表（已完成，2026-04-07）

新增 `export_report` Tool，调用 `GET /api/workhour/export/project-simple`，导出文件存 `/tmp/workhour_exports/`。

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

### 4.2 ✅ 复杂多步骤任务（PlannerAgent 已激活，2026-04-03）

`node_plan_and_execute` 已接入 LangGraph：LLM 返回 2 个以上 tool_calls 时并行执行，汇总后由 `node_summarize` 综合回答。支持"查多项目工时并对比"等复杂场景。

---

### 4.3 ✅ SQL Agent（已完成，2026-04-13）/ Code Interpreter（上线后迭代）

`sql_query` Tool 已上线：自然语言转 SQL，经三层安全控制（只读账号 + 应用层白名单 + 权限约束注入）后直接查询数据库并返回汇总结果。支持现有 Tool 无法覆盖的自定义分析场景。

Code Interpreter Python 沙箱（让 LLM 生成 Python 代码处理复杂数据）列入上线后迭代，在 SQL Agent 基础上进一步扩展。

---

## 六、技术债与优化项

### 5.4 ✅ 统一参数校验层（已完成，2026-04-01）

`app/services/param_resolver.py` 已实现，统一处理项目名→ID、成员名→ID，含进程级缓存。各工具已接入，详见第二节 Bug 修复说明。

---

### 原有技术债与优化项

按优先级整理，建议在每次迭代中穿插处理：

### 高优先级（影响稳定性）

| # | 问题 | 建议 | 改动量 |
|---|------|------|--------|
| 1 | ✅ 数据库密码硬编码在 `config.py`（已修复，2026-04-08） | `MYSQL_PASSWORD` 默认值已清空，强制 `.env` 注入 | 小 |
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
| 10 | ✅ `PlannerAgent` 已启用（2026-04-03） | node_plan_and_execute 已接入 LangGraph | — |
| 11 | 上下文快照粗暴截断 | 实现渐进式压缩（先汇总，再裁剪，最后截断） | 中 |
| 12 | 部分异常用空 `pass` 吞掉 | 统一改为 `logger.warning(...)` + 返回降级默认值（`session_memory.py` 等） | 小 |
| 13 | 关键服务缺乏自动化测试 | 创建 `tests/unit/` 和 `tests/integration/` 测试套件，覆盖意图路由、任务执行、记忆管理 | 中 |

---

## 七、RAG 检索优化

> 详细的技术方案和代码示例见 [`fastapi-service/docs/rag-upgrade-roadmap.md`](../fastapi-service/docs/rag-upgrade-roadmap.md)

| 优化项 | 效果 | 改动量 | 建议时机 |
|--------|------|--------|----------|
| ✅ 中文分词（jieba，已完成，2026-04-03） | BM25 对专业术语更准 | — | — |
| 知识库增量更新 | 重启不再全量重建 | 中 | 知识库文档较多时 |
| ContextualCompressionRetriever | 减少无关内容传给 LLM | 小（~10行） | 回答质量不够时 |
| ✅ 流式 RAG 输出（已完成，2026-04-08） | 知识问答不再"卡住"（当前为伪流式，后续可用 astream_events 优化） | — | — |
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

原架构将意图分类（qwen-flash）和参数提取（qwen-plus）拆成两次 LLM 调用，导致上下文割裂、参数提取不稳定。改造后切换为 qwen-plus + OpenAI 兼容 `tools` 参数，一次调用同时完成意图识别、参数提取和缺参追问。

关键架构决策：
- `IntentRouter` 的 800 行规则匹配降级为 LLM 不可用时的 fallback，不再参与主流程
- RAG 作为一个工具由 LLM 自行决定是否调用，移除独立的 `classify_intent` 节点
- 新增工具只需声明 function schema，`tool_registry` 自动收集，LLM 自动发现

详见 `docs/changelog/2026-03-31.md`。

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

✅ 第三阶段（4.8 ~ 4.13）— 稳定性 + 监控 + 体验 + SQL Agent（已完成）
  ├── ✅ 修复数据库密码硬编码（2026-04-08）
  │   config.py MYSQL_PASSWORD 默认值清空，强制 .env 注入
  ├── ✅ Prometheus 指标收集 + Grafana 看板（2026-04-08）
  │   /metrics 端点 + 11 项指标 + Grafana 8 面板看板
  │   埋点：chat.py / task_executor.py / llm_client.py / langchain_rag.py
  ├── ✅ 流式 RAG 输出（2026-04-08）
  │   stream_query() + langchain_rag_stream_query()
  │   langgraph_agent 检测 execute_rag 节点时拦截改为流式输出
  │   ⚠ 当前为"伪流式"（双次查询），后续可用 astream_events 彻底优化
  ├── ✅ vLLM 本地部署（2026-04-12）
  │   GPU 服务器 172.19.3.136:8099 部署 qwen3-8b
  │   CHAT_LLM_API_BASE / SQL_AGENT_LLM_API_BASE 均指向 vLLM
  ├── ✅ SQL Agent 上线（2026-04-13）
  │   sql_query 工具：自然语言→SQL→执行→汇总
  │   三层安全：只读账号 + 应用层校验 + 权限约束注入
  │   动态表选择 + 紧凑 schema，适配 qwen3-8b 小模型
  ├── ✅ compute_statistics auth_token 修复（2026-04-13）
  │   补齐 Authorization header 透传，修复 Spring Boot 401
  └── ✅ 精度回归验证（2026-04-13）
      layer1_v6: 87.0%（2000 case），与 v5 持平，无回归

🟡 上线准备（4.14 ~）
  ├── ✅ 创建只读数据库账号（2026-04-14）
  │   账号 read_only_ai，.env SQL_AGENT_DB_* 已配置
  ├── ⬜ 生产环境部署（ai-service 部署到 116 服务器）
  ├── ⬜ 生产 .env 配置确认（LLM/DB/Redis/Milvus 地址）
  └── ⬜ 上线后观察：Grafana 看板监控 + 日志排查

🔵 上线后持续迭代
  ├── ec 类别精度提升（当前 61%，不阻塞上线）
  ├── Self-Reflection（回答质量自校验，0.5天）
  ├── SQL Agent 精度测试集（量化 SQL 生成质量）
  ├── 查询结果可视化（ECharts 图表）
  ├── 技术债清理（工具注册校验、工具调用重试、Tool 基类提取）
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
L3  DeepSearch Agent   ← 已完成（PlannerAgent + SQL Agent + 导出报表）
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
| **LLM 本地部署（vLLM）** | ★★★★ | ~~0.5-1天~~ | ✅ 已完成（vLLM qwen3-8b） |
| Self-Reflection | ★★★ | 0.5天 | 🔵 上线后迭代 |
| MCP Server 批量接入 | ★★★★ | 1-2天 | 🔵 工具 > 10 个时 |
| Multi-Agent | ★★★ | 3-5天 | 🔵 等业务场景驱动 |
| Code Interpreter 沙箱 | ★★★★★ | 2-3天 | 🔵 上线后迭代 |
