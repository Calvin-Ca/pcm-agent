# RAG 渐进式披露改造 — Coding Agent 派单使用说明

> 关联设计文档:[`docs/rag-progressive-disclosure-design.md`](../rag-progressive-disclosure-design.md)
> 用途:把 RAG 改造工作拆给 coding agent(Claude Code / Cursor / Cline / Aider 等能读写代码、跑测试的 IDE agent)执行

---

## 3 份派单概览

| Agent | 阶段 | Prompt 文件 | 何时可启动 | 预估工时 |
|-------|------|------------|----------|---------|
| **F** | Phase 2 + 3:工具拆分 + Agent Loop 改造 | [`01-prompt-phase2-3-工具与agent-loop.md`](01-prompt-phase2-3-工具与agent-loop.md) | **立即可启动**(不依赖文档生成) | 4-6h |
| **E** | Phase 1:知识库整合 + 多格式转换 + 重建索引 | [`02-prompt-phase1-知识库整合.md`](02-prompt-phase1-知识库整合.md) | 等 4 个文档 agent 全部产出后 | 2-3h |
| **G** | Phase 4:对比评测 + 出报告 | [`03-prompt-phase4-评测.md`](03-prompt-phase4-评测.md) | 等 F + E 都完成后 | 2-3h |

## 推荐执行顺序

```
今天(2026-05-05):
  ┌─ 4 个文档生成 agent 并行跑(rag-doc-generation-prompts/)
  └─ 1 个 Agent F(工具+agent loop 改造)并行启动 ← 不依赖文档

明天(2026-05-06)上午:
  ├─ 文档生成完毕,启动 Agent E(知识库整合)
  └─ Agent F 应该已完成,代码合并到 main

明天(2026-05-06)下午:
  └─ 启动 Agent G(评测 + 出报告)

5/7 面试日:
  └─ 简历更新 + 面试 Q&A 过一遍
```

## Coding Agent 适用性

| Agent | 特点 | 推荐使用 |
|-------|------|---------|
| Claude Code | 全栈最强,理解大型仓库 | ✅ Agent F(改造)/ Agent G(评测) |
| Cursor | 交互友好,代码预览方便 | ✅ Agent F |
| Cline (VSCode) | 自动跑测试,迭代快 | ✅ Agent E(文件操作多) |
| Aider | 命令行,快但需手工监督 | 可选 |
| 普通对话 LLM | 不能直接执行代码 | ❌ 不推荐(只适合咨询) |

## 使用步骤

1. 打开你的 IDE coding agent(如 Claude Code 在项目根目录跑)
2. 把对应的 prompt 文件**全文**复制粘贴给 agent
3. agent 会:读必读文件 → 实现 → 写测试 → 跑测试 → 报告
4. 验收通过后,让 agent 写 commit message 提交
5. 三个 phase 都完成后,跑一遍端到端冒烟测试

## 共同约束(所有改造 prompt 共用)

- ❌ **不要修改** `docs/rag-progressive-disclosure-design.md` — 那是设计稿,如有偏差应在 commit message 中说明
- ❌ **不要破坏** 现有 RAG 链路 — `knowledge_qa` Tool 必须保留为 fallback
- ❌ **不要跳过测试** — 单元测试 + 集成测试都要写并通过
- ✅ **保持向后兼容** — `AgentState` 只能加字段,不能改/删现有字段
- ✅ **遵循现有代码风格** — 参考相邻文件的 import 顺序、log 风格、错误处理
- ✅ **commit 粒度** — 每个子任务一个 commit,commit message 用 `feat(progressive-rag): xxx` 前缀

## 验收冒烟测试(三个 phase 都跑完后)

```bash
cd fastapi-service
# 1. 单元测试全过
pytest tests/unit/test_kb_*.py -v
pytest tests/unit/test_agent_loop.py -v

# 2. 集成测试
pytest tests/integration/test_progressive_rag.py -v

# 3. 评测脚本能跑(只跑 2 条 smoke)
python tests/benchmark/bench_progressive_rag.py --smoke

# 4. 服务能起,/api/ai/chat/stream 可用
python main.py &
curl -X POST http://localhost:8000/api/ai/chat/stream -d '{"message":"加班算工时吗"}'
```

## 故障排查

| 问题 | 应对 |
|------|------|
| Agent 没读 design doc 直接开干 | 在 prompt 最前加一句"必须先读 design doc §X 再动手" |
| Agent 改了不该改的文件 | 在 prompt 里明确"禁止修改文件清单" |
| 测试不过但 agent 说过了 | 让它把 pytest 完整输出贴出来 |
| vLLM tool_calls 不稳定 | 这是已知问题(B7),不是 agent 的锅,不要让它去修 |
| Agent 想引入新依赖 | 拒绝,本期所有功能都基于已有库实现 |
