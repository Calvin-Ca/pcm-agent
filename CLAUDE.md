# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是**工时管理系统**的 AI 智能助手服务模块，基于 FastAPI + LangGraph 构建，提供自然语言交互能力，与 Spring Boot 主后端（端口 8080）集成。

## 常用命令

### 启动服务（Docker）

```bash
# 复制并配置环境变量（填入 DASHSCOPE_API_KEY）
cp .env.example .env

# 启动全部依赖服务（MySQL、Redis、Milvus、Prometheus、Grafana）
./start.sh
# 或
docker-compose up -d

# 停止服务
./stop.sh
```

### 本地开发（不使用 Docker）

```bash
conda create -n workhour python=3.11
conda activate workhour
pip install -r fastapi-service/requirements.txt
cd fastapi-service
python main.py
```

### 运行测试

```bash
# 在 fastapi-service/ 目录下执行
pytest tests/test_core_functionality.py -v          # 核心功能测试
pytest tests/test_e2e_phase8.py -v                  # 端到端测试
pytest tests/test_intent_router.py -v               # 意图识别测试
pytest tests/test_langchain_rag_retrieval.py -v     # RAG 检索测试
pytest tests/performance/test_response_time.py -v  # 性能测试
```

### 服务访问地址

| 服务 | 地址 |
|------|------|
| FastAPI AI Service | http://localhost:8000 |
| Swagger API 文档 | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |

## 代码架构

### 请求处理流程

```
Spring Boot (8080)
  → 注入 X-User-ID、X-Entity-Type、X-Department-ID 请求头（已完成 JWT 验证）
  → POST /api/ai/chat/stream（FastAPI 8000）
    → LangGraph Agent
      → IntentRouter.classify（规则匹配 → LLM 兜底）
      → 分支路由:
          TOOL_EXECUTION → PermissionValidator + TaskExecutor → 调用工具（HTTP 转发到 SpringBoot）
          KNOWLEDGE_QA   → LangChain RAG（Milvus + BM25 + CrossEncoder Reranker）
          GENERAL_CHAT   → LLM 直接回复
          CLARIFY        → 澄清追问
    → SSE 事件流返回
```

### 核心服务文件

| 文件 | 职责 |
|------|------|
| `app/services/intent_router.py` | 意图识别与路由（1093 行），是系统入口的核心逻辑 |
| `app/services/langgraph_agent.py` | LangGraph DAG 编排，管理节点跳转和流式输出 |
| `app/services/task_executor.py` | 工具执行：参数解析、权限预检、调用工具 |
| `app/services/permission_validator.py` | 细粒度权限校验，基于 X-Entity-Type 角色 |
| `app/services/langchain_rag.py` | RAG 管道：EnsembleRetriever + MultiQuery + Reranker |
| `app/services/tool_registry.py` | 单例工具注册中心，负责工具注册、查询、参数验证 |
| `app/services/prompt_manager.py` | 从 `prompts/*.yaml` 加载 Prompt，支持热更新 |
| `app/services/session_memory.py` | 短期会话记忆（Redis，TTL 30 分钟，保留 10 轮） |
| `app/services/user_memory.py` | 长期用户记忆（Redis + BM25 关键词提取） |

### 工具层（`app/tools/`）

5 个业务工具，均通过 HTTP 调用 Spring Boot API：

- `query_timesheet.py` — 工时查询（存在已知 Bug：默认返回全员数据，参数注入缺陷）
- `query_project.py` — 项目查询
- `compute_statistics.py` — 统计分析
- `generate_weekly_report.py` — 周报生成
- `save_workhour.py` — 引导式工时填报（多轮对话）

### LLM 模型配置

| 用途 | 模型 | 说明 |
|------|------|------|
| 意图识别 | qwen-flash | 轻量快速 |
| 主对话生成 | qwen-plus | 能力更强 |

均使用阿里云 DashScope API（OpenAI 兼容格式），通过 `.env` 中的 `DASHSCOPE_API_KEY` 配置。

### 权限角色体系

角色从低到高：`employee` → `deptSubAdmin` → `deptAdmin` → `regionAdmin` → `companyAdmin` → `superAdmin`

权限通过 Spring Boot 网关层注入请求头，`PermissionValidator` 在每次工具调用前检查。

### Prompt 管理

Prompt 模板存放在 `prompts/*.yaml`（系统 prompt、意图分类、参数提取、RAG、周报），由 `PromptManager` 加载并通过 `PromptBuilder` 合并短期/长期记忆后注入上下文。

## 已知问题（优先修复）

1. **`query_timesheet.py`** — 查工时时默认返回全员数据，参数注入有缺陷
2. **`save_workhour.py`** — 把项目名当作项目 ID 传递，参数解析层缺少类型转换
3. 参数处理逻辑分散在多处，缺少统一的参数校验层

## 关键配置文件

- `.env.example` — 环境变量模板（所有配置项说明）
- `fastapi-service/app/core/config.py` — Settings 类（Pydantic），应用级配置入口
- `docker-compose.yml` — 开发环境，包含全部依赖服务
- `docker-compose.prod.yml` — 生产环境覆盖配置
- `prompts/*.yaml` — Prompt 模板（修改后无需重启，热加载）
- `knowledge-base/*.md` — 企业知识库文档（修改后需重新构建 Milvus 索引）
