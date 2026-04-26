# 模型服务器部署文档

> 本文档仅记录 **LLM 推理服务器（172.19.3.136）** 的详细操作命令。
> 完整部署指南（开发/测试/生产环境、ai-service 部署、nginx 配置等）请见 [deployment.md](deployment.md)。

## 服务器信息

| 项目 | 值 |
|------|-----|
| 内网 IP | 172.19.3.136 |
| 公网 IP | 116.205.174.57 |
| SSH 用户 | caic |

---

## 一、当前部署架构

### 1.1 服务总览

| 端口 | 服务 | 模型 | GPU | 量化 | 备注 |
|------|------|------|-----|------|------|
| 8099 | **vLLM** | **qwen3-8b** | GPU 2 | Q4_K_M | **主力对话服务** |
| 8098 | vLLM | bge-base-zh-v1.5 | GPU 3 | FP32 | Embedding 服务 |
| 8097 | vLLM | bge-large-zh-v1.5 | GPU 3 | FP32 | Embedding 服务(大模型) |
| 11434 | Ollama | qwen3-vl:8b 等 | GPU 1+3 | Q4_K_M | 备用/其他模型 |

### 1.2 GPU 资源分配

```
nvidia-smi
```

| GPU | 显存占用 | 主要进程 |
|-----|----------|----------|
| GPU 0 | ~22GB | Python 多进程 / Celery |
| GPU 1 | ~12GB | Ollama (qwen3:32b 等) |
| GPU 2 | ~24GB | vLLM qwen3 |
| GPU 3 | ~11GB | vLLM bge-base + bge-large + Ollama |

### 1.3 模型量化信息

| 模型 | 量化级别 | 说明 |
|------|----------|------|
| Ollama qwen3 系列 | Q4_K_M | GGUF 格式，默认量化 |
| vLLM qwen3 | Q4_K_M | 模型文件为 GGUF Q4_K_M |
| bge-base-zh-v1.5 | FP32 | PyTorch 原生格式，1.2GB |
| bge-large-zh-v1.5 | FP32 | PyTorch 原生格式，1.3GB |

---

## 二、Ollama 服务

### 2.1 启动命令

```bash
docker run -d \
  --name ollama \
  --restart always \
  --gpus '"device=1,3"' \
  -p 11434:11434 \
  -v ollama:/root/.ollama \
  -e OLLAMA_NUM_PARALLEL=4 \
  -e OLLAMA_MAX_LOADED_MODELS=2 \
  -e OLLAMA_FLASH_ATTENTION=1 \
  ollama/ollama:latest
```

### 2.2 已部署模型

```bash
curl http://172.19.3.136:11434/api/tags
```

| 模型名 | 大小 | 量化 | 用途 |
|--------|------|------|------|
| qwen3-vl:8b | 6.0GB | Q4_K_M | 视觉对话 |
| qwen3:8b | 5.2GB | Q4_K_M | 文本对话 |
| qwen3:4b | 2.5GB | Q4_K_M | 轻量对话 |
| qwen3:1.7b | 1.4GB | Q4_K_M | 超轻量对话 |
| qwen3:14b | 9.3GB | Q4_K_M | 中等规模对话 |
| qwen3:32b | 20.2GB | Q4_K_M | 高质量对话 |
| qwen3-embedding:8b | 4.7GB | Q4_K_M | 文本嵌入 |
| qwen2.5vl:7b | 6.0GB | Q4_K_M | 视觉对话(旧版) |

### 2.3 下载新模型

```bash
docker exec ollama ollama pull qwen3-vl:8b
docker exec ollama ollama list
```

---

## 三、vLLM 服务

### 3.1 vLLM qwen3 (端口 8099)

主力对话服务，使用 GPU 2，支持 Function Calling。

```bash
docker stop vllm-qwen3-8b 2>/dev/null || true
docker rm vllm-qwen3-8b 2>/dev/null || true

docker run -d \
  --name vllm-qwen3-8b \
  --restart always \
  --gpus '"device=2"' \
  --ipc=host \
  -p 8099:8099 \
  -v /mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-8B:/model:ro \
  -e VLLM_LOGGING_LEVEL=INFO \
  vllm-qwen3:latest-cu122 \
  python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 \
  --port 8099 \
  --model /model \
  --served-model-name qwen3-8b \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --max-num-seqs 8 \
  --max-num-batched-tokens 4096 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

**参数说明**：
- `--gpus '"device=2"'` — 指定使用 GPU 2
- `--ipc=host` — 更好的进程间通信性能
- `--served-model-name qwen3-8b` — API 暴露的模型名
- `--gpu-memory-utilization 0.90` — 90% 显存
- `--enable-auto-tool-choice` — 启用自动工具选择
- `--tool-call-parser hermes` — Function Calling 解析器

### 3.2 vLLM bge-base-zh-v1.5 (端口 8098)

```bash
docker run -d \
  --name vllm-bge \
  --restart always \
  -p 8098:8099 \
  -v /home/stone/dockerfile/anju-ai/anju-python-ai-prod/public/model/BGE-base-zh-v1.5:/model \
  -e NVIDIA_VISIBLE_DEVICES=3 \
  vllm-qwen3:latest-cu122 \
  python -m vllm.entrypoints.openai.api_server \
  --model /model \
  --port 8099 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.85
