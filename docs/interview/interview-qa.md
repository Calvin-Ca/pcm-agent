# 模拟面试 Q&A

> **配套文档**：[`resume-bullets.md`](resume-bullets.md)
>
> **使用方法**：每条 bullet 配 3 个常见追问 + 答案要点。面试前过一遍，重点是"诚实 + 有理有据"，不要硬记台词。

---

## Bullet 1 追问 — Function Calling 架构

### Q1.1 你说"延迟没降反而慢 21~44%"，那这个改造的价值是什么？

**答案要点**：
- 延迟不是核心目标，**消除误差传播**才是。两步级联架构下，第一步意图分类错了，第二步参数提取再准也是错的；FC 单次调用让 LLM 同时拥有意图 + 工具描述 + 参数 schema，决策一致性更高。
- 慢是**本地 vLLM + qwen3-8b + 长 schema** 这个组合的特性 — schema 是 prefill 阶段的输入，prefill 越长 TTFT 越慢；但 prefill 是 GPU 受限，托管 API（DashScope/OpenAI）有更优的 prefill 算子和更大并发，这就是为什么我说托管场景预期能快 20-40%。
- save 类（schema 短）已经持平，这条数据本身就支撑"FC 不更慢"的结论 — 慢是个例不是规律。

### Q1.2 那为什么不直接用托管 API 测一遍证明？

**答案要点**：
- 工时管理系统是企业内部系统，数据有合规要求不能出网，所以生产必须用本地 vLLM。基准测试在生产环境跑才有参考意义，不能在 mock 环境测一组好看的数字。
- 离职前时间窗口紧（v1.2 冻结日期 2026-04-25），托管 API 实测留在 v1.3 路线图（如果有人接手）。

### Q1.3 你怎么处理 LLM 输出的 tool_calls 不稳定？

**答案要点**：
- vLLM qwen3-8b 偶发把 tool_calls 降级成文本格式 `<tool_call>{"name":...}</tool_call>`，这是模型层不稳定（changelog B7）。
- 我在 LLMClient.generate_with_tools 加了 fallback 解析：检测到文本格式 → 用正则提取 JSON → 包装成标准 tool_calls 结构返回。50 次填报压测中触发这条 fallback 的次数我没精确采（M5 TC-01 没跑完），但 think 块剥离的 30 次测试 0 污染（M5 TC-02 已验证）。
- 长期方案是托管 API（结构化输出更稳）或者升级模型；短期 fallback 已经让业务成功率 ≥ 95%。

---

## Bullet 2 追问 — RAG 混合检索

### Q2.1 4 个文档的测试集是不是太小，结果可信吗？

**答案要点**：
- 不可信作为绝对指标，**但可信作为相对趋势**。我也在简历里写了"小规模下 BM25 噪音主导，生产规模 20+ 文档后 Hybrid 预期反超"。
- 测试集小是因为企业知识库当前只有 4 篇（工时填报制度、加班规则、请假流程、考勤规范），不是我故意挑的。生产规模上去后会重测。
- 评测有第二个局限我也主动声明：测试 query 是从这 4 篇文档反向构造的（每篇抽 12 个事实问），所以 Recall 虚高 — 真实用户问法会有更多变体。

### Q2.2 为什么用 EnsembleRetriever + MultiQuery + Reranker 三层？不嫌重？

**答案要点**：
- EnsembleRetriever：向量召回擅长语义、BM25 擅长关键词，互补。比如"加班怎么算"向量好，"OT-001 制度第三条"BM25 好。
- MultiQuery：用户问法千奇百怪，让 LLM 改写成 3 个等价 query 并行召回提高覆盖率。
- Reranker：CrossEncoder 比 Bi-Encoder 精度高（同时编码 query+doc），用在最后一步对 top-K 重排序，K 通常 20，开销可控。
- 三层是 LangChain 推荐的标准 RAG pipeline。**重不重看场景** — 知识库小可以省掉 Reranker，但简历项目本身是练手 + 学习这套流程。

