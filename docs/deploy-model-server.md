# AI 服务模型部署指南

> 更新日期：2026-04-09
> 适用版本：ai-service v1.1+

---

## 一、服务器分工与部署位置

本系统涉及两台服务器：

| 服务器 | 角色 | 部署内容 |
|--------|------|---------|
| **116.205.174.57**（公网） | 应用服务器 | Spring Boot :8080、ai-service :8000、Redis、Milvus（Docker）、nginx |
| **172.19.3.136**（内网 GPU） | 模型推理服务器 | Ollama :11434 或 vLLM :8001、BGE Embedding :8002（可选） |

```
用户浏览器
    │ HTTPS
    ▼
116.205.174.57（公网应用服务器）
├── nginx
│     ├── /api/   → Spring Boot :8080（已有）
│     └── /api/ai/ → ai-service :8000（新增反代）
├── ai-service（systemd 进程，:8000）
├── Redis（apt 原生安装，:6379）
└── Milvus（Docker 单容器，:19530）
         │
         │ 内网 HTTP API 调用
         ▼
172.19.3.136（GPU 服务器，内网）
└── Ollama :11434 / vLLM :8001（LLM 推理）
```

**代码零改动**：所有模型调用已封装为 OpenAI 兼容接口，切换只需改 `.env`。

---

## 二、116 服务器各组件部署方式

### 2.1 Redis — apt 原生安装

```bash
apt install -y redis-server
systemctl enable redis --now
# .env 中 REDIS_HOST=127.0.0.1
```

### 2.2 Milvus — Docker 单容器（必须）

Milvus 依赖 etcd + MinIO，官方只提供 Docker 方式，无裸机安装包：

```bash
docker run -d --name milvus-standalone \
  --restart always \
  -p 19530:19530 \
  -v /data/milvus:/var/lib/milvus \
  milvusdb/milvus:v2.4.0 \
  milvus run standalone
# .env 中 MILVUS_HOST=127.0.0.1
```

### 2.3 ai-service — systemd 进程（无需 Docker）

与现有 Spring Boot 保持一致，直接用 systemd 托管：

```bash
# 在 116 上 git clone / git pull ai-service 代码
git clone <repo-url> /opt/ai-service
conda create -n workhour python=3.11
conda activate workhour
pip install -r /opt/ai-service/fastapi-service/requirements.txt

# 复制并配置 .env
cp /opt/ai-service/.env.example /opt/ai-service/.env
# 修改 CHAT_LLM_API_BASE 指向 172.19.3.136（见第五节）
```

systemd 服务文件 `/etc/systemd/system/ai-service.service`：

```ini
[Unit]
Description=AI Service (FastAPI)
After=network.target

[Service]
User=root
WorkingDirectory=/opt/ai-service/fastapi-service
ExecStart=/root/miniconda3/envs/workhour/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/opt/ai-service/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable ai-service --now
systemctl status ai-service
```

### 2.4 nginx 增加 ai-service 反代

在已有 nginx 配置的 `server {}` 块中追加：

```nginx
# SSE 流式输出，必须关闭缓冲
location /api/ai/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header Connection '';   # SSE 必须置空
    proxy_buffering off;              # SSE 必须关闭缓冲
    proxy_read_timeout 300s;
}
```

```bash
nginx -t && nginx -s reload
```

---

## 二、LLM 选型建议

### 2.1 单块 RTX 4090（24GB）推荐选型

#### Qwen 系列（首选，中文能力最强）

| 模型 | 参数量 | 显存占用 | 上下文 | Function Calling | 推荐程度 |
|------|--------|----------|--------|------------------|----------|
| **Qwen3.5-9B** | 9B (MoE) | ~18-20GB FP16 | 262K | ✅ qwen3_coder 解析器 | ★★★★★ **当前最新** |
| Qwen3-8B | 8B | ~16GB FP16 | 128K | ✅ | ★★★★★ 轻量备选 |
| Qwen3-14B (INT4) | 14B | ~8-10GB | 128K | ✅ | ★★★★ 效果更好 |
| Qwen3.5-9B (INT4) | 9B | ~5-8GB | 262K | ✅ | ★★★★ 省显存 |

> **推荐 Qwen3.5-9B 的理由：**
> - 2026年2月发布，混合 MoE 架构（Gated DeltaNet），推理效率高于同参数量密集模型
> - 原生 262K 上下文，长会话场景游刃有余
> - 原生 Function Calling，工具调用准确率高（TIR-Bench 45.6）
> - 内置"思考模式"（`<think>...</think>`），对复杂规划任务有帮助
> - ⚠ **思考模式会增加 token 消耗**，简单工具调用建议关闭（见第四节）

#### 非 Qwen 替代方案

