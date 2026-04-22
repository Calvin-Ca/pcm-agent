# 生产环境部署指南

本文档覆盖 ai-service 在各环境（开发、测试、生产）的实际部署配置和运维操作。
项目架构图、请求处理流程、RAG 流程等见 [deployment.md](deployment.md)。
LLM 推理服务器（vLLM / Ollama）的详细操作命令见 [deploy-model-server.md](deploy-model-server.md)。

---

## 一、架构总览

### 服务器分工

| 服务器 | 角色 |
|--------|------|
| 116.205.174.57 | 公网应用服务器（前端、Spring Boot、nginx） |
| 172.19.3.136 | 内网 GPU + 服务服务器（vLLM、Ollama、ai-service、Redis、Milvus、MinIO） |
| 192.168.0.94 | 数据库服务器（MySQL 3306，库名 workhour） |

### 网络拓扑

```
互联网
    |
    v
gst.thsware.com (116.205.174.57)
    |-- nginx (80/443)
    |     |-- /api/ai/  -->  ai-service (172.19.3.136:8000, SSH 隧道)
    |     |-- /api/ /thsuaa/ /thsadmin/  -->  Spring Boot (127.0.0.1:9900)
    |
    |-- Spring Boot (9900, nohup 启动)

172.19.3.136（内网 GPU + 服务服务器）
    |-- vLLM qwen3-8b  (8099)  主力对话 LLM
    |-- vLLM bge-large (8097)  Embedding
    |-- Ollama         (11434) 备用
    |
    |-- Docker 部署（ai-assistant-*）
    |     |-- ai-service  (8000)
    |     |-- redis       (16379)
    |     |-- milvus      (29530)
    |     |-- minio       (29000/29001)
    |     |-- etcd        (2379)
    |
    +--> HTTP 调用 Spring Boot 业务接口 (116.205.174.57:9900)
    +--> MySQL (192.168.0.94:3306, 库名 workhour)
    +--> LLM 推理（本地 vLLM/Ollama）
```

### 端口速查

**172.19.3.136（Docker 部署，ai-assistant-* 容器）**：

| 宿主机端口 | 容器端口 | 服务 |
|-----------|---------|------|
| 8000 | 8000 | ai-service |
| 16379 | 6379 | Redis |
| 29530 | 19530 | Milvus |
| 29000 | 9000 | MinIO API |
| 29001 | 9001 | MinIO Console |
| 19091 | 9091 | Milvus Console |
| 2379 | 2379 | etcd |

**172.19.3.136（LLM 推理服务）**：

| 端口 | 服务 |
|------|------|
| 8099 | vLLM qwen3-8b |
| 8097 | vLLM bge-large-zh-v1.5 |
| 8098 | vLLM bge-base-zh-v1.5 |
| 11434 | Ollama |

**116.205.174.57（公网应用服务器）**：

| 端口 | 服务 |
|------|------|
| 80/443 | nginx |
| 9900 | Spring Boot |

**192.168.0.94（数据库服务器）**：

| 端口 | 服务 |
|------|------|
| 3306 | MySQL (workhour) |

---

## 二、开发环境

开发者本地 Windows 机器。依赖服务（Redis、Milvus）通过 Docker 运行，ai-service 直接用 Python 启动，便于热调试。网关层直接访问生产 gst.thsware.com。

### 2.1 前提条件

- Docker Desktop（Windows）
- Conda（Python 3.11）
- 内网可访问 172.19.3.136（或有阿里云 DashScope API Key）

### 2.2 启动依赖服务

```bash
# 在 ai-service 根目录下执行
docker-compose up -d redis etcd minio milvus
```

如需同时启动监控（Prometheus / Grafana）：

```bash
docker-compose up -d
```

### 2.3 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写 CHAT_LLM_API_KEY 和 MYSQL_PASSWORD 等必填项
```

`.env.local` 用于覆盖差异项，已预置本地开发常用值，优先级高于 `.env`：

```bash
# .env.local（已有，通常无需修改）
REDIS_HOST=localhost
MILVUS_HOST=localhost
MYSQL_HOST=localhost
SPRINGBOOT_BASE_URL=https://gst.thsware.com

