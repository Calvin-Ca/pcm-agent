# 生产环境部署指南

本文档覆盖 ai-service 在各环境（开发、测试、生产）的实际部署配置和运维操作。
项目架构图、请求处理流程、RAG 流程等见 [deployment.md](deployment.md)。
LLM 推理服务器（vLLM / Ollama）的详细操作命令见 [deploy-model-server.md](deploy-model-server.md)。

---

## 一、架构总览

### 服务器分工

| 服务器 | 角色 |
|--------|------|
| 116.205.174.57 | 公网应用服务器（前端、Spring Boot、ai-service、Redis、Milvus） |
| 172.19.3.136 | 内网 GPU 服务器（vLLM、Ollama，4x RTX 4090） |
| 192.168.0.94 | 数据库服务器（MySQL 3306，库名 workhour） |

### 网络拓扑

```
互联网
    |
    v
gst.thsware.com (116.205.174.57)
    |-- nginx (80/443, /usr/local/nginx)
    |     |-- /api/ai/  -->  ai-service (127.0.0.1:8000, 端口待确认)
    |     |-- /api/ /thsuaa/ /thsadmin/  -->  Spring Boot (127.0.0.1:9900)
    |
    |-- Spring Boot (9900, WAR, nohup 启动)
    |-- ai-service (8000, 待部署, nohup 启动)
    |-- Redis (6379, apt 安装)
    |-- Milvus (19530, Docker 单容器)
         |
         +--> HTTP 调用 Spring Boot 业务接口 (127.0.0.1:9900)
         |
         +--> MySQL (192.168.0.94:3306, 库名 workhour)
         |
         +--> LLM 推理 (172.19.3.136, 内网)
                |-- vLLM qwen3-8b  (8099)  主力对话 LLM
                |-- vLLM bge-large (8097)  Embedding
                `-- Ollama         (11434) 备用
```

### 端口速查

| 服务器 | 端口 | 服务 |
|--------|------|------|
| 116.205.174.57 | 80/443 | nginx (公网入口) |
| 116.205.174.57 | 9900 | Spring Boot (内网) |
| 116.205.174.57 | 8000 | ai-service (内网，端口待确认) |
| 116.205.174.57 | 6379 | Redis |
| 116.205.174.57 | 19530 | Milvus |
| 192.168.0.94 | 3306 | MySQL |
| 172.19.3.136 | 8099 | vLLM qwen3-8b |
| 172.19.3.136 | 8097 | vLLM bge-large-zh-v1.5 |
| 172.19.3.136 | 8098 | vLLM bge-base-zh-v1.5 |
| 172.19.3.136 | 11434 | Ollama |

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

生产服务器：116.205.174.57，域名 gst.thsware.com（已有 SSL 证书）。

ai-service 与 Spring Boot 部署方式保持一致，使用 `nohup + 管理脚本` 方式运行，不使用 Docker 运行 ai-service 本身。

### 4.1 安装 Redis

```bash
apt install -y redis-server
systemctl enable redis --now
systemctl status redis
```

### 4.2 部署 Milvus（Docker 单容器）

```bash
docker run -d \
  --name milvus-standalone \
  --restart always \
  -p 19530:19530 \
  -v /data/milvus:/var/lib/milvus \
  milvusdb/milvus:v2.3.3 milvus run standalone
```

### 4.3 部署 ai-service 代码

```bash
# 拉取代码
git clone <repo-url> /home/gongshi/ai-service
cd /home/gongshi/ai-service

# 创建 Python 环境
conda create -n workhour python=3.11 -y
conda activate workhour
pip install -r fastapi-service/requirements.txt
```

### 4.4 配置 .env

```bash
cp .env.example /home/gongshi/ai-service/.env
vi /home/gongshi/ai-service/.env
```

生产环境 `.env` 关键配置：

```bash
# === 意图识别 LLM ===
INTENT_LLM_API_KEY=ollama
INTENT_LLM_API_BASE=http://172.19.3.136:8099/v1
INTENT_LLM_MODEL=qwen3-8b

# === 主对话 LLM ===
CHAT_LLM_API_KEY=ollama
CHAT_LLM_API_BASE=http://172.19.3.136:8099/v1
CHAT_LLM_MODEL=qwen3-8b

