# 简历 Bullet 草稿（v3）

> **数据冻结**：v1.2 数字源自 `docs/benchmarks/report-2026-04-25-final.md`（2026-04-25）+ `docs/changelog/2026-04-26.md`（端到端修复）；Bullet 6 数字源自 `docs/benchmarks/progressive_rag_report_2026-05-05.md`（A-RAG 3 模型对照，108 次评测）
>
> **叙事约束**：严格遵守 `.claude/memory/feedback_benchmark_narrative.md` — 不写"延迟下降 X%"、不写"拦截率 100%"、不写"+30% 覆盖率 / +1.5 分"等本地实测不成立的数字
>
> **目标场景**：投递 AI 应用 / 大模型工程 / 后端开发岗，强调"自然语言 → 工具调用"全链路工程能力

---

## 项目标题

**工时管理系统 AI 助手服务（FastAPI + LangGraph + vLLM + Milvus）**

---

## Bullet 1 — Function Calling 架构改造

**架构演进**：将"意图分类 → 参数提取"两步级联 LLM 调用重构为单次 Function Calling，消除分类边界处的误差传播；本地 vLLM + qwen3-8b 环境下 50 条用例对比测试显示，工时填报场景 P50 持平（+0.7%），数据查询/RAG 场景受长 schema prefill 影响 P50 慢 21~44%；托管 API（DashScope）场景因免去一次网络往返，理论可缩短 20~40%。

**关键技术栈**：LangGraph DAG / OpenAI Function Calling 协议 / qwen3-8b tool_call_parser

**实证支撑**：
- 测试集：50 条 query × 2 模式 × 1~5 次重复（CSV：`tests/benchmark/results/latency_full_20260424_rerun.csv`）
- 修正后报告：`docs/benchmarks/review-2026-04-25-corrected.md`

> ⚠️ 不要在简历正文写"延迟下降 X%"。被问"那你的延迟优化优势是什么"，答："架构价值在消除两步级联的误差传播 — 第一步意图错了第二步参数全错。延迟方面 save 类持平，托管 API 场景因省去一次网络往返预期 20-40% 改善，但本地 vLLM 因 schema prefill 反而略慢，这是一个真实权衡。"

---

## Bullet 2 — RAG 混合检索 + 重排序

**检索流程**：LangChain EnsembleRetriever 融合 Milvus 向量召回（DashScope text-embedding-v2，维度 1536）与 BM25 关键词召回，前置 MultiQueryRetriever 自动改写查询，后置 CrossEncoder 重排序；4 文档企业知识库小规模评测下 Recall@5 为 Milvus 100% / Hybrid 98%（小规模下 BM25 噪音主导，**生产规模 20+ 文档后 Hybrid 预期反超**），MRR 优于 BM25 单路。

**关键技术栈**：LangChain / pymilvus 2.6 / DashScope Embedding / BAAI/bge-reranker-base

**实证支撑**：
- 评测集：50 条问题，由 4 篇知识库文档反向构造（CSV：`tests/benchmark/results/rag_recall_<date>.csv`）
- 评测局限：测试集与文档同源 → Recall 虚高，故面试时主动声明

> ⚠️ Hybrid 98% < Milvus 100% **必须加解释**。面试官追问"你这个 Hybrid 是不是有 bug"，答："不是 bug，是测试集规模问题。4 文档下 BM25 关键词召回引入的噪音超过它带来的关键词精确匹配收益，所以略低于纯向量。理论上 20+ 文档后向量模型的语义召回上限会让 Hybrid 反超。生产环境会持续监控这条曲线。"

---

## Bullet 3 — SQL Agent 自然语言查询 + 分层安全

**功能链路**：用户自然语言 → LLM 生成 SQL（基于表 schema + 业务规则模板）→ **三层安全校验** → MySQL 执行 → LLM 摘要回写。

**三层安全设计**（必须拆开说，不写"综合 100%"）：
1. **硬规则层**（真防御）— 语句类型白名单（仅 SELECT）、危险关键字黑名单（DROP/TRUNCATE/DELETE/UPDATE）、表/列白名单、用户级 WHERE 条件强制注入；20 条恶意用例评测拦截率 **5/20 = 25%**（直接拦截）
2. **LLM 语义改写层**（辅助）— 检测到不规范请求时改写为安全等价（如 `DELETE FROM workhour` → `SELECT * FROM workhour LIMIT 100`）；改写率 **15/20 = 75%**
3. **权限校验层** — Spring Boot 网关注入 `X-Entity-Type`，FastAPI PermissionValidator 在工具调用前按角色（employee/deptAdmin/regionAdmin/companyAdmin/superAdmin）校验

**关键技术栈**：sqlparse / Pydantic / 自定义 SecurityValidator