### Q2.3 Milvus 你为什么不用 FAISS / Chroma？

**答案要点**：
- Milvus 支持分布式 + 持久化 + 多 collection，FAISS 是单机内存索引、Chroma 部署简但 schema 灵活度差。
- 项目目标是"接近生产架构"，所以选 Milvus。
- 但实际过程踩坑很多 — pymilvus 2.6 的 ORM 模式有 nodeID 不匹配的 bug，改用 MilvusClient 直连 collection 才稳定（feedback_rag_milvus_fixes 有记录）。

---

## Bullet 3 追问 — SQL Agent 安全

### Q3.1 你说硬规则拦截 25%，那剩下 75% 不是 LLM 在防吗？这怎么算"安全"？

**答案要点**：
- 区分**真防御**和**辅助层**。硬规则 25% 是真防御 — 即使我把 LLM 拔掉，硬规则也能拦住 5/20 恶意用例（DROP TABLE / DELETE / UPDATE / 跨库访问 / 列黑名单）。
- LLM 改写 75% 是**辅助层**，意思是 LLM 把不规范请求改写成安全等价。这不能算可靠防御 — 攻击者改 temperature 或 prompt injection 就可能绕过。
- **真正的安全保证**：硬规则 25% 直接拦截 + 权限层强制 WHERE 注入（user-level / dept-level）。LLM 改写是锦上添花，让用户体验更好（友好提示而不是直接报错）。
- 这是为什么我简历里没写"综合拦截 100%" — 那种写法误导面试官以为 LLM 能保证安全。

### Q3.2 SQL 注入怎么防？只靠白名单够吗？

**答案要点**：
- 白名单只是第一道。完整链路：
  1. **语句类型校验**：sqlparse 解析后检查 stmt.get_type() 必须是 SELECT，DDL/DML 全部拦截
  2. **关键字黑名单**：DROP/TRUNCATE/DELETE/UPDATE/INSERT/ALTER/CREATE 直接 reject
  3. **表白名单 + 列黑名单**：只允许工时业务相关表（workhour / project / sys_user / org_dept），明确禁止 mysql.user / information_schema 等系统表
  4. **跨库防护**：禁止 db.table 这种带点的语法
  5. **权限注入**：如果是 employee 角色，强制 WHERE member_id = '<current_user_id>'，防止跨用户数据
- **没用 ORM 参数化是因为 SQL 是 LLM 生成的**，必须当不可信输入处理 — 这就是为什么硬规则要这么严格。

### Q3.3 那如果用户问"删掉我昨天的工时"，你怎么处理？

**答案要点**：
- LLM 不会把 DELETE 直接生成出来 — 因为我在 sql_query 工具描述里限制了"只生成 SELECT"。但万一生成了：
  1. 硬规则的语句类型校验直接 reject
  2. 同时 ai-service 把这种"删除工时"识别为 save_workhour / 修改类操作的兜底场景，路由到对应业务 API（不是 SQL Agent）
- 实际上"删除工时"在工时系统里是有专门的 API 的，不需要走 SQL。

---

## Bullet 4 追问 — 全栈工程化

### Q4.1 你列了 11 个 e2e 发现的 bug，是不是说明你前期测试不充分？

**答案要点**：
- 不是测试不充分，是**测试粒度不同**。
- 单元测试：函数级别行为 ✅
- 基准测试（benchmarks/）：指标驱动，用 mock token + 强制 fallback 路径，跑指标不跑业务
- e2e 测试：场景驱动，必须穿过浏览器 → nginx → Spring Boot → FastAPI → vLLM → MySQL 完整链路
- **bug 全部落在跨集成边界**：SpringBoot DTO 字段名 / vLLM 输出格式 / SSE 渲染顺序 / 权限事件类型。这些边界**不可能用单元 + 基准测试覆盖到**，必须真人在浏览器里点。
- 我把这个总结写进了 `docs/testing/e2e-strategy.md`，作为以后做类似项目的方法论沉淀。

