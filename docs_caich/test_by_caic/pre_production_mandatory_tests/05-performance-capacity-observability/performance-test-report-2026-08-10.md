# Workhour Agent 性能测试与优化报告

> 测试日期：2026-08-10
> 测试对象：`workhour_agent` FastAPI AI 服务、RAG、Milvus、Redis、本地 vLLM 与 DashScope 托管模型
> 测试服务器：`172.19.3.136`
> 报告状态：性能专项测试完成；完整生产放行验收未完成

## 1. 执行摘要

本轮工作完成了测试环境恢复、RAG 链路优化、生产 Worker 配置、本地 vLLM 压测、托管 API Key 模式切换和同口径复测。

主要结论如下：

1. RAG 检索本身不是主要瓶颈。Milvus/混合检索通常只需要 `0.03～0.09 s`，主要耗时来自答案生成。
2. 本地 `qwen3-8b` 在低并发下延迟较低，但 RAG 并发达到 16 后出现明显排队；并发 32 时 RAG P95 E2E 达到 `18.515 s`，吞吐仅 `1.572 RPS`。
3. 切换为 DashScope `qwen3.5-plus` 后，单请求和低并发首包变慢，但高并发 RAG 容量明显提高；并发 32 时 P95 E2E 降至 `7.648 s`，吞吐提高至 `4.062 RPS`。
4. 普通聊天不适合全部切换托管 API。本地模型在本次测试的所有聊天并发档位均更快或吞吐更高。
5. 对约 500 名员工的内部系统，当前 API 模式基本达到“持续约 3 RPS、突发约 5 RPS、RAG P95 小于 10 秒”的最低容量目标，但只验证到并发 32，尚未完成 50 并发、30 分钟持续负载和 API 模式混合流量测试。
6. 测试期间 AI 请求均正常完成，未观察到 `<think>` 泄漏、LLM 超时、API 限流、OOM 或容器重启。
7. MySQL `172.29.0.1` 仍然连接失败。AI/RAG 响应可降级完成，但会话审计和完整业务链路不满足生产验收条件。

综合建议：普通聊天和轻量意图识别继续使用本地 `qwen3-8b`，RAG 答案生成和复杂规划使用托管 API。当前服务器暂时保留全 API 模式，便于后续完成 50 并发和稳定性测试。

## 2. 测试范围

### 2.1 已覆盖

- Windows 客户端到测试服务器的网络与 SSH 连通性。
- 生产 Docker 容器、端口、健康状态和启动方式。
- Redis 恢复和项目 Milvus 恢复。
- Milvus、MinIO、etcd 依赖检查。
- FastAPI 多 Worker 启动。
- 普通聊天 SSE 请求。
- RAG one-shot SSE 请求。
- 用户可见有效 TTFT、E2E、成功率和吞吐量。
- 本地 `qwen3-8b` 基线及并发阶梯测试。
- 本地聊天与 RAG 混合并发测试。
- DashScope `qwen3.5-plus` API Key 模式基线及并发阶梯测试。
- 压测后的容器健康、重启次数、LLM 队列、限流和异常日志检查。

### 2.2 未覆盖

- 写工具、单工具、批量工时、多工具复杂任务的性能。
- SQL Agent 性能。
- SpringBoot 与真实 MySQL 的完整业务链路性能。
- API 模式下的聊天/RAG 混合流量。
- 50、64、100 并发下的 API 模式容量。
- 30 分钟容量测试和 4～8 小时稳定性测试。
- Token P50/P95、托管 API 单请求成本和日预算。
- 答案质量、事实正确率和 RAG 忠实度；本报告只验证响应协议与性能，不替代 RAG 质量评测。

## 3. 测试环境

### 3.1 代码与主机

| 项目 | 值 |
|---|---|
| 本地工作区基线提交 | `18f9475`，另有本轮未提交改动 |
| 远端仓库基线提交 | `f9a0ddc`，另有本轮部署改动 |
| 服务器 | `172.19.3.136` |
| SSH 用户 | `caic` |
| GPU | 4 × NVIDIA GeForce RTX 4090，单卡 24564 MiB |
| AI 服务镜像 | `workhour_agent-ai-service` |
| AI 服务端口 | `8000` |
| MCP Gateway 端口 | `8765` |
| Chat/RAG 本地模型端口 | `8099` |
| Embedding 模型端口 | `8097` |
| Milvus 端口 | 宿主机 `29530/19091`，容器内 `19530/9091` |
| Redis 端口 | 宿主机 `16379`，容器内 `6379` |