**实证支撑**：CSV `tests/benchmark/results/sql_security_<date>.csv` + `docs/benchmarks/review-2026-04-25-corrected.md`

> ⚠️ 不写"综合拦截率 100%"。LLM 改写不是真安全（换 temperature / prompt injection 可能漏），必须区分。面试官追问"你怎么保证 LLM 改写一定生效"，答："不能保证 — LLM 改写是辅助层。真安全防御是硬规则的 25% 拦截 + 强制 WHERE 注入。如果改写失效，硬规则兜底；如果硬规则也漏了，权限层兜底。三层独立。"

---

## Bullet 4 — 全栈工程化与端到端可靠性

**部署拓扑**：
- 应用机（116）：Spring Boot 主后端 + nginx + 反向 SSH 隧道
- GPU 机（172）：vLLM + qwen3-8b（hermes parser）+ ai-service 容器（FastAPI）+ Milvus + Redis + Prometheus + Grafana
- 数据库（192.168.0.94）：MySQL 8 内网

**Docker Compose 编排** 7 个服务，autossh 反向隧道穿透公网应用机到内网 GPU 机；Prometheus 暴露 LLM 调用次数/延迟/RAG 检索/工具命中等 5 类指标，Grafana 看板做生产可观测。

**端到端可靠性**：浏览器实测发现并修复 11 项跨集成边界缺陷（详见 `docs/changelog/2026-04-26.md` B1-B11），覆盖：SpringBoot DTO 字段名一致性 / SQL 模板字段值约定（is_work_day='1' vs 'Y'）/ vLLM `<think>` 块剥离 + max_tokens 提升 / FC tool_calls 文本降级 fallback 解析 / 权限拒绝事件 SSE 类型透传 / SSE 摘要 vs 原始 JSON 渲染优先级。

**关键技术栈**：FastAPI / Docker Compose / vLLM / autossh / Prometheus + Grafana

> ⚠️ 不要把 11 个 bug 全列在 bullet 里 — 简历列代表性 3-4 类，面试再展开。被问"为什么这么多 bug 在 e2e 才发现"，答："基准测试是指标驱动（FC 延迟 / RAG 召回 / SQL 拦截率），用 mock token 和强制 fallback 路径，不会触发跨集成边界。e2e 是用户场景闭环测试，必须穿过浏览器→nginx→Spring Boot→FastAPI→vLLM→MySQL 完整链路，每个边界都是 bug 滋生地。这是测试方法学的差异，不是质量问题。"

---

## Bullet 5 — 工具参数智能解析层

**问题**：LLM Function Calling 拿到的参数往往是用户口语化输入（"预管理系统" / "李四" / "本周"），与后端业务系统的 ID/枚举/日期范围不直接兼容；同时 LLM 输出的 workType 等枚举字段写死会因部门/项目差异导致 SpringBoot 校验失败。

**解决方案**：
- **`param_resolver`** — 项目名 → ID、成员名 → ID 解析层，进程级 LRU 缓存
- **`work_type_resolver`** — workType 智能识别（候选 5 项：研发工作 / 商务工作 / 综合管理工作 / 履约工作 / 需求工作）
  - 主路径：`(user_id, project_id)` 二维分组，最近 30 天历史填报取众数
  - 兜底 1：`user_id` 单维历史众数
  - 兜底 2：LLM 从候选白名单选 1（无历史的新员工首次填报）
  - 兜底 3：默认值
  - cachetools.TTLCache 双级缓存，TTL 5 分钟

**关键技术栈**：cachetools / 自定义 fallback chain / SpringBoot REST 接入

**实证支撑**：`fastapi-service/app/services/work_type_resolver.py` + 端到端验证（adf3a11 / 08cd53b）

---

## Bullet 6 — Agentic RAG（A-RAG）渐进式披露 + 跨模型对照

**架构演进**：把 one-shot RAG 重构为 4 层检索工具（`kb_outline` / `kb_keyword_search` / `kb_semantic_search` / `kb_read_section`），LangGraph 承载 agent loop，LLM 多轮自主调用；带 `max_iterations=5` + 重复 tool_call 检测 + 连续错误熔断，3 道防死循环闸。

**跨模型对照评测**（同一评测集 18 query × 2 模式 × 3 模型 = **108 次**，进度可断点续跑）：

| 模型 | multi_hop 覆盖率 | progressive 整体 cov | tool_calls 行为 |
|------|----------------|---------------------|----------------|
| qwen3.5-plus（DashScope） | **100%** | 83% | 0/5 步各 7 条，会判断 |
| qwen3-8b（本地 vLLM） | 43% | 44% | 退化 44% / 单步 33% / 完整 22% |
| qwen-flash（DashScope） | 36% | 25% | 撞 max_iterations 触发率 **83%**（R3 风险落地实测）|