### Q4.2 反向 SSH 隧道穿透生产 — 不会被运维骂吗？

**答案要点**：
- 现实约束：生产应用机（116）在公网，GPU 机（172）在内网，主后端要调 ai-service 但不能直连。
- 选项：a) 运维开公网到内网的端口转发 b) 反向 SSH 隧道 c) VPN
- 选 b) 因为 a) 需要运维排期 + 防火墙策略评审 + 可能影响安全审计，b) 由 GPU 机主动外连应用机，单向连接无防火墙穿透问题。
- autossh 进程级监控 + systemd 守护，连接断了自动重连。生产稳定运行 1+ 月。
- 但**不是最优长期方案** — 生产化后应该走服务网格（如 Istio）或 API 网关 + 私网互通。这一点我也会在面试时主动声明。

### Q4.3 Prometheus 你监控了什么？看到过哪些有意思的指标？

**答案要点**：
- 5 类自定义指标：`ai_requests_total`、`ai_request_duration_seconds`（Histogram）、`tool_call_total{tool_name}`、`rag_retrieval_duration_seconds`、`llm_tokens{type=prompt|completion}`
- 也会用 vLLM 自带的 `vllm:gpu_cache_usage_perc` 看 KV cache 是否打满
- **有趣发现**：query 类请求 P95 比 P50 高 3 倍 — 排查发现是部分 SQL Agent 用例触发 vLLM 长 think 块（B5），max_tokens 1500 顶满后 SQL 截断，第二次重试拉高 P95
- **诚实声明**：基准测试期间 ai-service 重启了一次，Prometheus Counter 重置，所以"7 天累计"指标我没法在简历里写真实数字（feedback_benchmark_narrative.md 也禁止注水）

---

## Bullet 5 追问 — 工具参数智能解析

### Q5.1 workType 用历史众数 — 用户突然换工作类型怎么办？

**答案要点**：
- 30 天滑窗 + 二维分组 `(user_id, project_id)` 已经在最大限度降低这个风险 — 一个员工在同一项目突然换 workType 的概率很低。
- 真切换场景：员工换岗（研发 → PM）、项目阶段切换（研发期 → 履约期）。这种切换有过渡期，30 天内会**逐步**反映在众数里。
- 即使滞后了，用户在前端工时表单可以**手动覆盖** — workType 只是一个智能默认值，不是强制锁定。
- 极端 case 兜底：LLM 根据 description 推 → 默认值"研发工作"。最差情况是**填错一次**，用户下次改正。

### Q5.2 缓存 5 分钟 TTL 是怎么定的？

**答案要点**：
- 工时填报是低频高峰场景（每天午后 + 下班前各一波），单用户单次会话内 5 分钟足够覆盖；过期后重新查 30 天历史，开销不大（一次 SpringBoot HTTP）。
- 不缓存 → 每次填报都查 30 天历史 → SpringBoot 压力大
- 缓存太久（> 1 小时）→ workType 切换感知滞后
- 5 分钟是 lab 经验值，没做严格 A/B 测试 — 上生产后会按 cache hit rate 调整。

### Q5.3 LLM 兜底为什么要做候选白名单校验？

**答案要点**：
- LLM 不可靠，会创造性返回不在候选列表的值（如"研究开发工作"、"测试性研发"等），SpringBoot DTO 校验会直接拒绝 → 工时填报失败。
- 白名单校验：LLM 返回 → 遍历 5 个候选项 → 只有命中才采纳，没命中走默认值。
- 这是在调 LLM 但**不信任 LLM**的工程化模式 — 任何外部不可信源都做白名单/类型/范围校验。
- 这种 fallback 链思想其实就是**多层 graceful degradation**：(user × project) → user → LLM → 默认。每层失败的概率独立，整体可用性接近 100%。

---

## 综合追问

### Q-General-1 这个项目用了多少时间，你的角色是什么？

