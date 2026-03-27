# AI 智能助手 — 升级路线与优化建议

> 更新日期：2026-03-26
> 当前版本：1.0（已完成 MVP + Memory + RAG + 审计日志）

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
| LangGraph 编排 | ✅ | classify_intent → execute_tool/rag/llm/clarify |

---

## 二、AI 能力扩展策略

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

## 三、近期可落地的能力扩展

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

## 四、中期升级方向

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

## 五、技术债与优化项（来自 optimization-todo.md）

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

### 低优先级（完善）

| # | 问题 | 建议 | 改动量 |
|---|------|------|--------|
| 9 | `EmbeddingService` 未被引用 | 移至 `deprecated/` 或删除 | 小 |
| 10 | `PlannerAgent` 初始化但未使用 | 配合 4.2 节真正启用，或暂时移除 | 中 |
| 11 | 上下文快照粗暴截断 | 实现渐进式压缩（先汇总，再裁剪，最后截断） | 中 |

---

## 六、RAG 检索优化（来自 rag-upgrade-roadmap.md）

| 优化项 | 效果 | 改动量 | 建议时机 |
|--------|------|--------|----------|
| 中文分词（jieba） | BM25 对专业术语更准 | 小（`pip install jieba`） | 立即可做 |
| 知识库增量更新 | 重启不再全量重建 | 中 | 知识库文档较多时 |
| ContextualCompressionRetriever | 减少无关内容传给 LLM | 小（~10行） | 回答质量不够时 |
| 流式 RAG 输出 | 知识问答不再"卡住" | 中（改两个文件） | 用户体验优化时 |
| ParentDocumentRetriever | 检索更完整 | 大（重构分块逻辑） | 知识库内容较长时 |

---

## 七、未启动的 tasks.md 任务

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

## 八、推荐执行顺序

```
立即可做（本周）
  ├── 修复数据库密码硬编码（安全风险，30分钟）
  ├── 补充知识库文档（考勤/审核制度，不写代码）
  └── jieba 中文分词接入 BM25（1小时）

短期（1-2周）
  ├── 工时审核 Tool（3-4小时）
  ├── 导出报表 Tool（2-3小时）
  └── Prometheus 指标收集（Task 50.1-50.3，3小时）

中期（1个月内）
  ├── MCP Server 接入（1-2天，如接口增多）
  ├── 激活 PlannerAgent 支持多步骤任务（1天）
  └── 工具重试机制 + Tool 基类重构（代码质量）

长期（按需）
  ├── Code Interpreter / SQL 分析（2-3天）
  ├── Grafana Dashboard + 告警（Task 51-53）
  └── 流式 RAG 输出（体验优化）
```

---

## 附：让 AI 更聪明的成本对比

| 手段 | 智能提升效果 | 开发成本 | 运行成本 |
|------|-------------|----------|----------|
| 完善知识库文档 | ★★★ 制度问答更准 | 极低（加文档） | 无 |
| 优化 Prompt 模板 | ★★★ 意图识别更准 | 低（改 YAML） | 无 |
| 增加对话历史轮数 | ★★ 上下文更连贯 | 低 | 低（Redis） |
| 接入更多 Tool | ★★★ 能做更多事 | 中（每个Tool 2-4小时） | 低 |
| MCP Server 批量接入 | ★★★★ 能力快速扩展 | 中（一次性） | 低 |
| 升级更强 LLM 模型 | ★★★★ 整体理解力提升 | 极低（改配置） | 高（API费用） |
| 激活 PlannerAgent | ★★★ 支持复杂多步骤 | 中 | 中 |
| Code Interpreter | ★★★★★ 自定义分析 | 高 | 中 |