| 模型 | 参数量 | 显存占用 | 中文 | Function Calling | 特点 |
|------|--------|----------|------|------------------|------|
| **DeepSeek-R1-Distill-Qwen-7B** | 7B | ~14GB | ★★★★★ | ✅ | 推理能力强，蒸馏自 R1 |
| **GLM-4-9B-Chat** | 9B | ~18GB | ★★★★★ | ✅ | 智谱 AI 开源，企业场景成熟 |
| Llama-3.1-8B-Instruct | 8B | ~16GB | ★★★ | ✅ | 英文生态好，中文偏弱 |
| Mistral-7B-Instruct | 7B | ~14GB | ★★ | ✅ | 不适合纯中文场景 |

> **非 Qwen 场景选择建议：**
> - 如果更看重**推理/分析能力** → `DeepSeek-R1-Distill-Qwen-7B`（基于 Qwen2.5 底座，中文好）
> - 如果更看重**企业级稳定性** → `GLM-4-9B-Chat`（智谱，国内使用广泛）
> - Llama/Mistral 中文能力较弱，工时管理系统场景**不推荐**

### 2.2 无 GPU — 继续 DashScope 云端

| 用途 | 当前模型 | 备选（更便宜）|
|------|----------|--------------|
| 主对话 | qwen-plus | qwen-turbo（便宜约 50%，效果略降）|
| 意图识别 | qwen-flash | 保持不变，已是最便宜 |

---

## 三、Embedding 模型选型

| 模型 | 维度 | 中文效果 | 部署方式 | 说明 |
|------|------|----------|----------|------|
| **BAAI/bge-large-zh-v1.5** | 1024 | ★★★★★ | 本地 CPU | **首选，中文专用，1.3GB，无需 GPU** |
| BAAI/bge-m3 | 1024 | ★★★★★ | 本地 CPU | 多语言，~2.3GB，效果略好 |
| DashScope text-embedding-v2 | 1536 | ★★★★ | API | 现用方案，付费，无需部署 |

> **推荐 bge-large-zh-v1.5**：CPU 即可运行，中文 MTEB 榜前列，完全免费。
>
> ⚠ 切换 Embedding 模型后，维度从 1536 → 1024，Milvus 需**重建索引**：
> ```bash
> # 重启 ai-service，lifespan 会自动重建
> docker-compose restart ai-service
> ```

---

## 四、方案 A：复用已有 Ollama 服务（零部署成本，推荐先试）

服务器上已有 Ollama + Qwen，可直接复用。Ollama 提供 OpenAI 兼容 API，接法与 vLLM 完全相同。

```bash
# 1. 确认 Ollama 上的模型名
ollama list
# 输出示例：
# NAME             ID              SIZE    MODIFIED
# qwen3:8b         ...             5.2 GB  ...

# 2. 仅修改 .env 三行
CHAT_LLM_API_KEY=ollama                 # 随便填，Ollama 不验证
CHAT_LLM_API_BASE=http://<服务器IP>:11434/v1
CHAT_LLM_MODEL=qwen3:8b                 # 与 ollama list 名字完全一致
```

如果想用更新的 Qwen3.5-9B，让管理员执行：
```bash
ollama pull qwen3.5:9b
```

| 对比项 | Ollama（复用） | vLLM（自建） |
|--------|--------------|-------------|
| 部署工作量 | 零 | 需配置 |
| 并发吞吐 | 中（够内部使用）| 高 |
| Function Calling | ✅ | ✅ |
| 适合场景 | **内部系统首选** | 高并发生产 |

---

## 五、方案 B：自建 vLLM 服务

Ollama 不可用，或需要更高并发、更细粒度控制时选此方案。

### 5.1 环境要求

- CUDA 11.8+ / CUDA 12.x
- Python 3.10+
- 单卡 RTX 4090（24GB）

### 5.2 安装

```bash
pip install vllm
```

### 5.3 启动 Qwen3.5-9B（推荐）

Qwen3.5-9B 需要额外参数启用 Function Calling 和思考模式解析：

```bash
# FP16，启用 Function Calling + 思考模式解析
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-9B \
  --served-model-name qwen3.5-9b \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 32768 \
  --tensor-parallel-size 1 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --gpu-memory-utilization 0.85

# 若显存紧张，加 --language-model-only 关闭多模态（省约2GB）
# 或用 INT4 量化：--quantization awq
```

> **注意**：`--tool-call-parser qwen3_coder` 是 Qwen3.5-9B 必需参数，与 Qwen3 系列的 `qwen3` 不同。

### 5.4 启动 Qwen3-8B（轻量备选）

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-8B \
  --served-model-name qwen3-8b \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3 \
  --gpu-memory-utilization 0.85