由于本地和远端均包含未提交改动，本报告结果不能仅以 Git 提交号复现。复测时还必须保留第 5 节列出的配置和代码变更。

### 3.2 模型与后端

| 层 | 本地模式 | API Key 模式 |
|---|---|---|
| CHAT | `qwen3-8b`，`http://172.19.3.136:8099/v1` | `qwen3.5-plus`，DashScope Compatible API |
| INTENT | `qwen3-8b`，`http://172.19.3.136:8099/v1` | `qwen3.5-plus`，DashScope Compatible API |
| PLANNER | `qwen3.5-plus`，DashScope Compatible API | 不变 |
| Embedding | 本地 `bge-large-zh-v1.5` | 不变 |
| Vector Store | 项目 Milvus | 不变 |

API Key 始终保存在远端 `.env`。Compose 只通过变量引用复用 `PLANNER_LLM_API_KEY`，报告、命令输出和代码均未记录密钥值。

### 3.3 生产进程配置

AI 服务最终使用：

```text
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
```

已去掉 `--reload`。AI 服务资源上限为约 `1.5 CPU / 4 GiB memory`；压测结束后容器保持 `running, healthy, restarts=0`。

需要注意：当前 Compose 叠加配置仍继承基础文件中的 `/app` 源码 bind mount，启动日志中的 Environment 也仍为 `development`。因此本次环境使用了生产 Worker 启动方式，但还不是完全不可变镜像意义上的生产部署。上线前应显式移除源码挂载，并将运行环境标识切换为 production。

## 4. 前置连通性与环境检查

### 4.1 网络检查

Windows 客户端到服务器的 ICMP 正常：

```powershell
ping 172.19.3.136
```

结果为 4/4 成功、0% 丢包、往返时间小于 1 ms。

以下端口 TCP 检查均成功：

```powershell
Test-NetConnection 172.19.3.136 -Port 22
Test-NetConnection 172.19.3.136 -Port 8000
Test-NetConnection 172.19.3.136 -Port 8099
Test-NetConnection 172.19.3.136 -Port 8765
```

### 4.2 SSH 问题与处理

VS Code Remote-SSH 最初在建立连接后立即收到：

```text
Connection closed by 172.19.3.136 port 22
Failed to parse remote port from server output
```

当时 Windows `Test-NetConnection` 显示连接通过 Clash 接口，源地址为 `198.18.0.1`。后续使用物理网卡地址显式绑定源地址后，直接 SSH 可稳定连接：

```powershell
ssh -i C:\Users\Administrator\.ssh\id_ed25519 `
  -b 172.19.2.176 caic@172.19.3.136
