---
name: 当前开发优先级
description: roadmap.md 中的修订执行顺序，截至 2026-04-01 的完成状态
type: project
---

参考 `docs/roadmap.md` 和 `docs/testing-plan.md`（2026-04-01 更新）：

**🟢 已完成 — 框架升级（Function Calling 改造）**
- [x] 增强 System Prompt — 注入用户身份、强制工具调用规则（2026-04-01 加强）
- [x] 实现 Function Calling：`llm_client.generate_with_tools()` + `node_llm_with_tools` 节点
- [x] 简化 LangGraph 流程：图入口改为 `llm_with_tools`，`classify_intent` 降为 fallback
- [x] 启动入口整理 + 双 env 文件支持

**🟢 已完成 — RAG/Milvus 修复（2026-03-31）**
- [x] pymilvus 2.6 兼容性 monkey-patch
- [x] DashScope embedding check_embedding_ctx_length=False
- [x] langchain_classic.retrievers 包路径修复

**🟢 已完成 — 工具级修复（2026-04-01）**
- [x] 统一参数解析层 `param_resolver.py`（项目名→ID、成员名→ID，带进程级缓存）
- [x] `save_workhour.py` 接入 param_resolver，修复 LLM 传项目名导致找不到项目的 bug
- [x] `query_timesheet.py` 修复不传 memberId 返回全员数据的 bug
- [x] `task_executor.py` 权限拒绝错误不再暴露内部 UUID

**🔴 第一优先（进行中）— 测试覆盖**
- [ ] 生成 2000 条测试用例（用另一个模型，见 `docs/testing-plan.md` 第五节生成 Prompt）
- [ ] 运行 Layer 1 意图分类测试，建立精度基线
- [ ] jieba 中文分词接入 BM25（1小时，低风险）

**🟡 第二优先 — 能力扩展**
- [ ] 工时审核 Tool（approve_workhour）
- [ ] 导出报表 Tool（export_report）
- [ ] Prometheus 指标收集（Task 50.1-50.3）
- [ ] 多轮引导式 Skill 优化（Tool 层稳定后再做）

**数据库问题**：`ai_sessions` 表不存在 → 调用 `POST /api/init-db` 接口或启动时自动建表（待修复）

**Why:** 助手"笨"的根因是意图分类架构，Function Calling 改造从根本解决了这个问题。当前重点是测试验证精度，再扩展能力。
**How to apply:** 新对话时直接按此优先级推进，不要重复已完成的工作。