# 使用内网 GPU 服务器 vLLM
CHAT_LLM_API_KEY=ollama
CHAT_LLM_API_BASE=http://172.19.3.136:8099/v1
CHAT_LLM_MODEL=qwen3-8b

INTENT_LLM_API_KEY=ollama
INTENT_LLM_API_BASE=http://172.19.3.136:8099/v1
INTENT_LLM_MODEL=qwen3-8b
```

若无法访问内网 GPU，改用阿里云 DashScope：

```bash
CHAT_LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
CHAT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_LLM_MODEL=qwen-plus

INTENT_LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
INTENT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
INTENT_LLM_MODEL=qwen-turbo
```

RAG Embedding 固定使用 DashScope（Ollama 量化 Embedding 质量不足），确保 `.env` 中已填写：

```bash
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### 2.4 运行 ai-service

```bash
conda create -n workhour python=3.11 -y
conda activate workhour
pip install -r fastapi-service/requirements.txt

cd fastapi-service
python main.py
```

服务启动后：

| 地址 | 说明 |
|------|------|
| http://localhost:8000 | API 服务 |
| http://localhost:8000/docs | Swagger 文档 |
| http://localhost:9090 | Prometheus（如已启动） |
| http://localhost:3000 | Grafana（如已启动，admin/admin） |

### 2.5 运行测试

在 `fastapi-service/` 目录下执行：

```bash
pytest tests/test_core_functionality.py -v      # 核心功能测试
pytest tests/test_e2e_phase8.py -v              # 端到端测试
pytest tests/test_intent_router.py -v           # 意图识别测试
pytest tests/test_langchain_rag_retrieval.py -v # RAG 检索测试
```

---

## 三、测试环境

- 地址：192.168.2.52
- 前后端通过 nginx 部署，端口 9650
- 目前不常用，按需参考开发环境步骤手动部署

---

## 四、生产环境

生产环境部署在 **172.19.3.136**（内网 GPU + 服务服务器）。

ai-service 与依赖服务（Redis、Milvus、MinIO）通过 Docker Compose 部署，vLLM/Ollama 独立部署。

### 4.1 部署架构

```
172.19.3.136
├── Docker（ai-assistant-* 容器）
│     ├── ai-service      (127.0.0.1:8000)
│     ├── redis           (16379)
│     ├── milvus          (29530)
│     ├── minio           (29000/29001)
│     └── etcd            (2379)
│
├── vLLM 服务（Docker 独立部署）
│     ├── vllm-qwen3-8b   (8099)
│     ├── vllm-bge-large  (8097)
│     └── vllm-bge-base   (8098)
│
└── Ollama 服务（Docker 独立部署）
      └── ollama           (11434)

116.205.174.57
├── nginx (80/443) → 反向代理到 172.19.3.136:8000
└── Spring Boot     (9900)
```

### 4.2 部署方式

#### 方式一：Docker Compose 部署（推荐）

```bash
# 在 172 服务器上操作
cd ~/code/workhour/workhour_agent

# 启动所有服务
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f ai-service
```

#### 方式二：nohup 部署（传统方式，已不推荐）

```bash
# 创建 Python 环境
conda create -n workhour python=3.11 -y
conda activate workhour
pip install -r fastapi-service/requirements.txt

# 启动服务
nohup uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2 &
```

### 4.3 SSH 隧道配置

从 116 服务器访问 172 的 ai-service，通过 nginx 代理或 SSH 隧道。

#### SSH 密钥配置

```bash
# 在 172 服务器上生成 SSH 密钥（如果还没有）
ssh-keygen -t rsa -b 4096 -C "yunzuku@116" -f ~/.ssh/id_rsa

# 将公钥复制到 172 服务器
ssh-copy-id -i ~/.ssh/id_rsa.pub caic@172.19.3.136

# 或手动添加公钥到 172 服务器的 ~/.ssh/authorized_keys
cat ~/.ssh/id_rsa.pub | ssh caic@172.19.3.136 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

#### SSH 隧道命令

```bash
# 建立 SSH 隧道（本地端口转发）
# 将 172:8000 映射到本地 8000
ssh -L 8000:127.0.0.1:8000 caic@172.19.3.136 -fN