```

### 5.5 Docker 部署（推荐）

```bash
docker run -d \
  --gpus '"device=0"' \
  --name vllm-qwen \
  -p 8001:8000 \
  -v /path/to/models:/models \
  vllm/vllm-openai:latest \
  --model /models/Qwen3.5-9B \
  --served-model-name qwen3.5-9b \
  --host 0.0.0.0 \
  --port 8000 \
  --max-model-len 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --gpu-memory-utilization 0.85
```

> `--gpus '"device=0"'`：指定使用第 0 块卡，其他 3 块不受影响（服务器上共 4 卡）。

### 5.6 国内服务器下载模型

HuggingFace 在国内访问慢，建议用 ModelScope：

```bash
pip install modelscope
# Qwen3.5-9B
modelscope download --model Qwen/Qwen3.5-9B --local_dir ./models/Qwen3.5-9B
# Qwen3-8B（备选）
modelscope download --model Qwen/Qwen3-8B --local_dir ./models/Qwen3-8B
```

### 5.7 验证 vLLM 服务

```bash
curl http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-9b",
    "messages": [{"role": "user", "content": "你好"}],
    "extra_body": {"enable_thinking": false}
  }'
```

---

## 六、关闭思考模式（重要）

Qwen3.5-9B 默认开启思考模式，每次回复前生成 `<think>...</think>` 推理链，**会增加延迟和 token 消耗**。对于工时管理系统的简单工具调用，建议关闭。

在 `fastapi-service/app/services/llm_client.py` 的请求参数中追加：

```python
# 在 call() / stream() 方法的 extra_body 或 kwargs 中加入
extra_body={"enable_thinking": False}
```

或在 `.env` 中增加配置项，由 `LLMClient` 统一控制：

```bash
CHAT_LLM_ENABLE_THINKING=false
```

> **何时开启思考模式**：PlannerAgent 生成多步计划时可开启，能提升规划质量。
> 在 `node_plan_and_execute` 调用 LLM 时单独传 `enable_thinking=True`。

---

## 七、BGE Embedding 服务部署

BGE 只需 CPU，部署在任意服务器上即可。

### 7.1 embedding_server/main.py

```python
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import torch, uvicorn

app = FastAPI()
torch.set_num_threads(4)
model = SentenceTransformer("BAAI/bge-large-zh-v1.5", device="cpu")

class EmbedRequest(BaseModel):
    model: str
    input: list[str] | str

@app.post("/v1/embeddings")
def embed(req: EmbedRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    vecs = model.encode(texts, normalize_embeddings=True).tolist()
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
        "model": req.model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
```

### 7.2 Docker 部署

```dockerfile
# embedding_server/Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install sentence-transformers fastapi uvicorn
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5')"
COPY main.py .
CMD ["python", "main.py"]
```

```bash
docker build -t bge-embedding embedding_server/
docker run -d --name bge-embedding -p 8002:8002 bge-embedding
```

---

## 八、修改 .env 配置

```bash
# === 主对话 LLM ===
CHAT_LLM_API_KEY=any-string
CHAT_LLM_API_BASE=http://<GPU_IP>:8001/v1      # vLLM；或 http://<IP>:11434/v1（Ollama）
CHAT_LLM_MODEL=qwen3.5-9b                       # 与 --served-model-name 一致

# === 意图识别 LLM（可复用同一服务）===
INTENT_LLM_API_KEY=any-string
INTENT_LLM_API_BASE=http://<GPU_IP>:8001/v1
INTENT_LLM_MODEL=qwen3.5-9b

# === Embedding（本地 BGE）===
EMBEDDING_API_KEY=any-string
EMBEDDING_API_BASE=http://<EMBED_IP>:8002/v1
EMBEDDING_MODEL=bge-large-zh-v1.5
```

### 8.1 config.py 补充 Embedding 独立配置

```python
# fastapi-service/app/core/config.py — Settings 类中追加
EMBEDDING_API_KEY: str = ""
EMBEDDING_API_BASE: str = ""        # 空则复用 CHAT_LLM_API_BASE
EMBEDDING_MODEL: str = "text-embedding-v2"
```

`langchain_rag.py` 中使用：

```python
embed_base = settings.EMBEDDING_API_BASE or settings.CHAT_LLM_API_BASE
embed_key  = settings.EMBEDDING_API_KEY  or settings.CHAT_LLM_API_KEY
self.embeddings = OpenAIEmbeddings(
    model=settings.EMBEDDING_MODEL,
    openai_api_key=embed_key,
    openai_api_base=embed_base,
    chunk_size=20,
    check_embedding_ctx_length=False,
)
```

---

## 九、部署验证清单

```bash
# 1. LLM 服务健康检查
curl http://<GPU_IP>:8001/health                    # vLLM
curl http://<GPU_IP>:11434/api/tags                 # Ollama

# 2. Function Calling 验证
curl http://<GPU_IP>:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5-9b","messages":[{"role":"user","content":"查我本周工时"}],
       "tools":[{"type":"function","function":{"name":"query_timesheet","description":"查询工时","parameters":{"type":"object","properties":{}}}}],
       "extra_body":{"enable_thinking":false}}'