# === RAG Embedding（DashScope，不可省略） ===
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# === MySQL（数据库服务器） ===
MYSQL_HOST=192.168.0.94
MYSQL_PORT=3306
MYSQL_DATABASE=workhour
MYSQL_USER=yunzuku
MYSQL_PASSWORD=yunzuku2021

# === Redis（本机） ===
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# === Milvus（本机 Docker） ===
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530

# === Spring Boot 后端（同机内网） ===
SPRINGBOOT_BASE_URL=http://127.0.0.1:9900

# === MinIO 密码（如通过 docker-compose 启动 Milvus 时填写） ===
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=<自定义强密码>

# === 日志 ===
LOG_LEVEL=INFO
```

### 4.5 创建启动管理脚本

在 `/home/gongshi/` 下创建 `ai-service.sh`（与 `gongshi-ht.sh` 风格一致）：

```bash
#!/bin/bash
APP_DIR=/home/gongshi/ai-service/fastapi-service
LOG_DIR=/home/gongshi/logs
PID_FILE=/home/gongshi/ai-service.pid
CONDA_ENV=workhour

case "$1" in
  start)
    source $(conda info --base)/etc/profile.d/conda.sh
    conda activate $CONDA_ENV
    mkdir -p $LOG_DIR
    cd $APP_DIR
    nohup uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2 \
      >> $LOG_DIR/ai-service.log 2>&1 &
    echo $! > $PID_FILE
    echo "ai-service 已启动，PID=$(cat $PID_FILE)"
    ;;
  stop)
    if [ -f $PID_FILE ]; then
      kill $(cat $PID_FILE) && rm $PID_FILE
      echo "ai-service 已停止"
    else
      echo "ai-service 未运行（PID 文件不存在）"
    fi
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    if [ -f $PID_FILE ] && kill -0 $(cat $PID_FILE) 2>/dev/null; then
      echo "ai-service 运行中，PID=$(cat $PID_FILE)"
    else
      echo "ai-service 未运行"
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status}"
    ;;