# 建立持久化隧道（后台运行）
ssh -o ServerAliveInterval=60 -o ServerAliveCountMax=3 \
    -L 8000:127.0.0.1:8000 \
    -L 16379:127.0.0.1:16379 \
    -L 29530:127.0.0.1:29530 \
    caic@172.19.3.136 -fN

# 查看已建立的隧道
ps aux | grep ssh | grep -v grep

# 关闭隧道
pkill -f "ssh -L.*172.19.3.136"
```

#### SSH Config 配置（简化连接）

在 `~/.ssh/config` 中添加：

```
Host 172
    HostName 172.19.3.136
    User caic
    ForwardAgent yes
    ServerAliveInterval 60
    ServerAliveCountMax 3

Host 172-tunnel
    HostName 172.19.3.136
    User caic
    LocalForward 8000 127.0.0.1:8000
    LocalForward 16379 127.0.0.1:16379
    LocalForward 29530 127.0.0.1:29530
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

之后可以用简化命令：
```bash
ssh 172-tunnel -fN   # 建立隧道
ssh 172              # 直接连接
```

### 4.4 配置 .env

#### 4.4a Docker Compose 部署（生产，推荐）

```bash
# === LLM（使用本地 vLLM）===
INTENT_LLM_API_KEY=EMPTY
INTENT_LLM_API_BASE=http://127.0.0.1:8099/v1
INTENT_LLM_MODEL=qwen3-8b

CHAT_LLM_API_KEY=EMPTY
CHAT_LLM_API_BASE=http://127.0.0.1:8099/v1
CHAT_LLM_MODEL=qwen3-8b

# === RAG Embedding（DashScope 专用）===
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# === MySQL（数据库服务器）===
MYSQL_HOST=192.168.0.94
MYSQL_PORT=3306
MYSQL_DATABASE=workhour
MYSQL_USER=yunzuku
MYSQL_PASSWORD=yunzuku2021

# === Redis（Docker 容器，容器内用服务名）===
REDIS_HOST=redis
REDIS_PORT=6379

# === Milvus（Docker 容器，容器内用服务名）===
MILVUS_HOST=milvus
MILVUS_PORT=19530

# === MinIO ===
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# === Spring Boot 后端（116 服务器，公网）===
SPRINGBOOT_BASE_URL=https://gst.thsware.com

# === SQL Agent 只读账号 ===
SQL_AGENT_DB_HOST=192.168.0.94
SQL_AGENT_DB_PORT=3306
SQL_AGENT_DB_NAME=workhour
SQL_AGENT_DB_USER=read_only_ai
SQL_AGENT_DB_PASSWORD=read_only_ai

# === 日志 ===
LOG_LEVEL=INFO
```

#### 4.4b ai-service 裸跑 + 依赖用 Docker（本地开发）

```bash
# === LLM（使用内网 GPU 服务器 vLLM）===
INTENT_LLM_API_KEY=EMPTY
INTENT_LLM_API_BASE=http://172.19.3.136:8099/v1
INTENT_LLM_MODEL=qwen3-8b

CHAT_LLM_API_KEY=EMPTY
CHAT_LLM_API_BASE=http://172.19.3.136:8099/v1
CHAT_LLM_MODEL=qwen3-8b

# === RAG Embedding（DashScope 专用）===
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# === MySQL ===
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=workhour
MYSQL_USER=yunzuku
MYSQL_PASSWORD=yunzuku2021

# === Redis（Docker 容器，宿主机用高位端口映射）===
REDIS_HOST=127.0.0.1
REDIS_PORT=16379

# === Milvus（Docker 容器，宿主机用高位端口映射）===
MILVUS_HOST=127.0.0.1
MILVUS_PORT=29530

# === MinIO ===
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# === Spring Boot 后端（生产公网）===
SPRINGBOOT_BASE_URL=https://gst.thsware.com

# === SQL Agent 只读账号 ===
SQL_AGENT_DB_HOST=192.168.0.94
SQL_AGENT_DB_PORT=3306
SQL_AGENT_DB_NAME=workhour
SQL_AGENT_DB_USER=read_only_ai
SQL_AGENT_DB_PASSWORD=read_only_ai

# === 日志 ===
LOG_LEVEL=INFO
```

