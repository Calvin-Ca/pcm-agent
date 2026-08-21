# Benchmarks 报告导读

本目录共有 10 个 Markdown 文档，其中 8 个属于测试或诊断报告，另外 2 个是任务清单和前置评审。

| 文件 | 主要测试内容 |
|---|---|
| [tasks-2026-04.md](tasks-2026-04.md) | 基准测试任务规划，不是结果报告。规划了 RAG Recall、Function Calling 延迟、SQL Agent 准确性与安全、vLLM 吞吐和 Grafana 生产指标五类测试。 |
| [review-2026-04-24.md](review-2026-04-24.md) | 对第一版 Function Calling 延迟测试的评审。发现“两次 LLM”和 Function Calling 没有走相同链路，测试不公平，因此要求返工。 |
| [report-2026-04-24-v2.md](report-2026-04-24-v2.md) | 公平比较旧架构“两次 LLM 调用”和新架构“单次 Function Calling”的端到端延迟。覆盖工时查询、填报、知识问答、SQL 四类共 50 条请求。 |
| [report-2026-04-24-v3.md](report-2026-04-24-v3.md) | 在 v2 基础上增加 vLLM 工具解析器验证，重点比较 `qwen3_xml` 与 `hermes` parser 对工具调用成功率的影响，并重跑 Function Calling 延迟测试。 |
| [review-2026-04-25-corrected.md](review-2026-04-25-corrected.md) | 对 Function Calling 延迟数据进行纠错：剔除无法公平比较的 SQL 样本、标注误路由等异常，重新计算 query、save、kb 各类延迟；同时复盘 SQL 正例失败和安全校验问题。 |
| [report-2026-04-25-final.md](report-2026-04-25-final.md) | 4 月基准测试综合报告：① Milvus、BM25、Hybrid 的 RAG Recall@5/10 和 MRR 消融；② Function Calling 与两次 LLM 的延迟；③ SQL Agent 的生成准确性和安全校验；④ Grafana 最近 7 天生产运行数据及生产链路验证。部分 Function Calling 统计应以 corrected 报告为准。 |
| [progressive_rag_report_2026-05-05.md](progressive_rag_report_2026-05-05.md) | 比较传统一次性 RAG 与 Progressive/A-RAG。用 18 条问题测试简单问答、多跳检索、元数据过滤和跨文档比较，并对比 qwen3-8b、qwen3.5-plus、qwen-flash 的工具选择、多步检索、文档覆盖率、Token 和延迟。 |
| [llm-bakeoff-report-2026-05-15.md](llm-bakeoff-report-2026-05-15.md) | GPU1 上的模型性能选型测试。比较 Qwen3-8B BF16、8B FP8、14B AWQ、35B MoE，测量 TTFT、总耗时、生成 TPS、Token 数和显存占用。 |
| [llm-bakeoff-r2-diagnosis-2026-05-16.md](llm-bakeoff-r2-diagnosis-2026-05-16.md) | 专门诊断 14B-AWQ 为什么比 8B 慢约 6.3 倍。通过同权重切换 AWQ GEMM/Marlin kernel，并加入 14B-FP8 对照，确认性能瓶颈是否来自量化 kernel。 |
| [llm-quality-bakeoff-2026-05-16.md](llm-quality-bakeoff-2026-05-16.md) | 模型业务质量对照。比较 14B-AWQ-Marlin、8B-BF16 和云端 qwen3.5-plus 在 A-RAG 中的工具导航、多步检索、文档覆盖、答案质量、Token 与延迟，判断 14B 是否解决 8B 的工具选择退化问题。 |

## 测试主线

1. **RAG 检索效果**：`report-2026-04-25-final.md`
2. **Function Calling 架构延迟**：`review-2026-04-24.md` → `report-2026-04-24-v2.md` → `report-2026-04-24-v3.md` → `review-2026-04-25-corrected.md`
3. **Progressive/A-RAG 业务效果**：`progressive_rag_report_2026-05-05.md` → `llm-quality-bakeoff-2026-05-16.md`
4. **本地 LLM 性能与部署选型**：`llm-bakeoff-report-2026-05-15.md` → `llm-bakeoff-r2-diagnosis-2026-05-16.md`

## 其他材料

`screenshots/` 下的图片是 Grafana 生产监控证据，不是独立测试报告。