```

该现象说明客户端路由/代理路径是重要影响因素，但本轮未对 Clash 与 VS Code Remote-SSH 的内部转发行为做完整根因验证，因此不将其写成已完全证明的唯一根因。

另有两次命令使用错误：

- `New-NetFirewallRule` 是 Windows PowerShell 命令，不能在 Linux Bash 中执行。
- 从网页复制的 `|<br/>` 被 Bash 解释成文件路径，导致 `bash: br/: No such file or directory`。正确写法应为普通管道 `|`，不能包含 HTML 标签。

### 4.3 Docker 容器确认

测试目标容器包括：

```text
ai-assistant-service       workhour_agent-ai-service
ai-assistant-mcp-gateway   workhour_agent-mcp-gateway
vllm-qwen3-8b              vllm-qwen3:latest-cu122
```

`caic` 用户同时存在不同 Docker 上下文的可能性。本轮固定使用系统 Docker Socket，避免误查 rootless Docker：

```bash
docker -H unix:///var/run/docker.sock ps
```

## 5. 测试前修复与优化

### 5.1 Redis、Milvus 与 MinIO

完成以下处理：

1. 恢复项目 Redis 容器，并确认 AI 服务通过容器网络使用 `redis:6379`。
2. 恢复 etcd、MinIO 和项目 Milvus。
3. 将 Milvus 的 MinIO 环境变量修正为：

```yaml
MINIO_ACCESS_KEY_ID: ${MINIO_ROOT_USER}
MINIO_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}
```

4. AI 服务固定使用项目 Milvus `milvus:19530`，不再依赖临时 FAISS 作为主后端。
5. 为多 Worker 同时初始化 Milvus 增加进程间初始化锁和复用逻辑，避免两个 Worker 同时重建 collection。

启动日志确认：

```text
Milvus collection initialized by another worker; reusing knowledge_base
LangChain RAG 服务初始化完成（向量后端: Milvus，文档块: 849）
```

### 5.2 生产 Worker 配置

将 AI 服务从开发启动方式：

```text
uvicorn ... --reload
```

调整为生产方式：

```text
uvicorn ... --workers 2
```

只重建 AI 服务，不重启 Redis、Milvus 和 vLLM。

### 5.3 RAG 链路优化

本轮定位到旧链路存在重复生成和冗余规划，完成以下优化：

- 消除 RAG 非流式生成完成后又执行一次流式生成的问题。
- `knowledge_qa` 使用直接快速路径，减少不必要的 Planner 调用。
- 为 Qwen3 本地 vLLM 使用正确的 `chat_template_kwargs.enable_thinking=false`。
- 为 DashScope Compatible API 使用对应的 `enable_thinking=false` 参数。
- INTENT 最大输出限制为 128 tokens。
- RAG 最大输出限制为 800 tokens。
- 增加 Planner 可用性保护。
- 性能脚本把任何用户可见 `<think>` 判定为失败。

单次 RAG 优化前后观察值：

| 阶段 | TTFT | E2E |
|---|---:|---:|
| 优化前 | 14.945 s | 23.683 s |
| 优化后代表值 | 0.642 s | 4.151 s |

该对比用于证明重复生成和冗余规划已消除，不作为最终容量基线；最终容量数据以第 8、9 节的重复测试为准。

### 5.4 代码验证

本轮相关回归测试共 61 项通过：

```text
24 passed
37 passed
```

涉及的主要文件：

- `docker-compose.yml`
- `docker-compose.prod.yml`
- `fastapi-service/app/services/langgraph_agent.py`
- `fastapi-service/app/services/langchain_rag.py`
- `fastapi-service/app/services/intent_router.py`
- `fastapi-service/tests/test_knowledge_qa_rag_strategy.py`
- `fastapi-service/tests/performance/benchmark_live.py`

## 6. 测试工具与口径

### 6.1 测试脚本

使用：

```text
fastapi-service/tests/performance/benchmark_live.py
```

远端测试副本：

```text
/tmp/benchmark_live.py
```

脚本通过真实 `/api/ai/chat/stream` SSE 接口发起只读请求。测试用户和会话相互隔离，不执行写工具。

### 6.2 场景

- `chat`：普通聊天请求。
- `rag`：知识库问答请求。

每个并发档位一次性并发发出与档位相同数量的请求，例如并发 32 表示同时发出 32 个请求。

### 6.3 指标定义

| 指标 | 定义 |
|---|---|
| Success | HTTP/SSE 正常完成、收到有效回答且没有用户可见 `<think>` |
| TTFT | 从请求发出到首个用户可见回答内容的时间，不把 SSE `start` 事件当作有效首字 |
| E2E | 从请求发出到 SSE 回答完整结束的总时间 |
| P50/P95 | 本轮样本的中位数和 95 分位延迟 |
| RPS | 该并发批次成功请求数除以批次墙钟时间 |

### 6.4 基本命令

基线：

```bash
python /tmp/benchmark_live.py \
  --base-url http://127.0.0.1:8000 \
  --timeout 120 \
  --repeats 5 \
  --scenarios chat,rag \
  --skip-concurrency
```

聊天并发：

```bash
python /tmp/benchmark_live.py \
  --base-url http://127.0.0.1:8000 \
  --timeout 180 \
  --scenarios= \
  --concurrency 1,2,4,8,16,32 \
  --concurrency-scenario chat