### 4.5 配置 nginx（116 服务器）

在 116 服务器上编辑 `/usr/local/nginx/conf/nginx.conf`，添加 AI 服务反向代理：

```nginx
location /api/ai/ {
    proxy_pass http://127.0.0.1:9900;   # 走 SpringBoot AIController（网关），不是直接连 ai-service
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

> **关于反向 SSH 隧道**：ai-service 在 172.19.3.136，只绑 `127.0.0.1:8000`。116 上的 nginx 不能直连 172:8000，路径是：
> - 浏览器 → nginx(116) → SpringBoot(127.0.0.1:9900) → 反向隧道(127.0.0.1:9901) → ai-service(172:8000)
> - 隧道由 172 上的 autossh 常驻维持：`ssh -R 9901:127.0.0.1:8000 useryzk@116`

重载 nginx：

```bash
/usr/local/nginx/sbin/nginx -t
/usr/local/nginx/sbin/nginx -s reload
```

### 4.6 初始化知识库索引

首次部署后需构建 Milvus 向量索引。修改 `knowledge-base/*.md` 后也需重建。

```bash
curl -X POST http://127.0.0.1:8000/api/rag/reload
```

或进入容器执行：

```bash
docker exec -it ai-assistant-service python -c "from app.services.langchain_rag import build_index; build_index()"
```

### 4.7 健康检查

```bash
# ai-service 健康检查
curl http://127.0.0.1:8000/api/ai/health

# Docker 服务状态
docker compose ps

# 查看服务日志
docker compose logs -f ai-service
docker compose logs -f milvus
docker compose logs -f redis
```

预期响应：

```json
{
  "status": "healthy",
  "components": {
    "intent_router": true,
    "task_executor": true,
    "tool_registry": true,
    "permission_validator": true,
    "planner_agent": true
  }
}
```

---

## 五、LLM 推理服务器

服务器地址：172.19.3.136（内网 GPU，4x RTX 4090）。

详细部署命令（启动容器、压测、GPU 资源分配等）见 [deploy-model-server.md](deploy-model-server.md)。

### 当前运行服务

| 端口 | 服务 | 模型 | GPU | 用途 |
|------|------|------|-----|------|
| 8099 | vLLM | qwen3-8b | GPU 2 | 主力对话 LLM（支持 Function Calling） |
| 8097 | vLLM | bge-large-zh-v1.5 | GPU 3 | Embedding（主用） |
| 8098 | vLLM | bge-base-zh-v1.5 | GPU 3 | Embedding（备用） |
| 11434 | Ollama | qwen3 系列等 | GPU 1+3 | 备用对话 LLM |

### 验证服务可用性

```bash
# 检查 vLLM 主对话服务
curl http://172.19.3.136:8099/v1/models

# 检查 Embedding 服务
curl http://172.19.3.136:8097/v1/models

# 检查 Ollama
curl http://172.19.3.136:11434/api/tags
```

---

## 六、配置项参考

所有配置通过根目录 `.env` 注入，`.env.local` 中的同名项优先级更高。完整模板见 `.env.example`。

### LLM 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `INTENT_LLM_API_KEY` | — | 意图识别 LLM 的 API Key |
| `INTENT_LLM_API_BASE` | DashScope 地址 | 意图识别 LLM 的 API 地址 |
| `INTENT_LLM_MODEL` | `qwen-flash` | 意图识别模型（建议轻量快速） |
| `CHAT_LLM_API_KEY` | *(必填)* | 主对话 LLM 的 API Key |
| `CHAT_LLM_API_BASE` | DashScope 地址 | 主对话 LLM 的 API 地址 |
| `CHAT_LLM_MODEL` | `qwen-plus` | 主对话模型（建议能力更强） |
| `DASHSCOPE_API_KEY` | — | DashScope Key，RAG Embedding 专用 |

### 数据库配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MYSQL_HOST` | `localhost` | MySQL 地址 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_DATABASE` | `workhour` | 数据库名 |
| `MYSQL_USER` | `root` | 用户名 |
| `MYSQL_PASSWORD` | — | 密码 |
| `REDIS_HOST` | `localhost` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `MILVUS_HOST` | `localhost` | Milvus 地址 |
| `MILVUS_PORT` | `19530` | Milvus 端口 |

### 业务配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SPRINGBOOT_BASE_URL` | `http://localhost:8080` | Spring Boot 后端地址（生产改为 `http://127.0.0.1:9900`） |
| `LOG_LEVEL` | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `SQL_AGENT_ENABLED` | `true` | 是否启用 SQL Agent 工具 |
| `SQL_AGENT_MAX_ROWS` | `500` | SQL 查询最大返回行数 |
| `SQL_AGENT_QUERY_TIMEOUT` | `30` | SQL 查询超时秒数 |

---

## 七、常用运维命令

### Docker Compose（生产）

```bash
# 查看所有服务状态
docker compose ps

# 启动所有服务
docker compose up -d

# 重启 ai-service
docker compose restart ai-service

# 停止所有服务
docker compose down

# 实时日志
docker compose logs -f ai-service
docker compose logs -f milvus
docker compose logs -f redis

# 查看服务日志（指定行数）
docker compose logs --tail=100 ai-service

# 健康检查
curl http://127.0.0.1:8000/api/ai/health
```

### 服务单独管理

```bash
# 查看某个服务状态
docker ps | grep ai-assistant

# 重启单个服务
docker restart ai-assistant-service
docker restart ai-assistant-milvus
docker restart ai-assistant-redis
docker restart ai-assistant-minio

# 进入容器调试
docker exec -it ai-assistant-service bash
docker exec -it ai-assistant-redis redis-cli -p 16379

# 查看容器内进程
docker exec ai-assistant-service ps aux
```

### Milvus 运维

```bash
# Milvus 健康检查
curl http://127.0.0.1:29530/healthz

# Attu Web UI（Milvus 管理界面）
# 访问 http://172.19.3.136:8088
# 默认账号：root / milvus
docker ps | grep attu

# 重建 Milvus 索引（知识库重建）
curl -X POST http://127.0.0.1:8000/api/rag/reload

# 或进入容器重建
docker exec -it ai-assistant-service python -c "from app.services.langchain_rag import build_index; build_index()"
```

### Redis 运维

```bash
# Redis 连接测试
docker exec -it ai-assistant-redis redis-cli -p 16379 ping

# 查看 key 数量
docker exec -it ai-assistant-redis redis-cli -p 16379 dbsize

# 清空会话缓存（谨慎）
docker exec -it ai-assistant-redis redis-cli -p 16379 flushdb

# 手动执行 Redis 命令
docker exec -it ai-assistant-redis redis-cli -p 16379
```

### MinIO 运维

```bash
# MinIO Console
# 访问 http://172.19.3.136:29001
# 默认账号：minioadmin / minioadmin123

# MinIO 健康检查
curl http://127.0.0.1:29000/minio/health/live

# 使用 mc 命令行工具（需要安装）
mc alias set local http://127.0.0.1:29000 minioadmin minioadmin123
mc ls local/
```

---

## 八、部署建议与方案

### 8.1 推荐部署方式：Docker Compose

生产环境使用 Docker Compose 部署 ai-service，与 Redis、Milvus、MinIO 等依赖服务同属一个 docker 网络，容器间通过服务名通信。

### 8.2 资源需求评估

ai-service 本身不运行任何 LLM（LLM 推理全部在 172.19.3.136），主要消耗：

| 资源 | 空闲 | 单请求峰值 | 说明 |
|------|------|-----------|------|
| CPU | < 0.1 核 | 0.2~0.5 核 | 主要是 FastAPI 路由、JSON 序列化、BM25 检索 |
| 内存 | ~300 MB | ~600 MB | LangGraph 状态、BM25 索引、CrossEncoder Reranker 模型、会话缓存 |
| 网络 | 低 | 取决于 LLM 响应速度 | SSE 长连接，每请求持续 5~30 秒 |

### 8.3 高可用建议

**当前阶段：单实例足够**，理由：
- 工时管理系统为内部系统，并发用户数有限
- ai-service 的瓶颈在 LLM 推理（172.19.3.136），而非 ai-service 本身
- 单实例配合 `--workers 2`（uvicorn 多进程）可处理并发 SSE 请求

如需多实例，nginx 负载均衡配置示意：

```nginx
upstream ai_service {
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
}

location /api/ai/ {
    proxy_pass http://ai_service/api/ai/;
    # ... 其余 SSE 配置同上
}
```

注意：多实例共享 Redis（会话记忆），无状态设计，可直接水平扩展。

### 8.4 依赖服务连通性

```
ai-service (172.19.3.136, Docker 内)
    |
    +--[redis:6379]---------> Redis (Docker)    容器内服务名
    +--[milvus:19530]-------> Milvus (Docker)   容器内服务名
    +--[192.168.0.94:3306]-> MySQL              跨机内网
    +--[172.19.3.136:8099]-> vLLM qwen3-8b      本机 GPU
    +--[172.19.3.136:8097]-> vLLM bge-large     本机 GPU，Embedding
    +--[DashScope HTTPS]-----> 阿里云             外网，RAG Embedding

ai-service --[SpringBoot 业务调用]--> https://gst.thsware.com
```

所有内网连接均无防火墙阻断（同 VLAN），外网仅 DashScope Embedding。

### 8.6 健康检查与监控

**健康检查端点**（已实现）：

```bash
GET http://127.0.0.1:8000/api/ai/health
```

返回各组件（LLM、Redis、Milvus、MySQL）的连通状态，可接入 nginx upstream health check 或简单 cron 脚本监控：

```bash
# cron 每分钟检查，失败时发告警（示例）
* * * * * curl -sf http://127.0.0.1:8000/api/ai/health || echo "ai-service unhealthy $(date)" >> /home/gongshi/logs/alert.log
```

**Prometheus 指标**（可选，按需启用）：

ai-service 暴露 `/metrics` 端点（FastAPI + prometheus_client）。开发环境可通过 `docker-compose up -d prometheus grafana` 启动监控栈。生产环境如需监控，在 116 服务器单独运行 Prometheus 容器即可：

```bash
docker run -d --name prometheus --restart always \
  -p 9090:9090 \
  -v /home/gongshi/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus:v2.48.0
```

当前阶段，审计日志（MySQL `conversation_logs` 表）已覆盖核心业务指标，Prometheus 可酌情启用。

### 8.7 日志管理

**日志文件**：`/home/gongshi/logs/ai-service.log`

生产环境建议配置 logrotate 防止日志文件过大：

```bash
# /etc/logrotate.d/ai-service
/home/gongshi/logs/ai-service.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

日志级别通过 `.env` 中 `LOG_LEVEL` 控制，生产建议设置为 `INFO`，排查问题时临时改为 `DEBUG` 再 restart。

业务审计日志存入 MySQL `conversation_logs` 表，包含每次对话的意图、调用工具、耗时、状态（success / error / rejected），可直接 SQL 查询分析。

### 8.8 回滚方案

**方案：保留上一版本代码目录，回滚仅需切换目录 + restart**

```bash
# 部署新版本前，先备份当前目录
cp -r /home/gongshi/ai-service /home/gongshi/ai-service.bak

# 更新代码
cd /home/gongshi/ai-service
git pull
pip install -r fastapi-service/requirements.txt
sh /home/gongshi/ai-service.sh restart

# 验证新版本（健康检查 + 发送测试请求）
curl http://127.0.0.1:8000/api/ai/health

# 如需回滚，替换目录后 restart
sh /home/gongshi/ai-service.sh stop
rm -rf /home/gongshi/ai-service
mv /home/gongshi/ai-service.bak /home/gongshi/ai-service
sh /home/gongshi/ai-service.sh start
```

若使用 git 管理版本，也可通过 `git checkout <commit>` 回到指定版本：

```bash
cd /home/gongshi/ai-service
git log --oneline -10        # 查看历史版本
git checkout <commit-hash>   # 切换到目标版本
sh /home/gongshi/ai-service.sh restart
```
