# MEMORY.md

## ⚠️ 必读：架构诊断
- 助手"笨"的根因诊断 → 已归档至 `docs/changelog/diagnosis-2026-03-31.md`（两步LLM调用+弱System Prompt是根因，Function Calling是解法，已完成改造）

## 项目背景
- [项目概览](project_overview.md) — 技术栈、**改造后架构链路**、核心服务文件、启动方式、环境配置（2026-03-31 更新）
- [文档目录结构](project_docs_structure.md) — 两个 docs 目录整合后的结构，各文件定位（2026-03-31 整合）

## 开发计划
- [当前开发优先级](project_next_steps.md) — Function Calling + RAG + param_resolver 均已完成，下一步：测试数据生成 + 精度验证

## 接口参考
- [SpringBoot API 参考](reference_springboot_api.md) — 工时/项目/用户接口路径、字段名、查询参数（2026-04-01 整理自源码）

## 生产环境
- [生产环境基础设施](project_infra.md) — 实测拓扑：ai-service 在 172 Docker Compose + 反向 SSH 隧道到 116:9901；SpringBoot 网关模式。8 个阻断 bug 见 `docs/deploy/deploy-fixes-2026-04-22.md`（2026-04-22 更新）

## 精度测试
- [Layer1 精度路线图](project_accuracy_roadmap.md) — v1=70.2%、v2=70.9%、失败根因分析、v3 Prompt改进方案（3项改动）、PlannerAgent架构方向（2026-04-02）

## 踩坑记录
- [RAG/Milvus 兼容性修复经验](feedback_rag_milvus_fixes.md) — pymilvus 2.6 ORM bug、DashScope embedding 兼容、langchain包结构变化（2026-03-31）

## 基准测试约束（必读）
- [简历叙事约束](feedback_benchmark_narrative.md) — FC延迟/SQL安全/RAG Recall的**绝对不要写**和**正确写法**，基于三轮实测数据（2026-04-25）
