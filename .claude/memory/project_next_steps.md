---
name: 当前开发优先级
description: roadmap.md 中的修订执行顺序，截至 2026-04-07 的完成状态
type: project
updated: 2026-04-07
---

## ✅ 已完成 — 第一阶段（精度达标 + 业务补齐）

- [x] Function Calling 架构改造（2026-03-31）
- [x] System Prompt 增强、param_resolver.py、Bug 修复（2026-04-01）
- [x] approve_workhour Tool（deptAdmin+，2026-04-03）
- [x] jieba 中文分词接入 BM25 + 知识库新增《工时审核流程》《假期与加班政策》（2026-04-03）
- [x] Layer 1 精度迭代至 v5：82.6%（2026-04-03）
- [x] Layer 2 参数提取精度 v4：86.9%，有效精度 99.7%（2026-04-03）

## ✅ 已完成 — 第二阶段部分（L3 DeepSearch）

- [x] 激活 PlannerAgent + execution loop（2026-04-03）
  - ≥2 个 tool_calls → TaskPlan 并行执行
  - node_plan_and_execute：路径A(multi_tool_calls直接执行)/路径B(PlannerAgent生成计划)
  - node_summarize：plan_results → LLM 综合分析

## 🔴 当前待做 — 第二阶段剩余（4.7 ~ 4.11）

- [ ] SQL Agent（只读连接 + SQL 白名单，1.5d）
  "统计各部门近三月工时趋势并排序"
- [ ] 导出报表 Tool export_report（2h）
- [ ] Layer 1 收尾：knowledge_qa 含人名 few-shot（+2~3%，约 1h）
- [ ] Layer 3 端到端测试（接 SpringBoot 做完整链路测试）

## 🟢 第三阶段（4.14 ~ 4.18）

- [ ] LLM 本地部署（vLLM + Qwen2.5）
- [ ] Self-Reflection 机制
- [ ] Prometheus 指标收集（Task 50.1-50.3）
- [ ] 修复数据库密码硬编码（安全风险）
- [ ] 流式 RAG 输出

**Why:** Layer 2 有效精度 99.7%，主流场景（查工时/填工时/查项目）均已超 90%，系统已具备生产可用条件。
**How to apply:** 新对话时直接按此优先级推进，不要重复已完成的工作。