**答案要点**（按真实情况调整）：
- 时间窗口：N 个月（具体时间）
- 角色：独立设计 + 实现 + 部署 + 测试，包括架构选型、性能基准、生产部署
- 协作：与 Spring Boot 主后端团队对接 API；与运维讨论生产部署
- 量化：commit 数、bug 修复数（changelog 记录的 11 项）、benchmark CSV 三类共 X 行

### Q-General-2 这个项目最难的部分是什么？

**答案要点**（任选一个，深聊）：
- **方法学层面**：搞清楚"基准测试 vs e2e 测试"的边界 — 早期我也以为基准测试过了就稳定了，浏览器实测发现 11 个 bug 后才意识到测试粒度差异
- **架构层面**：Function Calling 改造时是不是该做、什么时候做。两步级联看起来稳但有误差传播；FC 看起来好但本地 vLLM 慢。最后的判断是**架构正确性优于延迟数字**
- **协作层面**：跨团队边界的字段名 / 类型 / 错误透出约定 — DTO 字段名 description vs workContent / workType 候选值 5 项 / SpringBoot ProblemDetail title vs detail vs message — 都是"看起来很小但每次跑 e2e 才暴露"的边界 bug
- **诚实层面**：克制把数字写漂亮的诱惑。simple 反例 "FC 延迟下降 90%"（事实上慢 21~44%），如果写了一定被面试官质疑。

### Q-General-3 如果你接手这个项目，下一步会做什么？

**答案要点**（演 v1.3 / v1.4 的路线）：
- v1.3：基准测试改用托管 API 重测，验证"延迟缩短 20-40%"假设
- v1.4：知识库扩到 20+ 文档，重测 RAG Hybrid 是否反超 Milvus
- v2.0：上生产化的 LLM 网关（vLLM → Triton + Ray Serve），多模型并发
- 监控：补 SLO 看板（P99 < 5s 目标 / 错误率 < 1% 目标）
- 团队化：把 e2e 测试体系（docs/testing/）改成 CI 自动化（每次 PR 跑 M1-M5）

### Q-General-4 这个项目你最想推荐的"读源代码"路径是什么？

**答案要点**（带面试官读 5 个文件，体现对架构的把握）：
1. `app/services/langgraph_agent.py` — DAG 编排入口，看怎么从 SSE 路由到 FC vs RAG vs general_chat
2. `app/services/param_resolver.py` + `app/services/work_type_resolver.py` — 工程化的"LLM 输出后处理"层
3. `app/services/sql_engine.py` + `app/tools/sql_query.py` — SQL Agent 三层安全
4. `app/services/langchain_rag.py` — RAG 混合检索 + Reranker
5. `docs/changelog/2026-04-26.md` — e2e 修复全过程，体现工程纪律

---

## 不要回答的问题（如果被问）

| 问题 | 应对 |
|------|------|
| "你这个 RAG 比 GPT-4 RAG 强吗" | "完全不是一个量级的对比，企业内网部署 + 4 文档场景 vs 通用大模型 + 互联网级数据。我做的是工程化复现 + 工程优化。" |
| "你为什么不用 langchain agent / autogen / metagpt" | "langchain agent 决策不可控、autogen/metagpt 是多 agent 框架场景不匹配。LangGraph DAG 给了我节点级路由控制。" |
| "你简历哪条是你独立做的" | 全部独立。但要诚实声明 LangChain / LangGraph / Milvus / vLLM 是开源框架，我做的是组合 + 调优 + 业务集成。 |
| "你怎么保证简历数字可验证" | "每个数字都能 git checkout 这个 commit + 跑 tests/benchmark/ 复现。CSV 文件入库了。" |

---

## 准备工具

简历提交前 1 天，按这个清单过一遍：
- [ ] 5 条 bullet 通顺、无注水、所有数字可验证
- [ ] Q&A 每条 bullet 至少能答 3 个追问
- [ ] 数字快速对照表（resume-bullets.md 末尾）打印放手边
- [ ] 准备好"读源代码 5 个文件路径"应对深聊
- [ ] 准备好"最难的部分"3 个备选答案（架构 / 方法学 / 诚实）