**关键发现**：3 模型形成"太保守 / 刚刚好 / 太激进"工具选择能力光谱，**定量证明 A-RAG 的瓶颈在 LLM tool-calling 控制能力，不在框架设计**；为 v1.5 模型选型 / Reranker / RAG fallback 策略提供基线数据。

**人工评分**（qwen3-8b 36/36 完成）：oneshot 7.35 / progressive 5.11，但 metadata_filter 类目 progressive 反超 +2.0 分（outline 工具的设计价值在元数据过滤场景显现）。

**关键技术栈**：LangGraph StateGraph 条件边循环 / 自定义 `KnowledgeNavigator`（outline + 章节索引 + 二次召回）/ DashScope OpenAI 兼容协议跨模型 swap

**配套工程**：知识库扩容到 **100+ 篇 / 7 主题域 / 4 格式**（md / docx / pdf / csv），通过 4 个文档生成 agent 并行批产 + Phase 1 整合脚本（`split_output.py` 带 frontmatter 校验 + 字数下限 + category 白名单）

**实证支撑**：
- 评测集：`fastapi-service/tests/benchmark/data/progressive_rag_eval_18.jsonl`（4 类：simple / multi_hop / metadata_filter / compare）
- 3 模型 CSV：`tests/benchmark/results/progressive_rag_2026-05-05_{qwen3-8b, qwen3.5-plus, qwen-flash}.csv`
- 完整报告：`docs/benchmarks/progressive_rag_report_2026-05-05.md`（480 行，§6.5 跨模型对照表完整）
- 设计文档：`docs/rag-progressive-disclosure-design.md`（A-RAG 论文落地 + 4 工具设计 + 评测协议）

> ⚠️ **不写"+30% 覆盖率 / +1.5 分"** — 这是设计 doc 的预期，本地 8B 实测反向落后 39pp；qwen3.5-plus 上 multi_hop 才 +57pp。被问"为什么数据 8B 反向你还做"，答："这正是工程价值——预设目标和实测数据的差就是 LLM 工具选择能力的量化指标。同一份代码切到 plus 上 multi_hop 100%，证明设计正确。flash 撞顶 83% 又证明了我们设计的死循环闸有用。"

---

## 自检清单（提交简历前过一遍）

- [ ] Bullet 1 没有"延迟下降 X%"
- [ ] Bullet 2 RAG 98% 旁边有"小规模下 BM25 噪音主导"解释
- [ ] Bullet 3 没有"综合拦截率 100%"，硬规则 / LLM 改写 / 权限三层分别拆开
- [ ] Bullet 4 不列全部 11 个 bug，仅列 3-4 类代表
- [ ] Bullet 5 workType 候选值 5 项准确，兜底 chain 完整
- [ ] Bullet 6 没有写"+30% 覆盖率 / +1.5 分"等本地 8B 不成立的虚数字；3 模型对照表硬数字（100% / 43% / 83%）准确
- [ ] 所有数字可在 git 仓库 CSV 或 docs 中追溯
- [ ] 没有写"修复 100% bug"、"性能提升 5x"等绝对句

---

## 数字快速对照表（面试时随手翻）

| 指标 | 数字 | 来源 |
|------|------|------|
| FC save 类 P50 | +0.7%（持平）| latency_full_20260424_rerun.csv |
| FC query 类 P50 | -37~44%（本地 vLLM） | 同上 |
| FC kb 类 P50 | -21% | 同上 |
| RAG Recall@5 Milvus | 100% | rag_recall CSV |
| RAG Recall@5 Hybrid | 98% | 同上 |
| SQL 硬规则拦截 | 5/20 = 25% | sql_security CSV |
| SQL LLM 改写 | 15/20 = 75% | 同上 |
| e3db51a 路由命中 | 5/5（能力匹配） | report-2026-04-25-final §10 |
| 端到端修复 bug | 11 项（B1-B11） | changelog/2026-04-26.md |
| workType 候选 | 5 项 | work_type_resolver.py |
| A-RAG 评测总规模 | 108 次（18×2×3 模型） | progressive_rag_report_2026-05-05.md |
| qwen3.5-plus multi_hop cov | 100% | progressive_rag_2026-05-05_qwen3.5-plus.csv |
| qwen3-8b multi_hop cov | 43% | progressive_rag_2026-05-05_qwen3-8b.csv |
| qwen3-8b 完整循环触发率 | 22%（4/18） | 同上 |
| qwen3-8b 退化率 | 44%（8/18） | 同上 |
| qwen-flash 撞顶率 | 83%（15/18） | progressive_rag_2026-05-05_qwen-flash.csv |
| 知识库规模 | 100+ 篇 / 7 主题域 / 4 格式 | knowledge-base/ + report 表头 |
| qwen3-8b 人工评分（35/36） | oneshot 7.35 / progressive 5.11 | progressive_rag_2026-05-05_qwen3-8b.csv |
