---
name: 项目概览
description: ai-service 项目的技术栈、架构、目录结构和关键文件（2026-04-01 更新）
type: project
---

工时管理系统的 AI 智能助手服务，FastAPI + LangGraph 构建，与 Spring Boot 主后端（8080）集成。

**技术栈**：FastAPI、LangGraph、LangChain、Milvus、Redis、MySQL、阿里云 DashScope（qwen-plus 对话+Function Calling）

**请求链路（改造后）**：Spring Boot 注入 X-User-ID/X-Entity-Type/X-Department-ID → FastAPI 8000 → LangGraph Agent → `node_llm_with_tools`（Function Calling 一步完成意图识别+参数提取）→ 四个分支（TOOL_EXECUTION / KNOWLEDGE_QA / GENERAL_CHAT / CLARIFY）→ SSE 流式返回。降级路径：Function Calling 失败时 → `node_classify_intent`（规则匹配 + LLM 兜底）

**核心服务文件**（均在 `fastapi-service/app/services/`）：
- `langgraph_agent.py` — LangGraph DAG 编排，现为系统主入口（新增 `node_llm_with_tools`、`_build_openai_tools`）
- `param_resolver.py` — 统一参数解析层：项目名→ID、成员名→ID，带进程级缓存（2026-04-01 新增）
- `llm_client.py` — LLM 调用，新增 `generate_with_tools()` 支持 Function Calling
- `intent_router.py` — 原意图识别路由，现降级为 fallback
- `task_executor.py` — 工具执行、参数解析、权限预检
- `permission_validator.py` — 细粒度权限校验，基于 X-Entity-Type 角色
- `langchain_rag.py` — RAG 管道（EnsembleRetriever + MultiQuery + CrossEncoder Reranker）
- `tool_registry.py` — 单例工具注册中心

**工具层**（`fastapi-service/app/tools/`，5个）：
- `query_timesheet.py` — 工时查询（memberId 无值时回退到当前用户，已修复全员数据问题）
- `save_workhour.py` — 工时填报（已接入 param_resolver，自动将项目名转换为 ID）
- `query_project.py`、`compute_statistics.py`、`generate_weekly_report.py`

**启动入口**：`fastapi-service/main.py`（顶部用 dotenv 加载 `.env` + `.env.local`）

**环境配置**：
- `.env` — Docker/生产配置（含 API 密钥，gitignored）
- `.env.local` — 本地开发覆盖（REDIS_HOST=localhost, MILVUS_HOST=localhost, SPRINGBOOT_BASE_URL=http://localhost:8080，gitignored）
- `fastapi-service/app/core/config.py` — Settings，env_file 加载顺序 `.env` → `.env.local`

**测试**（开发中）：`fastapi-service/tests/`，见 `docs/testing-plan.md`

**权限角色**：employee → deptSubAdmin → deptAdmin → regionAdmin → companyAdmin → superAdmin

**Why:** 项目架构信息方便后续快速定位文件和理解代码结构
**How to apply:** 回答"这个功能在哪个文件"或"架构是怎样的"时直接参考，验证前先核实文件是否存在