```

### 3.3 vLLM bge-large-zh-v1.5 (端口 8097)

```bash
docker run -d \
  --name vllm-bge-large \
  --restart always \
  -p 8097:8099 \
  -v /home/stone/dockerfile/anju-ai/anju-python-ai-prod/public/model/bge-large-zh-v1.5:/model \
  -e NVIDIA_VISIBLE_DEVICES=3 \
  vllm-qwen3:latest-cu122 \
  python -m vllm.entrypoints.openai.api_server \
  --model /model \
  --port 8099 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.85
```

---

## 四、压测参数对比

### 4.1 第一组（偏稳）

```bash
--max-num-seqs 8
--max-num-batched-tokens 4096
```

### 4.2 第二组（偏吞吐）

```bash
--max-num-seqs 16
--max-num-batched-tokens 8192
```

### 4.3 压测指标

| 指标 | 说明 |
|------|------|
| 首 token 时间 | 首次生成的时间 |
| 总生成速度 | tokens/second |
| 并发 4 请求耗时 | 平均响应时间 |
| GPU 利用率 | nvidia-smi 监控 |

### 4.4 压测脚本

```bash
cat > /tmp/vllm_test.py << 'EOF'
import requests
import time
import threading

def test_single_request():
    start = time.time()
    r = requests.post("http://localhost:8099/v1/chat/completions", 
        json={"model":"/model","messages":[{"role":"user","content":"写一个Python快速排序"}],"max_tokens":200})
    first_token = time.time() - start
    total = r.json()
    print(f"首token时间: {first_token:.3f}s")

def test_concurrent(n=4):
    threads = []
    start = time.time()
    for _ in range(n):
        t = threading.Thread(target=lambda: requests.post("http://localhost:8099/v1/chat/completions", 
            json={"model":"/model","messages":[{"role":"user","content":"你好"}],"max_tokens":50}))
        t.start()
        threads.append(t)
    for t in threads: t.join()
    print(f"并发{n}请求总耗时: {time.time()-start:.3f}s")

test_single_request()
test_concurrent(4)
EOF

python3 /tmp/vllm_test.py
```

---

## 五、API 端点

### 5.1 Ollama

```bash
# 列出模型
curl http://172.19.3.136:11434/api/tags

# 对话
curl http://172.19.3.136:11434/v1/chat/completions

# 嵌入
curl http://172.19.3.136:11434/v1/embeddings
```

### 5.2 vLLM

```bash
# 主对话 qwen3（端口 8099）
curl http://172.19.3.136:8099/v1/chat/completions

# bge-base 嵌入（端口 8098）
curl http://172.19.3.136:8098/v1/embeddings

# bge-large 嵌入（端口 8097）
curl http://172.19.3.136:8097/v1/embeddings
```

---

## 六、修改 .env 配置

```bash
# === 主对话 LLM (vLLM qwen3) ===
CHAT_LLM_API_KEY=any-string
CHAT_LLM_API_BASE=http://172.19.3.136:8099/v1
CHAT_LLM_MODEL=qwen3-8b

# === Embedding (bge-large) ===
EMBEDDING_API_BASE=http://172.19.3.136:8097/v1
EMBEDDING_MODEL=bge-large-zh-v1.5
```

---

## 七、常用命令

```bash
# 查看服务状态
docker ps | grep -E "ollama|vllm"

# 查看 GPU 状态
nvidia-smi

# 查看容器日志
docker logs ollama --tail 50
docker logs vllm-qwen3-8b --tail 50

# 重启服务
docker restart ollama
docker restart vllm-qwen3-8b
docker restart vllm-bge
docker restart vllm-bge-large

# 下载 HuggingFace 模型（需要代理）
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download baai/bge-large-zh-v1.5 --local-dir /path/to/model
```
