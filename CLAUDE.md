# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 生产环境速查（执行任务必读）

### 服务器 SSH 连接

| 服务器 | 用途 | SSH 命令 |
|--------|------|----------|
| 172.19.3.136 | GPU 服务器，运行 ai-service Docker Compose | `ssh caic@172.19.3.136` |
| 116.205.174.57 | 公网应用服务器，运行 SpringBoot + nginx | 从 172 跳转：`ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 '<命令>'"` |
| 192.168.0.94 | 数据库服务器，MySQL 3306，库名 workhour | 仅内网访问 |

> 注意：本地无法直接 SSH 到 116，必须经 172 跳转。

### 关键路径与命令

```bash
# 172：ai-service 目录
/home/caic/code/workhour/workhour_agent/

# 172：重建 ai-service 容器（改了 docker-compose.yml 或 .env 后执行）
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"

# 172：查看 ai-service 日志
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 50"

# 116：SpringBoot 启动脚本位置
/home/gongshi/gongshi-ht.sh
/home/gongshi/gongshi-ht.war   # WAR 包

# 116：重启 SpringBoot（需要 sudo）
ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cd /home/gongshi && sudo bash gongshi-ht.sh restart'"

# 116：nginx 配置
/usr/local/nginx/conf/nginx.conf
```

### 网络拓扑（简版）

```
浏览器 → nginx(116:443) → SpringBoot(116:9900) → 隧道(116:9901) → ai-service(172:8000)
```

反向 SSH 隧道（常驻 172）：`autossh -M 0 -N -R 9901:127.0.0.1:8000 useryzk@116.205.174.57`

### ⚠️ 公网测试必读：华为云 WAF 会限流开发机 IP

从**本地开发机** curl 公网 `https://gst.thsware.com/api/ai/chat` 多次后会出现 **403（0.04s 快速拒绝）**，这是华为云 WAF 对该 IP 的 CC 防护，**不是路径白名单问题，不是服务故障**。识别指纹：响应头带 `Set-Cookie: HWWAFSESID` + `Server: CW`。

**正确做法**：
1. **测试入口放 116 服务器**，脚本推过去跑（企业出口 IP 信誉高，基本不被限）
   ```bash
   cat scripts/e2e-regression-0423.sh | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/e2e.sh && chmod +x /tmp/e2e.sh'"
   ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/e2e.sh'"
   ```
2. 脚本内每条 curl 之间 `sleep 3`，加浏览器 UA + Origin + Referer
3. 已被限流 → 等 15~30 分钟自动解封，或换 IP（4G 热点）
4. **不要在未复测（至少两个不同 IP）前下"CDN 阻断"结论**
5. 最终验收必须**浏览器实测**，脚本过 ≠ 用户能用

完整诊断与证据：[`docs/waf-403-diagnosis-2026-04-23.md`](docs/waf-403-diagnosis-2026-04-23.md)

### 获取 JWT Token（用于 API 测试）

```bash
# 从接口获取（密码需加密，见 reference_e2e_testing.md）
# 或从浏览器 DevTools → Network → authenticate → Response → data.token
TOKEN="<从浏览器复制的 Bearer token，去掉 Bearer 前缀>"
```

---

## 跨机器记忆同步

记忆文件存放在项目内的 `.claude/memory/` 目录（已纳入 git）。
**新对话开始时请先读取 `.claude/memory/MEMORY.md`**，然后按需读取其中索引的各记忆文件，恢复项目上下文。

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
  → 构造 POST body，在 user_context 中携带用户信息（user_id / entity_type / department_id / auth_token）
  → POST /api/ai/chat/stream（FastAPI 8000）
    → 入口路由优先读取 body.user_context.*，header X-User-ID 等作为兜底
    → LangGraph Agent
      → node_llm_with_tools（Function Calling 主节点，qwen-plus + tools schema）
          ├─ tool_calls → ParamResolver（名称→ID）→ PermissionValidator → TaskExecutor → 调用工具
          ├─ knowledge_qa → LangChain RAG（Milvus + BM25 + CrossEncoder Reranker）
          ├─ general_chat → LLM 直接回复
          └─ （降级）→ IntentRouter 规则匹配（LLM 不可用时）
    → SSE 事件流返回
```

### 核心服务文件

| 文件 | 职责 |
|------|------|
| `app/services/langgraph_agent.py` | LangGraph DAG 编排，Function Calling 主节点 + 节点路由 |
| `app/services/intent_router.py` | 规则匹配意图路由（降级 fallback，LLM 不可用时使用） |
| `app/services/param_resolver.py` | 统一参数解析层：项目名→ID、成员名→ID，带进程级缓存 |
| `app/services/task_executor.py` | 工具执行：依赖注入、权限预检、调用工具 |
| `app/services/permission_validator.py` | 细粒度权限校验，基于 X-Entity-Type 角色 |
| `app/services/langchain_rag.py` | RAG 管道：EnsembleRetriever + MultiQuery + Reranker |
| `app/services/sql_engine.py` | SQL Agent 数据库连接池管理、SQL 执行、表结构缓存 |
| `app/services/tool_registry.py` | 单例工具注册中心，负责工具注册、查询、参数验证 |
| `app/services/prompt_manager.py` | 从 `prompts/*.yaml` 加载 Prompt，支持热更新 |
| `app/services/session_memory.py` | 短期会话记忆（Redis，TTL 30 分钟，保留 10 轮） |
| `app/services/user_memory.py` | 长期用户记忆（Redis + BM25 关键词提取） |

### 工具层（`app/tools/`）

6 个业务工具，5 个通过 HTTP 调用 Spring Boot API，1 个直接操作数据库：

- `query_timesheet.py` — 工时查询（`memberId` 无值时回退到当前用户，已修复全员数据问题）
- `query_project.py` — 项目查询
- `compute_statistics.py` — 统计分析
- `generate_weekly_report.py` — 周报生成
- `save_workhour.py` — 工时填报（已接入 `param_resolver`，自动将项目名转换为 ID）
- `sql_query.py` — **SQL Agent**（自然语言转 SQL，直接查询数据库，支持复杂分析场景）
- `generate_weekly_report.py` — 周报生成
- `save_workhour.py` — 工时填报（已接入 `param_resolver`，自动将项目名转换为 ID）

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

## 已知问题

> 以下问题均已修复（2026-04-01）

- ~~`query_timesheet.py` 默认返回全员数据~~ → 已在第143行加 `elif params.user_id` fallback
- ~~`save_workhour.py` 把项目名当项目 ID~~ → 已接入 `param_resolver.resolve_project_id()`
- ~~参数处理逻辑分散~~ → 已新建 `app/services/param_resolver.py` 统一处理

## 关键配置文件

- `.env.example` — 环境变量模板（所有配置项说明）
- `.env.local` — 本地开发覆盖（REDIS_HOST/MILVUS_HOST/SPRINGBOOT_BASE_URL=localhost）
- `fastapi-service/app/core/config.py` — Settings 类（Pydantic），应用级配置入口
- `docker-compose.yml` — 开发环境，包含全部依赖服务
- `docker-compose.prod.yml` — 生产环境覆盖配置
- `prompts/*.yaml` — Prompt 模板（修改后无需重启，热加载）
- `knowledge-base/*.md` — 企业知识库文档（修改后需重新构建 Milvus 索引）

## 参考文档

- `docs/springboot-api-reference.md` — SpringBoot 后端接口速查（工时/项目/用户，含字段说明）
- `docs/roadmap.md` — 升级路线与优先级
- `docs/changelog/` — 各版本变更记录（按日期命名，如 `2026-04-01.md`）
