# AI 智能助手 — 部署文档

> 适用版本：当前主分支
> 更新日期：2026-04-13

**相关文档**：
- [生产环境部署指南（deploy-guide.md）](deploy-guide.md) — 环境拓扑、生产部署方案、运维建议
- [LLM 推理服务器部署（deploy-model-server.md）](deploy-model-server.md) — vLLM / Ollama 操作细节

---

## 目录

1. [架构概览](#1-架构概览)
2. [环境要求](#2-环境要求)
3. [快速启动（开发环境）](#3-快速启动开发环境)
4. [生产环境部署](#4-生产环境部署)
5. [配置说明](#5-配置说明)
6. [知识库初始化](#6-知识库初始化)
7. [数据库初始化](#7-数据库初始化)
8. [健康检查与验证](#8-健康检查与验证)
9. [故障排查](#9-故障排查)
10. [升级与回滚](#10-升级与回滚)

---

## 1. 架构概览

### 1.1 整体部署拓扑

```
┌───────────────────────────────────────────────────────────────┐
│                        用户浏览器                              │
└──────────────────────────┬────────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼────────────────────────────────────┐
│                  SpringBoot 后端 (8080)                        │
│                                                               │
│  • JWT 鉴权（提取 user_id / entity_type / dept_id）           │
│  • AIController：POST /api/ai/chat → SSE 流式代理             │
│  • AIPermissionInterceptor：权限上下文构建                     │
│  • WebClient：转发请求到 FastAPI，透传 SSE 事件流              │
└──────────────────────────┬────────────────────────────────────┘
                           │ HTTP SSE（内网）
┌──────────────────────────▼────────────────────────────────────┐
│               FastAPI AI Service (8000)                       │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                  LangGraph Agent                        │  │
│  │                                                         │  │
│  │  START                                                  │  │
│  │    └─► llm_with_tools ──(条件路由)──┬─► execute_tool  │  │
│  │      (Function Calling 主节点)      ├─► execute_rag   │  │
│  │      qwen3-8b(vLLM) + tools schema ├─► execute_llm   │  │
│  │      一次调用完成意图+参数提取        └─► clarify_node │  │
│  │             │                              │           │  │
│  │      降级 ──┘ (LLM 不可用时)              └──► END    │  │
│  │             ▼                                           │  │
│  │       classify_intent                                   │  │
│  │    (IntentRouter 规则匹配 fallback)                     │  │
│  │                                                         │  │
│  │  多工具并行（≥2 tool_calls 时）：                        │  │
│  │    llm_with_tools ─► plan_and_execute ─► summarize     │  │
│  │    (PlannerAgent 并行执行 + LLM 综合汇总)               │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────┐  ┌──────────────────────────────────┐   │
│  │  Intent Router   │  │         RAG 检索引擎              │   │
│  │  (降级 fallback) │  │                                  │   │
│  │                  │  │  knowledge-base/ 文档加载         │   │
│  │ 关键词规则匹配    │  │  MarkdownHeaderTextSplitter       │   │
│  │ （LLM 不可用时） │  │  → EnsembleRetriever             │   │
│  │                  │  │    ├── Milvus 向量检索（60%）     │   │
│  │ 路由结果：        │  │    └── BM25 关键词检索（40%）     │   │
│  │ • tool_execution │  │  → MultiQueryRetriever（改写）    │   │
│  │ • knowledge_qa   │  │  → CrossEncoderReranker（精排）   │   │
│  │ • general_chat   │  │  → ChatPromptTemplate + LLM       │   │
│  │ • clarify        │  │  → 附加文档来源标注               │   │
│  └──────────────────┘  └──────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────┐  ┌──────────────────────────────────┐   │
│  │   Tool Executor  │  │         Memory System             │   │
│  │                  │  │                                   │   │
│  │ 权限校验         │  │  短期记忆（Session Memory）        │   │
│  │ • can_access_    │  │  • Redis 存储对话历史              │   │
│  │   user_data      │  │  • 按 session_id 隔离             │   │
│  │ • can_access_    │  │  • 最近 N 轮注入 System Prompt    │   │
│  │   project_data   │  │                                   │   │
│  │                  │  │  长期记忆（User Memory）           │   │
│  │ 注册工具(7个)：   │  │  • Redis 存储用户偏好/习惯        │   │
│  │ • query_timesheet│  │  • 规则提取（不消耗 LLM token）   │   │
│  │ • query_project  │  │  • 注入 System Prompt 个性化      │   │
│  │ • compute_stats  │  │                                   │   │
│  │ • generate_weekly│  │  PromptBuilder                    │   │
│  │ • save_workhour  │  │  • 合并 base_system + 记忆 + 历史 │   │
│  │ • approve_workhour│ │                                   │   │
│  │ • export_report  │  │                                   │   │
│  │ • sql_query ──┐  │  │                                   │   │
│  │   (SQL Agent) │  │  │                                   │   │
│  └───────────────┼──┘  └──────────────────────────────────┘   │
│                  │                                             │
│  ┌───────────────▼──────────────────────────────────────────┐  │
│  │                   SQL Agent                              │  │
│  │  自然语言 → LLM 生成 SQL → 安全校验 → 执行 → LLM 汇总    │  │
│  │  • 动态表选择（关键词→相关表，减少 token）                 │  │
│  │  • 三层安全：只读账号 + 应用层白名单 + 权限约束注入        │  │
│  │  • 独立 LLM 配置（SQL_AGENT_LLM_*，可切换更强模型）       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Prompt Manager                         │  │
│  │                                                          │  │
│  │  app/prompts/*.yaml  ──watchdog──►  热重载               │  │
│  │  • system.yaml           主对话 system prompt            │  │
│  │  • intent_classify.yaml  意图分类提示词                  │  │
│  │  • param_extract.yaml    参数提取提示词                  │  │
│  │  • rag.yaml              RAG 问答 ChatPromptTemplate     │  │
│  │  • weekly_report.yaml    周报生成提示词                  │  │
│  │  • sql_generation.yaml   SQL 生成提示词（SQL Agent）     │  │
│  │  • sql_summarize.yaml    SQL 结果汇总提示词              │  │
│  │                                                          │  │
│  │  LangChain PromptTemplate / ChatPromptTemplate 解析      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   审计日志系统                            │  │
│  │  每次对话在 finally 块写入：                              │  │
│  │  • conversation_logs：intent / tools_called / duration   │  │
│  │  • ai_sessions：会话轮次汇总                             │  │
│  │  • status：success / error / rejected（权限拒绝）        │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────┬──────────────┬──────────┬────────────────────────────┘
        │              │          │
   ┌────▼───┐    ┌─────▼───┐  ┌──▼──────────────────────────┐
   │ Redis  │    │ Milvus  │  │       MySQL (workhour)       │
   │        │    │ + etcd  │  │  • 业务表（工时/项目/人员）   │
   │短期记忆│    │ + minio │  │  • conversation_logs（审计） │
   │长期记忆│    │         │  │  • ai_sessions（会话汇总）   │
   └────────┘    │ 知识库  │  └─────────────────────────────┘
                 │ 向量存储│
                 └─────────┘
                 ┌────────────────────────────────────────┐
                 │  172.19.3.136（内网 GPU 服务器）          │
                 │  vLLM 推理服务：                        │
                 │  • qwen3-8b :8099 （主对话+工具+SQL）   │
                 │  • bge-large-zh :8097 （Embedding）     │
                 │  • Ollama :11434                        │
                 │                                        │
                 │  Docker 部署（ai-assistant-*）：        │
                 │  • ai-service :8000                    │
                 │  • redis :16379                        │
                 │  • milvus :29530                       │
                 │  • minio :29000/29001                  │
                 │                                        │
                 │  备选 LLM：                             │
                 │  • 阿里云 DashScope（外网，降级路径）   │
                 └────────────────────────────────────────┘
                 ┌────────────────────────────────────────┐
                 │  116.205.174.57（公网应用服务器）        │
                 │  • Spring Boot :9900                   │
                 │  • nginx :80/:443                      │
                 └────────────────────────────────────────┘
```

### 1.2 一次完整请求的处理流程

以"查询我本周工时"为例：

```
用户输入
  │
  ▼
[SpringBoot] JWT 解析 → 构建 PermissionContext（user_id, entity_type）
  │  POST /api/ai/chat  SSE 流式代理
  ▼
[FastAPI] stream_agent_response()
  │
  ├─① Prompt Manager 加载 system.yaml → base_system_prompt
  ├─② Memory System（PromptBuilder）
  │    ├── Redis 读取短期记忆（最近对话轮次）
  │    ├── Redis 读取长期记忆（用户偏好）
  │    └── 拼装 conversation_history（messages 列表）
  │
  ├─③ LangGraph.astream() 启动图执行
  │
  ├─④ [节点] llm_with_tools（Function Calling 主节点）
  │    └── qwen3-8b(vLLM) + tools schema + system prompt（含用户身份/日期/工具规则）
  │         一次 LLM 调用同时完成：意图识别 + 工具选择 + 参数提取
  │         → tool_call: query_timesheet(user_id=当前用户, start_date=本周一, end_date=今天)
  │         （复杂分析场景 → sql_query，≥2 tool_calls → PlannerAgent 并行执行）
  │
  ├─⑤ SSE 发送 tool_call 事件 → 前端显示"正在查询工时..."
  │
  ├─⑥ [节点] execute_tool
  │    ├── PermissionValidator.can_access_user_data() 权限校验
  │    ├── TaskExecutor 调用 query_timesheet handler
  │    └── handler → HTTP 请求 SpringBoot /workhour/list 接口
  │
  ├─⑦ SSE 发送 response 事件（工时数据格式化为 Markdown）
  │
  ├─⑧ [finally] 审计日志写入 conversation_logs
  │    └── Memory System 保存本轮对话到 Redis
  │
  └─⑨ SSE 发送 done 事件
```

### 1.3 RAG 知识库查询流程

以"工时填报的截止时间是什么时候"为例：

```
用户提问
  │
  ▼
[llm_with_tools] → finish_reason=stop + 含知识问答关键词 → intent = knowledge_qa
  │
  ▼
[execute_rag] → LangChainRAGService.query()
  │
  ├─① MultiQueryRetriever
  │    └── LLM 将问题改写为 3 种不同表述，并行检索
  │
  ├─② EnsembleRetriever（混合检索）
  │    ├── Milvus 向量检索（语义相似，权重 60%）
  │    └── BM25 关键词检索（精确词匹配，权重 40%）
  │
  ├─③ CrossEncoderReranker（精排）
  │    └── BAAI/bge-reranker-base 对检索结果重新打分，取 Top 5
  │
  ├─④ ChatPromptTemplate（rag.yaml）+ LLM 生成回答
  │
  └─⑤ 附加来源标注："📚 来源：工时填报管理制度.md"
```

### 1.4 服务列表

**开发环境**（docker-compose）：

| 服务 | 镜像 | 端口（宿主机） | 容器内部端口 | 用途 |
|------|------|---------------|-------------|------|
| ai-service | 本地构建 | 127.0.0.1:8000 | 8000 | FastAPI AI 核心服务 |
| redis | redis:7-alpine | 16379 | 6379 | 短期会话记忆 + 长期用户记忆 |
| milvus | milvusdb/milvus:v2.3.3 | 29530 | 19530 | 知识库向量存储（降级 FAISS） |
| etcd | quay.io/coreos/etcd:v3.5.5 | - | 2379 | Milvus 元数据存储 |
| minio | minio/minio | 29000/29001 | 9000/9001 | Milvus 对象存储 |
| prometheus *(可选)* | prom/prometheus:v2.48.0 | 9090 | 9090 | 监控指标收集 |
| grafana *(可选)* | grafana/grafana:10.2.2 | 3000 | 3000 | 监控可视化 |

> **生产环境端口**（172.19.3.136 服务器）：与开发环境一致，见下方「生产环境部署」章节。

### 1.5 技术栈速查

| 层次 | 技术 | 说明 |
|------|------|------|
| Agent 编排 | LangGraph `StateGraph` | 状态机驱动，条件路由 |
| RAG 检索 | LangChain LCEL | 多路召回 + Reranker 精排 |
| 向量存储 | Milvus / FAISS（降级） | 知识库语义检索 |
| 混合检索 | EnsembleRetriever | BM25（40%）+ 向量（60%） |
| 重排序 | CrossEncoderReranker | BAAI/bge-reranker-base |
| Prompt 管理 | PromptManager + YAML | watchdog 热重载 |
| 记忆系统 | Redis | 短期会话 + 长期用户偏好 |
| LLM 接入 | OpenAI 兼容接口 | vLLM qwen3-8b（本地 GPU）/ DashScope（降级） |
| SQL Agent | SQLAlchemy async + sqlparse | 自然语言转 SQL，三层安全防护 |
| 审计日志 | SQLAlchemy + MySQL | conversation_logs 表 |
| 流式输出 | SSE（Server-Sent Events） | LangGraph astream → FastAPI → SpringBoot → 浏览器 |

---

## 2. 环境要求

### 宿主机

| 项目 | 最低要求 | 推荐 |
|------|---------|------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB | 50 GB |
| OS | Linux / macOS / Windows (WSL2) | Ubuntu 22.04 |

### 软件依赖

- **Docker** ≥ 24.0
- **Docker Compose** ≥ 2.20（`docker compose` 命令，非 `docker-compose`）
- **Git**

### 外部依赖

- **MySQL 8.0**：业务数据库（workhour），地址 192.168.0.94:3306
- **vLLM 推理服务**：GPU 服务器 172.19.3.136:8099（qwen3-8b）+ :8097（bge-large Embedding）
- **Redis/Milvus/MinIO**：同 GPU 服务器 172.19.3.136（Docker 部署，ai-assistant-* 容器）
- **SpringBoot 后端**：提供工时/项目查询接口（生产地址 gst.thsware.com，端口 9900）
- **阿里云 DashScope API Key**：RAG Embedding 专用（DashScope text-embedding-v2）

---

## 3. 快速启动（开发环境）

### 3.1 克隆代码

```bash
git clone <repository-url>
cd ai-service
git submodule update --init --recursive
```

### 3.2 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填写以下必填项：

```dotenv
# LLM API Key（从阿里云 DashScope 控制台获取）
INTENT_LLM_API_KEY=sk-xxxx
CHAT_LLM_API_KEY=sk-xxxx

# MySQL 连接（使用宿主机 MySQL）
MYSQL_PASSWORD=your_mysql_password

# MinIO 密码（首次启动后不可更改）
MINIO_ROOT_PASSWORD=your_minio_password
```

### 3.3 启动服务

```bash
./start.sh
```

或手动：

```bash
docker compose up -d
```

### 3.4 初始化数据库

服务启动后，执行一次建表操作：

```bash
curl -X POST http://localhost:8000/api/db/init
```

成功返回：
```json
{"status": "ok", "message": "数据库表初始化完成"}
```

### 3.5 验证启动

```bash
curl http://localhost:8000/api/ai/health
```

预期响应：
```json
{
  "status": "healthy",
  "components": {
    "llm": true,
    "redis": true,
    "milvus": true,
    "database": true
  }
}
```

---

## 4. 生产环境部署

### 4.1 构建镜像

```bash
cd fastapi-service
docker build -t ai-service:latest .
```

### 4.2 使用生产配置启动

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

> 生产配置与开发配置的区别：
> - 不使用 `--reload`，改为 2 个 worker 进程
> - 不挂载源码目录，代码已打包进镜像
> - 所有服务添加 `restart: unless-stopped`
> - ai-service 限制资源（2GB 内存，1.5 CPU）
> - Prometheus/Grafana 默认不启动（需要时见 4.3）

### 4.3 启用监控（可选）

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile monitoring up -d
```

访问 Grafana：http://your-server-ip:3000（默认 admin/admin，首次登录请修改密码）

### 4.4 生产脚本

创建 `start-prod.sh`：

```bash
#!/bin/bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

创建 `stop-prod.sh`：

```bash
#!/bin/bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

---

## 5. 配置说明

所有配置通过 `.env` 文件注入，参考 `.env.example`：

### LLM 配置

| 变量 | 说明 | 当前生产值 |
|------|------|-----------|
| `INTENT_LLM_API_KEY` | 意图分类 LLM 的 API Key | `EMPTY`（vLLM 不校验） |
| `INTENT_LLM_API_BASE` | 意图分类 LLM 的 API 地址 | `http://172.19.3.136:8099/v1` |
| `INTENT_LLM_MODEL` | 意图分类模型名 | `qwen3-8b` |
| `CHAT_LLM_API_KEY` | 对话生成 LLM 的 API Key | `EMPTY` |
| `CHAT_LLM_API_BASE` | 对话生成 LLM 的 API 地址 | `http://172.19.3.136:8099/v1` |
| `CHAT_LLM_MODEL` | 对话生成模型名（Function Calling 主力） | `qwen3-8b` |
| `SQL_AGENT_LLM_API_KEY` | SQL Agent LLM Key（空=复用 CHAT） | `EMPTY` |
| `SQL_AGENT_LLM_API_BASE` | SQL Agent LLM 地址（空=复用 CHAT） | （复用 CHAT_LLM） |
| `SQL_AGENT_LLM_MODEL` | SQL Agent 模型（空=复用 CHAT） | （复用 CHAT_LLM） |
| `DASHSCOPE_API_KEY` | DashScope API Key（RAG Embedding 专用） | `sk-xxxx` |

> 当前全部使用 vLLM 本地部署的 qwen3-8b。如 SQL 生成质量不足，可单独将 SQL_AGENT_LLM_* 指向 DashScope qwen-plus。

### 数据库配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MYSQL_HOST` | MySQL 地址 | `host.docker.internal` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_DATABASE` | 数据库名 | `workhour` |
| `MYSQL_USER` | 用户名 | `root` |
| `MYSQL_PASSWORD` | 密码 | *(必填)* |

### 业务配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `SPRINGBOOT_BASE_URL` | SpringBoot 后端地址 | `https://gst.thsware.com` |
| `LOG_LEVEL` | 日志级别 | `INFO`（生产）/ `DEBUG`（开发） |

### Prompt 配置

Prompt 模板存放在 `fastapi-service/app/prompts/` 目录，YAML 格式，支持热重载（修改后自动生效，无需重启）。

| 文件 | 用途 |
|------|------|
| `system.yaml` | 主对话 System Prompt（含工具触发规则） |
| `intent_classify.yaml` | 意图分类提示词 |
| `param_extract.yaml` | 参数提取提示词 |
| `rag.yaml` | 知识库问答提示词 |
| `weekly_report.yaml` | 周报生成提示词 |
| `sql_generation.yaml` | SQL Agent SQL 生成提示词 |
| `sql_summarize.yaml` | SQL Agent 结果汇总提示词 |

---

## 6. 知识库初始化

### 6.1 添加文档

将知识库文档放入 `knowledge-base/` 目录，支持格式：

| 格式 | 说明 |
|------|------|
| `.md` | Markdown（推荐，按标题层级自动分割） |
| `.txt` | 纯文本 |
| `.pdf` | PDF 文档 |
| `.docx` | Word 文档 |
| `.xlsx` | Excel 表格 |

当前已有文档：
- `工时填报管理制度.md`
- `常见问题FAQ.md`

### 6.2 触发知识库加载

服务启动时自动加载 `knowledge-base/` 目录。如需在运行时重新加载：

```bash
curl -X POST http://localhost:8000/api/rag/reload
```

### 6.3 注意事项

- 文档内容修改后需手动触发重新加载
- Milvus 向量化依赖 DashScope Embedding API，确保 API Key 有 Embedding 权限
- 首次加载文档较慢（需要调用 Embedding API），后续使用 Milvus 缓存

---

## 7. 数据库初始化

AI 服务使用的表（在现有 `workhour` 数据库中自动创建）：

| 表名 | 用途 |
|------|------|
| `conversation_logs` | 审计日志，记录每次 AI 对话详情 |
| `ai_sessions` | 会话汇总，记录会话轮次和活跃时间 |

### 手动初始化

```bash
# 服务启动后执行一次
curl -X POST http://localhost:8000/api/db/init
```

### 也可以使用 SQL 脚本

```bash
mysql -h your-mysql-host -u root -p workhour < sql/init.sql
```

---

## 8. 健康检查与验证

### 8.1 服务健康检查

```bash
curl http://localhost:8000/api/ai/health
```

各组件状态说明：

| 组件 | 为 false 时影响 |
|------|----------------|
| `llm` | 无法进行 AI 对话 |
| `redis` | 无会话记忆，每次对话独立 |
| `milvus` | 知识库查询降级为 FAISS（本地内存） |
| `database` | 无法记录审计日志 |

### 8.2 端到端测试

```bash
# 发送一条测试消息
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"message": "你好", "session_id": "test-001"}'
```

### 8.3 查看日志

```bash
# 查看 ai-service 实时日志
docker compose logs -f ai-service

# 查看最近 100 行
docker compose logs --tail=100 ai-service

# 生产环境（指定 compose 文件）
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f ai-service
```

---

## 9. 故障排查

### ai-service 启动失败

**现象**：`docker compose ps` 显示 ai-service 状态为 Exit

**排查**：

```bash
docker compose logs ai-service | tail -50
```

常见原因：
- LLM API Key 未配置或无效 → 检查 `.env` 中 `INTENT_LLM_API_KEY` / `CHAT_LLM_API_KEY`
- MySQL 连接失败 → 确认 `MYSQL_HOST`、`MYSQL_PASSWORD` 正确，且 MySQL 已运行
- 端口占用 → `lsof -i :8000` 检查端口

### Milvus 启动慢 / 超时

Milvus 依赖 etcd 和 minio，冷启动需要 60-90 秒，属正常现象。

```bash
# 等待 Milvus 就绪
docker compose logs -f milvus | grep -i "ready"
```

若长时间无法就绪，检查内存是否充足（Milvus 最低需要 2GB）。

### RAG 知识库查询无结果

1. 确认 `knowledge-base/` 目录有文档
2. 检查 Milvus 是否正常运行：`docker compose ps milvus`
3. 重新加载知识库：`curl -X POST http://localhost:8000/api/rag/reload`
4. 查看加载日志确认文档数量：`docker compose logs ai-service | grep -i "chunk"`

### Redis 连接失败

会话记忆功能降级，其他功能不受影响。

```bash
docker compose restart redis
```

### LLM 响应超时

默认超时 60 秒。如网络不稳定可调整：

```bash
# 在 docker-compose.yml 的 ai-service environment 中添加
- LLM_TIMEOUT=120
```

### 权限错误（PermissionError）

用户收到"权限不足"提示，查看审计日志确认原因：

```bash
# 在数据库中查询 rejected 状态的记录
SELECT user_id, user_message, error_message, created_at
FROM conversation_logs
WHERE status = 'rejected'
ORDER BY created_at DESC
LIMIT 20;
```

---

## 10. 升级与回滚

### 升级步骤

```bash
# 1. 拉取最新代码
git pull

# 2. 重新构建镜像
docker compose build ai-service

# 3. 滚动重启（不中断其他服务）
docker compose up -d ai-service

# 生产环境：
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build ai-service
```

### 回滚步骤

```bash
# 查看最近镜像历史
docker images ai-service

# 回滚到上一个版本（用上次构建的镜像 tag）
docker compose stop ai-service
docker tag ai-service:previous ai-service:latest
docker compose up -d ai-service
```

### 数据卷备份

```bash
# 备份 Redis 数据
docker run --rm -v ai-service_redis-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/redis-backup-$(date +%Y%m%d).tar.gz /data

# 备份 Milvus 数据
docker run --rm -v ai-service_milvus-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/milvus-backup-$(date +%Y%m%d).tar.gz /data
```

---

## 11. Embedding 模型本地部署（规划中）

> 当前：使用阿里云 DashScope `text-embedding-v2` API（按量计费）。
> 服务器有 GPU 时，可迁移到本地部署，消除外部 API 依赖、降低延迟和成本。

### 11.1 方案选型

| 方案 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **HuggingFace TEI**（推荐） | 生产部署 | 独立服务、GPU 加速、OpenAI 兼容 API、高并发 | 多一个 Docker 容器 |
| sentence-transformers 直接加载 | 开发/测试 | 无需额外服务，零配置 | 模型在 FastAPI 进程内，占用内存大，重启慢 |
| Ollama | 开发调试 | 安装简单 | 与 Milvus 集成需额外适配，不推荐生产用 |

### 11.2 推荐模型（中文场景）

| 模型 | 向量维度 | 模型大小 | 中文效果 | 说明 |
|------|---------|---------|---------|------|
| `BAAI/bge-m3` | 1024 | 570 MB | ⭐⭐⭐⭐⭐ | **首选**，多语言，中英文均佳，支持长文本（8192 token） |
| `BAAI/bge-large-zh-v1.5` | 1024 | 326 MB | ⭐⭐⭐⭐⭐ | 纯中文，效果与 bge-m3 接近，体积更小 |
| `BAAI/bge-small-zh-v1.5` | 512 | 93 MB | ⭐⭐⭐⭐ | GPU 资源受限时的备选 |

### 11.3 部署步骤（HuggingFace TEI）

**Step 1：在 `docker-compose.yml` 中添加 TEI 服务**

```yaml
tei:
  image: ghcr.io/huggingface/text-embeddings-inference:1.5
  ports:
    - "8001:80"
  volumes:
    - tei-model-cache:/data
  command: --model-id BAAI/bge-large-zh-v1.5 --dtype float16
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  restart: unless-stopped
```

首次启动会自动从 HuggingFace 下载模型（`bge-large-zh-v1.5` 约 326 MB）。
如服务器无法访问 HuggingFace，可提前下载模型文件挂载到 `/data` 目录。

**Step 2：修改 `langchain_rag.py` 的 Embedding 初始化**

```python
# 当前（DashScope API）：
self.embeddings = OpenAIEmbeddings(
    model="text-embedding-v2",
    openai_api_key=api_key,
    openai_api_base=api_base,
    chunk_size=20,
    check_embedding_ctx_length=False,  # DashScope 不接受 token ID 数组
)

# 切换为本地 TEI（改这一处即可，其余代码零改动）：
self.embeddings = OpenAIEmbeddings(
    model="BAAI/bge-large-zh-v1.5",
    openai_api_key="none",               # TEI 不需要鉴权
    openai_api_base="http://tei:80/v1",  # Docker 内网访问
    chunk_size=100,                      # TEI 无 25 条限制，可调大
    check_embedding_ctx_length=False,    # 本地服务同样只接受字符串
)
```

**Step 3：更新 `.env`（可选，将 TEI 地址做成可配置）**

```dotenv
EMBEDDING_API_BASE=http://tei:80/v1
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
```

**Step 4：Milvus 集合重建**

切换 Embedding 模型后，向量维度可能从 1536（DashScope）变为 1024（bge 系列），**必须重建 Milvus 集合**：

```bash
# 触发知识库重新加载（drop_old=True 会自动重建集合）
curl -X POST http://localhost:8000/api/rag/reload
```

### 11.4 注意事项

1. **向量维度变化**：DashScope `text-embedding-v2` 输出 1536 维，`bge-large-zh-v1.5` 输出 1024 维。切换时必须删除旧的 Milvus collection 并重建，否则维度不匹配会报错。

2. **`check_embedding_ctx_length=False` 保留**：本地 TEI 服务同样接收文本字符串而非 token 数组，此参数需保留。我们的 `_split_documents(chunk_size=512)` 已保证所有文本分块远低于 8192 token 上限，不存在截断风险。

3. **首次下载耗时**：TEI 容器启动时从 HuggingFace 下载模型，内网受限时可提前手动下载：
   ```bash
   # 使用 huggingface-cli 下载到本地后挂载
   pip install huggingface_hub
   huggingface-cli download BAAI/bge-large-zh-v1.5 --local-dir ./models/bge-large-zh
   ```
   然后 docker-compose volumes 挂载 `./models/bge-large-zh:/data`。

---

## 附录：常用命令速查

```bash
# 查看所有服务状态
docker compose ps

# 重启单个服务
docker compose restart ai-service

# 进入容器调试
docker compose exec ai-service bash

# 完全清理（⚠️ 会删除所有数据卷）
docker compose down -v

# 查看资源占用
docker stats
```