# 3. Embedding 服务验证
curl http://<EMBED_IP>:8002/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-large-zh-v1.5","input":["测试文本"]}'

# 4. 启动 ai-service（Milvus 自动重建索引）
docker-compose up -d ai-service

# 5. 对话功能验证
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 1" -H "X-Entity-Type: employee" \
  -d '{"message":"查一下我本周的工时","session_id":"deploy-test-001"}'

# 6. RAG 验证
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 1" -H "X-Entity-Type: employee" \
  -d '{"message":"工时填报截止日期是几号","session_id":"deploy-test-002"}'

# 7. Layer3 集成测试
cd fastapi-service && pytest tests/test_layer3_integration.py -v
```

---

## 十、性能调优

### vLLM 参数

```bash
--max-num-seqs 32              # 并发请求数（4090 建议 32-64）
--gpu-memory-utilization 0.85  # 显存利用率（如 OOM 降到 0.8）
--enable-prefix-caching        # System Prompt 复用缓存，减少重复计算
```

### Prometheus 监控接入

vLLM 自带 `/metrics` 端点，接入已有 Grafana：

```yaml
# prometheus.yml 追加
- job_name: 'vllm'
  static_configs:
    - targets: ['<GPU_IP>:8001']
```

---

## 十一、方案速查

```
服务器上已有 Ollama？
  ├── 是 → 方案 A：直接复用，改 3 行 .env，10 分钟上线
  │          ollama list 确认模型名 → 修改 .env → 重启 ai-service
  │
  └── 否 → 方案 B：自建 vLLM
             单卡 RTX 4090 → Qwen3.5-9B（首选）或 Qwen3-8B（轻量）
             docker run --gpus '"device=0"' vllm/vllm-openai ...

Embedding：
  继续 DashScope → 无需改动
  切换本地 BGE  → 部署 embedding_server，修改 .env，重启后 Milvus 自动重建
```

---

## 十三、本地测试联通服务器大模型（第一步验证）

在正式部署 ai-service 到 116 之前，先在**本地开发机**验证能否成功调用服务器上的 LLM。

### 13.1 确认 Ollama 对外可访问

Ollama 默认只监听 `127.0.0.1`，需要让管理员确认已开放外网/内网访问：

```bash
# 在 172.19.3.136 上确认监听地址
ss -tlnp | grep 11434
# 期望看到：0.0.0.0:11434 或 :::11434
# 若只有 127.0.0.1:11434，需修改配置后重启 Ollama
```

若需要修改（管理员操作）：
```bash
# systemd 方式
sudo systemctl edit ollama
# 追加：
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"

sudo systemctl restart ollama
```

### 13.2 本地修改 .env 指向服务器

在本地 `ai-service/.env`（或 `.env.local`）中修改：

```bash
CHAT_LLM_API_KEY=ollama
CHAT_LLM_API_BASE=http://172.19.3.136:11434/v1
CHAT_LLM_MODEL=qwen3:8b    # 改成 ollama list 返回的实际名字
```

> 若本地无法直接访问 `172.19.3.136`（内网隔离），需先通过 VPN 或跳板机建立连接。

### 13.3 用 curl 直接测试（不启动 ai-service）

```bash
# 列出可用模型
curl http://172.19.3.136:11434/api/tags

# 发送对话请求（OpenAI 兼容格式）
curl http://172.19.3.136:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "你好，介绍一下你自己"}],
    "stream": false
  }'
```

返回类似以下内容即表示联通：
```json
{"choices": [{"message": {"role": "assistant", "content": "你好！我是..."}}]}
```

### 13.4 启动本地 ai-service 测试完整链路

```bash
conda activate workhour
cd fastapi-service
python main.py   # 读取 .env，LLM 指向服务器

# 另开终端测试
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 1" \
  -H "X-Entity-Type: employee" \
  -d '{"message": "你好", "session_id": "local-test-001"}'
```

### 13.5 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `Connection refused` | Ollama 未监听 0.0.0.0 | 让管理员修改 `OLLAMA_HOST` |
| `Connection timed out` | 防火墙屏蔽 11434 端口 | 开放端口：`ufw allow 11434` |
| 模型名不匹配 | `.env` 中 model 名与 `ollama list` 不符 | 以 `ollama list` 输出为准 |
| 响应极慢 | 思考模式开启，生成大量 token | 在请求中加 `"extra_body": {"enable_thinking": false}` |