esac
```

```bash
chmod +x /home/gongshi/ai-service.sh
sh /home/gongshi/ai-service.sh start
sh /home/gongshi/ai-service.sh status
```

### 4.6 配置 nginx

编辑 `/usr/local/nginx/conf/nginx.conf`，在 `gst.thsware.com` server 块内，**在 `location /api/` 前面**插入 AI 接口专用 location（SSE 长连接需禁用缓冲）：

```nginx
location /api/ai/ {
    proxy_pass http://127.0.0.1:8000/api/ai/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

重载 nginx：

```bash
/usr/local/nginx/sbin/nginx -t
/usr/local/nginx/sbin/nginx -s reload
```

> ai-service 监听 `127.0.0.1:8000`，不对外暴露，仅 nginx 内部转发。实际端口以部署时确认为准。

### 4.7 初始化知识库索引

首次部署后需构建 Milvus 向量索引。修改 `knowledge-base/*.md` 后也需重建。

```bash
curl -X POST http://127.0.0.1:8000/api/rag/reload
```

或：

```bash
conda activate workhour
cd /home/gongshi/ai-service/fastapi-service
python -c "from app.services.langchain_rag import build_index; build_index()"
```

### 4.8 健康检查

```bash
curl http://127.0.0.1:8000/api/ai/health
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

### ai-service（生产）

```bash
# 启停
sh /home/gongshi/ai-service.sh start
sh /home/gongshi/ai-service.sh stop
sh /home/gongshi/ai-service.sh restart
sh /home/gongshi/ai-service.sh status

# 实时日志
tail -f /home/gongshi/logs/ai-service.log

# 健康检查
curl http://127.0.0.1:8000/api/ai/health
```

### ai-service（开发，Docker）

```bash
# 查看服务状态
docker-compose ps

# 重启 ai-service
docker-compose restart ai-service

# 实时日志
docker-compose logs -f ai-service

# 进入容器调试
docker-compose exec ai-service bash
```

### Milvus（生产）

```bash
# 查看容器状态
docker ps | grep milvus

# 查看日志
docker logs milvus-standalone --tail 50

# 重启
docker restart milvus-standalone
```

### Redis

```bash
# 检查连接
redis-cli ping

# 查看 key 数量
redis-cli dbsize

# 清空会话缓存（谨慎）
redis-cli flushdb
```

### nginx

```bash
# 测试配置
/usr/local/nginx/sbin/nginx -t

# 重载配置（不中断连接）
/usr/local/nginx/sbin/nginx -s reload

# 查看错误日志
tail -f /usr/local/nginx/logs/error.log
```

### Spring Boot（参考）

```bash
sh /home/gongshi/gongshi-ht.sh status
sh /home/gongshi/gongshi-ht.sh restart
```

### 升级 ai-service（生产）

```bash
cd /home/gongshi/ai-service
git pull
conda activate workhour
pip install -r fastapi-service/requirements.txt   # 如有新依赖
sh /home/gongshi/ai-service.sh restart
```

---

## 八、部署建议与方案

### 8.1 推荐部署方式：conda 裸跑 vs Docker 容器化

| 维度 | conda 裸跑 | Docker 容器化 |
|------|-----------|--------------|
| 部署复杂度 | 低，与 Spring Boot 同一套运维习惯 | 中，需额外维护镜像构建 |
| 环境隔离 | 靠 conda 虚拟环境，有限 | 完整隔离，依赖不互相污染 |
| 启动速度 | 快（直接 nohup） | 稍慢（需拉取/构建镜像） |
| 热更新 | 直接 git pull + restart | 需重新构建镜像 |
| 资源开销 | 低 | 略高（容器层开销） |
| 与现有运维对齐 | 高（和 Spring Boot 同风格） | 低 |

**推荐：当前阶段使用 conda 裸跑**，理由：
- 116 服务器已有 conda + Spring Boot 的运维流程，复用成本最低
- ai-service 依赖（Redis、Milvus）单独用 Docker 管理，ai-service 本身无需容器化
- 团队规模小，维护 Docker 镜像构建流程收益有限
- 未来如有多机部署需求，可再切换到容器化

### 8.2 资源需求评估

ai-service 本身不运行任何 LLM（LLM 推理全部在 172.19.3.136），主要消耗：

| 资源 | 空闲 | 单请求峰值 | 说明 |
|------|------|-----------|------|
| CPU | < 0.1 核 | 0.2~0.5 核 | 主要是 FastAPI 路由、JSON 序列化、BM25 检索 |
| 内存 | ~300 MB | ~600 MB | LangGraph 状态、BM25 索引（约 200 MB）、CrossEncoder Reranker 模型（约 200 MB）、会话缓存 |
| 网络 | 低 | 取决于 LLM 响应速度 | SSE 长连接，每请求持续 5~30 秒 |

**建议预留**：1 核 CPU、1 GB 内存。116 服务器同时运行 Spring Boot（约 500 MB），需确认宿主机总内存充足（建议 8 GB 以上）。

### 8.3 部署位置建议

**推荐：部署在 116 服务器（与 Spring Boot 同机）**，理由：

- ai-service 频繁调用 Spring Boot 内部接口（工时查询、项目查询等），同机通信走 `127.0.0.1`，延迟 < 1 ms，避免跨机器网络开销
- Milvus、Redis 均已在 116 部署，同机访问无网络跳转
- nginx 在同机，SSE 代理最简单，无需额外网络配置
- 唯一的跨机器调用是 vLLM（172.19.3.136），这是合理的（GPU 机器独立管理）

如果未来 116 服务器资源不足，可将 ai-service 迁移到独立机器，届时调整 `SPRINGBOOT_BASE_URL` 和 nginx 代理地址即可。

### 8.4 高可用建议

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

### 8.5 依赖服务连通性

```
ai-service (116:8000)
    |
    +--[127.0.0.1:9900]--> Spring Boot       同机内网，延迟极低
    |
    +--[127.0.0.1:6379]--> Redis             同机内网，延迟极低
    |
    +--[127.0.0.1:19530]-> Milvus (Docker)   同机内网，延迟极低
    |
    +--[192.168.0.94:3306]-> MySQL           跨机内网，延迟 < 1 ms
    |
    +--[172.19.3.136:8099]-> vLLM qwen3-8b  跨机内网，延迟 < 5 ms
    |                                        （LLM 推理本身 5~30 秒）
    +--[172.19.3.136:8097]-> vLLM bge-large  Embedding，每次 RAG 检索调用
    |
    +--[DashScope HTTPS]---> 阿里云           RAG Embedding（外网）
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