```

RAG 并发：

```bash
python /tmp/benchmark_live.py \
  --base-url http://127.0.0.1:8000 \
  --timeout 240 \
  --scenarios= \
  --concurrency 1,2,4,8,16,32 \
  --concurrency-scenario rag
```

## 7. RAG 瓶颈定位

日志分段计时显示：

```text
Milvus/混合检索：约 0.03～0.09 s
RAG 答案生成：约 3.9～4.1 s（低并发代表值）
```

模型接口确认结果：

| 端口 | 模型 | 用途 |
|---|---|---|
| `8099` | `qwen3-8b`，max model len 32768 | Chat、Intent、RAG 生成 |
| `8097` | `bge-large-zh-v1.5`，模型接口 ID `/model` | Embedding |
| `8098` | `qwen2.5-vl-7b` | 与本轮 RAG 无关 |

因此，本地模式下的主要瓶颈是 `vllm-qwen3-8b` 的生成吞吐，而不是 Embedding 或 Milvus。高并发时多个 RAG 请求竞争同一个生成模型，TTFT 和 E2E 随排队迅速上升。

## 8. 第一阶段：本地 qwen3-8b 测试结果

### 8.1 五次基线

| 场景 | 成功 | TTFT P50 | TTFT P95 | E2E P50 | E2E P95 |
|---|---:|---:|---:|---:|---:|
| Chat | 5/5 | 0.555 s | 0.573 s | 0.596 s | 0.609 s |
| RAG | 5/5 | 0.621 s | 0.630 s | 4.152 s | 5.983 s |

### 8.2 Chat 并发阶梯

| 并发 | 成功 | TTFT P95 | E2E P95 | 吞吐 |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 0.523 s | 0.560 s | 1.786 RPS |
| 2 | 2/2 | 0.636 s | 0.688 s | 2.906 RPS |
| 4 | 4/4 | 0.632 s | 0.740 s | 5.402 RPS |
| 8 | 8/8 | 0.800 s | 0.900 s | 8.891 RPS |
| 16 | 16/16 | 1.449 s | 1.558 s | 10.255 RPS |
| 32 | 32/32 | 2.833 s | 3.099 s | 10.313 RPS |
| 64 | 64/64 | 5.385 s | 5.538 s | 11.395 RPS |

普通聊天在并发 32 前扩展较好；并发 16 后吞吐逐渐接近平台，但没有请求失败。

### 8.3 RAG 并发阶梯

| 并发 | 成功 | TTFT P95 | E2E P95 | 吞吐 |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 0.675 s | 4.346 s | 0.230 RPS |
| 2 | 2/2 | 0.704 s | 4.387 s | 0.456 RPS |
| 4 | 4/4 | 0.772 s | 6.354 s | 0.629 RPS |
| 8 | 8/8 | 0.903 s | 6.689 s | 1.196 RPS |
| 16 | 16/16 | 7.322 s | 11.326 s | 1.413 RPS |
| 32 | 32/32 | 14.640 s | 18.515 s | 1.572 RPS |

RAG 在并发 8 后出现明确拐点：并发从 8 增加到 32，吞吐只提高约 31%，但 TTFT P95 从 `0.903 s` 上升到 `14.640 s`。

### 8.4 本地混合负载

并行运行 Chat 16 + RAG 8，连续执行 3 批，共 72 个请求：

- 72/72 成功。
- Chat E2E P95 在不同批次约为 `1.85～5.67 s`。
- RAG E2E P95 在不同批次约为 `4.97～8.59 s`。

混合负载比单场景负载波动更大，证明 Chat 和 RAG 竞争同一个 `qwen3-8b` 推理服务时会互相影响。

## 9. 第二阶段：API Key 模式测试结果

### 9.1 切换方式

生产 Compose 将 CHAT 和 INTENT 映射到已有 PLANNER 托管配置：

```yaml
- CHAT_LLM_API_KEY=${PLANNER_LLM_API_KEY}
- CHAT_LLM_API_BASE=${PLANNER_LLM_API_BASE}
- CHAT_LLM_MODEL=${PLANNER_LLM_MODEL}
- INTENT_LLM_API_KEY=${PLANNER_LLM_API_KEY}
- INTENT_LLM_API_BASE=${PLANNER_LLM_API_BASE}
- INTENT_LLM_MODEL=${PLANNER_LLM_MODEL}
```

实际非敏感配置：

```text
API base: https://dashscope.aliyuncs.com/compatible-mode/v1
Model: qwen3.5-plus
API key: SET（值未输出）
```

仅重建 AI 服务：

```bash
docker -H unix:///var/run/docker.sock compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d --no-deps --force-recreate ai-service
```

重建后两个 Worker 均初始化成功，Redis、Milvus、Embedding 和 RAG 正常。

### 9.2 小流量验证

| 场景 | 成功 | TTFT | E2E |
|---|---:|---:|---:|
| Chat | 1/1 | 1.661 s | 1.714 s |
| RAG | 1/1 | 2.964 s | 6.603 s |

小流量验证通过后才继续扩大并发。

### 9.3 五次基线

| 场景 | 成功 | TTFT P50 | TTFT P95 | E2E P50 | E2E P95 |
|---|---:|---:|---:|---:|---:|
| Chat | 5/5 | 1.639 s | 1.707 s | 1.679 s | 1.763 s |
| RAG | 5/5 | 2.483 s | 2.933 s | 6.697 s | 6.880 s |

### 9.4 Chat 并发阶梯

| 并发 | 成功 | TTFT P95 | E2E P95 | 吞吐 |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 1.471 s | 1.510 s | 0.662 RPS |
| 2 | 2/2 | 1.326 s | 1.369 s | 1.461 RPS |
| 4 | 4/4 | 1.687 s | 1.724 s | 2.319 RPS |
| 8 | 8/8 | 1.790 s | 1.834 s | 4.360 RPS |
| 16 | 16/16 | 1.754 s | 1.852 s | 8.627 RPS |
| 32 | 32/32 | 3.273 s | 3.313 s | 9.012 RPS |

### 9.5 RAG 并发阶梯

| 并发 | 成功 | TTFT P95 | E2E P95 | 吞吐 |
|---:|---:|---:|---:|---:|
| 1 | 1/1 | 2.445 s | 6.440 s | 0.155 RPS |
| 2 | 2/2 | 2.574 s | 6.540 s | 0.306 RPS |
| 4 | 4/4 | 2.439 s | 6.778 s | 0.590 RPS |
| 8 | 8/8 | 2.830 s | 6.718 s | 1.191 RPS |
| 16 | 16/16 | 2.917 s | 6.970 s | 2.295 RPS |
| 32 | 32/32 | 3.759 s | 7.648 s | 4.062 RPS |

### 9.6 API 模式请求统计

本阶段包括：

- 小流量验证 2 个请求。
- 五次基线 10 个请求。
- Chat 并发阶梯 63 个请求。
- RAG 并发阶梯 63 个请求。

合计 138 个请求，138/138 成功。未检测到 `<think>` 泄漏、LLM 超时或 API 限流响应。

## 10. 本地与 API 模式对比

### 10.1 单请求与低并发

| 场景 | 本地 P95 E2E | API P95 E2E | 结论 |
|---|---:|---:|---|
| Chat 基线 | 0.609 s | 1.763 s | 本地延迟低约 65% |
| RAG 基线 | 5.983 s | 6.880 s | 本地延迟低约 13% |

托管 API 增加了公网调用、调度和更大模型推理延迟，因此不适合以降低单请求延迟为目标进行全量替换。

### 10.2 高并发 RAG

| 并发 | 指标 | 本地 qwen3-8b | API qwen3.5-plus | API 改善 |
|---:|---|---:|---:|---:|
| 16 | TTFT P95 | 7.322 s | 2.917 s | 降低 60.2% |
| 16 | E2E P95 | 11.326 s | 6.970 s | 降低 38.5% |
| 16 | 吞吐 | 1.413 RPS | 2.295 RPS | 提升 62.4% |
| 32 | TTFT P95 | 14.640 s | 3.759 s | 降低 74.3% |
| 32 | E2E P95 | 18.515 s | 7.648 s | 降低 58.7% |
| 32 | 吞吐 | 1.572 RPS | 4.062 RPS | 提升 158.4% |

API 模式主要解决了本地单模型在高并发 RAG 下的推理排队问题。

### 10.3 Chat 对比

Chat 在本次所有同口径档位下，本地模型都具有更低延迟或更高吞吐。例如并发 32：

| 模式 | TTFT P95 | E2E P95 | 吞吐 |
|---|---:|---:|---:|
| 本地 qwen3-8b | 2.833 s | 3.099 s | 10.313 RPS |
| API qwen3.5-plus | 3.273 s | 3.313 s | 9.012 RPS |

因此没有性能依据支持把普通聊天永久全部切到托管 API。

## 11. 500 名员工容量评估

500 名员工不等于 500 个同时进行中的 AI 请求。本报告采用以下容量假设：

- 高峰活跃员工占 10%～20%，即 50～100 人。
- 每名高峰活跃用户平均每 30～60 秒发起一次 AI 请求。
- 预估持续请求率约 1.7～3.3 RPS。
- 加上突发和容量余量后，最低按持续 3 RPS、突发 5 RPS、50 个同时进行中的请求设计。

### 11.1 本项目建议门槛

| 指标 | 上线最低 | 推荐目标 |
|---|---:|---:|
| 同时在线用户 | 50 | 100 |
| 同时进行中的 AI 请求 | 30～50 | 80～100 |
| 持续吞吐 | 3 RPS | 5 RPS |
| 短时突发吞吐 | 5 RPS | 10 RPS |
| 请求成功率 | >= 99% | >= 99.9% |
| 普通聊天 P95 E2E | <= 5 s | <= 3 s |
| RAG 有效 TTFT P95 | <= 5 s | <= 3 s |
| RAG E2E P95 | <= 10 s | <= 8 s |
| 最低容量持续时间 | 30 分钟 | 2 小时 |
| 容量余量 | >= 30% | >= 50% |

以上是面向本项目 500 人规模的工程验收目标，比仓库通用门禁中的 `<30 s/<60 s` 更严格。通用门禁用于阻止严重性能回退，本表用于实际容量规划。

### 11.2 当前判定

| 项目 | 当前证据 | 判定 |
|---|---|---|
| API RAG 并发 32 | 4.062 RPS，P95 7.648 s，100% 成功 | 达到 32 并发档目标 |
| API Chat 并发 32 | 9.012 RPS，P95 3.313 s，100% 成功 | 达到最低目标 |
| 50 并发 | 未执行 | 未验收 |
| 持续 3 RPS × 30 分钟 | 未执行 | 未验收 |
| 突发 5 RPS | 未按开放到达率执行 | 未验收 |
| API 混合负载 | 未执行 | 未验收 |
| 完整业务链路 | MySQL 不可达 | 不通过 |

结论：当前 API 模式具有满足 500 人规模最低性能要求的潜力，但尚不能签署容量验收通过。

容量测试应使用接近真实业务的用户旅程、预定义 KPI，并逐步增加到超过预计峰值以识别容量拐点。参考：

- [AWS Well-Architected：Validate system reliability with performance testing](https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/qa.nt.2-validate-system-reliability-with-performance-testing.html)
- [AWS Well-Architected：Test scalability and performance requirements](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_testing_resiliency_test_non_functional.html)

## 12. 压测后健康检查

### 12.1 AI 服务

```text
running, healthy, restarts=0
```

### 12.2 本地 qwen3-8b 队列

API 模式测试结束后：

```text
vllm:num_requests_running{model_name="qwen3-8b"} 0
vllm:num_requests_waiting{model_name="qwen3-8b"} 0
```

这与 CHAT/INTENT/RAG 生成已经切到 API 模式一致。Embedding 仍通过独立的本地模型服务完成。

### 12.3 错误与告警

未发现：

- LLM/API timeout。
- API rate limit。
- HTTP 5xx 导致的测试失败。
- `<think>` 泄漏。
- 容器 OOM 或重启。
- vLLM 请求持续排队。
- Redis、Milvus 或 RAG 初始化失败。

持续存在但与本轮 LLM 性能对比无直接关系的问题：

```text
Can't connect to MySQL server on '172.29.0.1' ([Errno 111] Connection refused)
```

该问题导致会话日志/审计写入降级。虽然测试请求返回成功，但生产放行必须恢复 MySQL 后重测。

此外还有两项生产配置差异：

- 启动日志仍显示 `Environment: development`。
- Compose 合并后仍存在 `/app` 源码 bind mount，并非完全使用镜像内不可变代码。

两项均不影响本轮同一环境下的 A/B 性能比较，但必须在最终生产等价测试前修正。

启动阶段还观察到 PyMilvus 弃用警告及一次 `AsyncMilvusClient._get_connection was never awaited` RuntimeWarning。当前没有造成功能失败，但应在后续依赖升级任务中处理。

## 13. 当前配置状态与回滚

### 13.1 当前状态

服务器当前保持 API Key 模式：

```text
CHAT_LLM_MODEL=qwen3.5-plus
INTENT_LLM_MODEL=qwen3.5-plus
PLANNER_LLM_MODEL=qwen3.5-plus
```

Embedding 和 Milvus 仍使用项目本地服务。

### 13.2 备份

切换前生产 Compose 已保存在：

```text
/tmp/docker-compose.prod.before-api.yml
```

更早的远端 RAG 优化前代码备份：

```text
/tmp/langgraph_agent.before-rag-opt.py
/tmp/langchain_rag.before-rag-opt.py
/tmp/intent_router.before-rag-opt.py
```

### 13.3 回滚步骤

确认需要恢复本地 CHAT/INTENT 后：

```bash
cd /home/caic/code/workhour/workhour_agent
cp /tmp/docker-compose.prod.before-api.yml docker-compose.prod.yml
docker -H unix:///var/run/docker.sock compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d --no-deps --force-recreate ai-service
```

回滚后必须重新检查模型环境变量、健康接口、Chat 和 RAG 冒烟请求。

## 14. 推荐生产架构

建议增加独立的 `RAG_LLM_*` 配置，形成以下模型分层：

```text
普通聊天 / 轻量意图识别
        └── 本地 qwen3-8b

