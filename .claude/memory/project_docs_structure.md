---
name: 文档目录结构
description: 两个 docs 目录整合后的最终结构和各文件定位（2026-04-01 更新：新增 changelog/ 子目录和 testing-plan.md）
type: project
---

**`docs/`（对外文档）**
- `api.md` — 接口文档，面向开发者/集成方
- `deployment.md` — 部署运维文档
- `user-guide.md` — 最终用户手册
- `roadmap.md` — 综合升级路线图（Bug修复 + 技术债 + 能力扩展 + RAG优化），是**主要开发参考**
- `springboot-api-reference.md` — SpringBoot 后端接口速查（工时/项目/用户，2026-04-01 整理自源码）
- `testing-plan.md` — AI 助手测试方案（2000条量级，三层测试架构，2026-04-01 新增）
- `changelog/` — 变更日志子目录
  - `2026-03-31.md` — Function Calling 改造、RAG 修复
  - `2026-04-01.md` — param_resolver、Bug修复、API文档
  - `diagnosis-2026-03-31.md` — 助手"笨"根因诊断归档

**`fastapi-service/docs/`（技术参考）**
- `rag-upgrade-roadmap.md` — RAG 管道详细技术方案（含代码示例），做 RAG 优化时查阅
- `deprecated/` — 废弃代码和历史设计文档归档

**`.claude/memory/`（Claude Code 记忆，跨机器共享）**
- `MEMORY.md` — 记忆索引
- `project_overview.md`、`project_next_steps.md` 等 — 各类记忆文件

**Why:** 避免未来再重复整合，明确每个文档的定位
**How to apply:** 找文档时直接导航到对应位置，不要再建重复文档