RAG 答案生成 / 多步规划 / 复杂任务
        └── DashScope qwen3.5-plus

Embedding
        └── 本地 bge-large-zh-v1.5

Vector Store
        └── 项目 Milvus
```

这样可以保留本地模型低延迟、低成本的优点，同时绕开高并发 RAG 的生成吞吐瓶颈。引入该配置后需要重新执行同一套基线、混合并发和稳定性测试。

## 15. 后续必做测试

按优先级执行：

1. 恢复 MySQL，确认会话审计和真实业务链路正常。
2. API 模式 50 并发 Chat/RAG 混合测试，建议流量比例为 Chat 70%、RAG 30%。
3. 使用开放到达率执行持续 `3 RPS × 30 分钟`。
4. 执行 `5 RPS × 10～15 分钟` 峰值测试。
5. 执行 100 并发短时压力测试，记录拐点和恢复能力。
6. 执行 4～8 小时稳定性测试，观察内存、连接、句柄、Redis、Milvus 和 GPU 显存趋势。
7. 记录每场景 prompt/completion token、API 成本和限额。
8. 加入单工具、复杂任务、批量工时、SQL Agent 和接近上下文上限的场景。
9. 对 API 模式单独执行 RAG 答案质量评测，避免只优化吞吐而降低准确率。

## 16. 最终测试结论

| 检查项 | 结果 |
|---|---|
| Redis 恢复 | 通过 |
| 项目 Milvus 恢复 | 通过 |
| 生产双 Worker、无 reload | 通过 |
| RAG 重复生成问题 | 已修复 |
| 本地模型基线/并发测试 | 完成 |
| API Key 模式基线/并发测试 | 完成 |
| Chat/RAG 协议成功率 | 本轮样本 100% |
| 用户可见 `<think>` | 0 |
| 容器崩溃/OOM/重启 | 0 |
| 本地 qwen3-8b 高并发 RAG 容量 | 不满足 500 人最低目标 |
| qwen3.5-plus API 高并发 RAG 容量 | 32 并发达到目标，50 并发待测 |
| 30 分钟容量测试 | 未执行 |
| 4～8 小时稳定性测试 | 未执行 |
| MySQL 完整链路 | 不通过 |

最终判定：**本轮性能定位、优化和模型方案对比完成；当前配置可以继续进入生产容量验收，但不能据此直接签署完整生产放行。**
