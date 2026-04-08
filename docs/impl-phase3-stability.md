# 第三阶段：稳定性 + 监控 + 体验优化

> 编写日期：2026-04-08
> 执行优先级：Task 1 → Task 2 → Task 3 → Task 4（按序执行，每个独立）
> 前置条件：Phase 2 已全部完成，Layer 1 v5 82.6%，Layer 2 v4 99.7%（有效精度）

---

## 概览

| # | 任务 | 预估 | 难度 | 核心收益 |
|---|------|------|------|---------|
| T1 | 修复数据库密码硬编码 | 0.5h | 极低 | 安全风险消除 |
| T2 | Prometheus 指标收集 + Grafana 看板 | 0.5d | 中 | 上线后可观测 |
| T3 | 流式 RAG 输出 | 0.5d | 中 | 知识问答不再"卡住" |
| T4 | vLLM 本地部署验证 | 0.5-1d | 中 | 成本归零 + 低延迟 |

---

## Task 1：修复数据库密码硬编码

### 背景

`fastapi-service/app/core/config.py` 第 40 行：

```python
MYSQL_PASSWORD: str = "19990512"
```

生产环境密码硬编码在代码中，已纳入 git 历史。

### 改动文件

`fastapi-service/app/core/config.py`

### 改动内容

将所有敏感字段的默认值改为空字符串，通过 `.env` 文件注入：

```python
# ── 改动前 ──
MYSQL_PASSWORD: str = "19990512"

# ── 改动后 ──
MYSQL_PASSWORD: str = ""
```

同时确认 `.env.example` 中有明确的注释说明该字段必填：

```env
# MySQL 密码（必填，不要使用默认值）
MYSQL_PASSWORD=
```

### 附加检查

确认以下文件中没有其他硬编码密码/密钥：

```bash
grep -rn "password\|secret\|api_key" fastapi-service/app/ --include="*.py" \
  | grep -v "\.pyc" | grep -v "test" | grep -v "# " | grep -v '""' | grep -v "os.getenv"
```

如果发现其他硬编码值，一并清理。

### 验证方法

```bash
# 1. 不配置 .env 中的 MYSQL_PASSWORD 启动服务
# 期望：服务正常启动（main.py 已有 DB 降级逻辑），但 DB 功能不可用
# 不应崩溃

# 2. 配置正确的 MYSQL_PASSWORD 启动服务
# 期望：DB 功能正常
```

---

## Task 2：Prometheus 指标收集 + Grafana 看板

### 背景

系统即将上线但完全没有运行时监控。出问题时只能翻日志，无法实时发现异常。

**好消息**：基础设施已经 80% 就绪：
- `prometheus-client>=0.19.0` 已在 `requirements.txt`
- `docker-compose.yml` 已配置 Prometheus（:9090）和 Grafana（:3000）
- `prometheus/prometheus.yml` 已配置抓取 `ai-service:8000/metrics`
- 代码中已有丰富的日志数据（duration_ms、tokens、tool_name、intent 等）

**缺失**：`/metrics` 端点 + 指标埋点 + Grafana 看板。

### 改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `fastapi-service/app/core/metrics.py` | **新建** | 定义所有 Prometheus 指标 |
| `fastapi-service/main.py` | 修改 | 注册 `/metrics` 端点 |
| `fastapi-service/app/api/chat.py` | 修改 | 请求级指标埋点 |
| `fastapi-service/app/services/task_executor.py` | 修改 | 工具执行指标埋点 |
| `fastapi-service/app/services/llm_client.py` | 修改 | LLM 调用指标埋点 |
| `grafana/datasources/prometheus.yml` | **新建** | Grafana 数据源配置 |
| `grafana/dashboards/ai-service.json` | **新建** | Grafana 看板 |

---

### 文件 1：新建 `fastapi-service/app/core/metrics.py`

```python
"""
Prometheus 指标定义

所有指标统一在此定义，各模块 import 后使用。
"""

from prometheus_client import Counter, Histogram, Gauge, Info

# ─── 请求级指标 ────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "ai_chat_requests_total",
    "聊天请求总数",
    ["intent", "status"],  # intent: tool_execution/knowledge_qa/general_chat/clarify, status: success/error
)

REQUEST_LATENCY = Histogram(
    "ai_chat_request_duration_seconds",
    "聊天请求处理耗时（秒）",
    ["intent"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

ACTIVE_REQUESTS = Gauge(
    "ai_chat_active_requests",
    "当前正在处理的请求数",
)

# ─── 工具执行指标 ──────────────────────────────────────────────────────────────

TOOL_CALL_COUNT = Counter(
    "ai_tool_calls_total",
    "工具调用总数",
    ["tool_name", "status"],  # status: success/error/permission_denied
)

TOOL_CALL_LATENCY = Histogram(
    "ai_tool_call_duration_seconds",
    "工具调用耗时（秒）",
    ["tool_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ─── LLM 调用指标 ──────────────────────────────────────────────────────────────

LLM_CALL_COUNT = Counter(
    "ai_llm_calls_total",
    "LLM API 调用总数",
    ["model", "call_type", "status"],  # call_type: generate/stream/function_calling, status: success/error
)

LLM_CALL_LATENCY = Histogram(
    "ai_llm_call_duration_seconds",
    "LLM API 调用耗时（秒）",
    ["model", "call_type"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

LLM_TOKENS = Counter(
    "ai_llm_tokens_total",
    "LLM Token 使用量",
    ["model", "token_type"],  # token_type: prompt/completion
)

# ─── RAG 指标 ──────────────────────────────────────────────────────────────────

RAG_QUERY_COUNT = Counter(
    "ai_rag_queries_total",
    "RAG 查询总数",
    ["status"],  # status: success/no_results/error
)

RAG_QUERY_LATENCY = Histogram(
    "ai_rag_query_duration_seconds",
    "RAG 查询耗时（秒）",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0],
)

# ─── 服务信息 ──────────────────────────────────────────────────────────────────

SERVICE_INFO = Info(
    "ai_service",
    "AI 服务版本信息",
)
```

---

### 文件 2：修改 `fastapi-service/main.py`

在 `lifespan` 函数的初始化末尾（工具注册之后），添加 `/metrics` 端点：

```python
# 在 main.py 顶部添加 import
from prometheus_client import make_asgi_app as make_metrics_app

# 在 app 创建之后（约第 147 行附近），挂载 /metrics
metrics_app = make_metrics_app()
app.mount("/metrics", metrics_app)

# 在 lifespan 的初始化末尾添加服务信息
from app.core.metrics import SERVICE_INFO
SERVICE_INFO.info({
    "version": "1.2",
    "phase": "3",
    "llm_model": settings.CHAT_LLM_MODEL,
})
```

---

### 文件 3：修改 `fastapi-service/app/api/chat.py`

在流式和非流式端点中添加指标埋点。

#### 流式端点 `/chat/stream`（约第 152 行）

在 `chat_stream` 函数开头和结尾添加：

```python
from app.core.metrics import REQUEST_COUNT, REQUEST_LATENCY, ACTIVE_REQUESTS
import time

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request):
    ACTIVE_REQUESTS.inc()
    _start = time.monotonic()
    _intent = "unknown"

    async def _wrapped_stream():
        nonlocal _intent
        try:
            async for event_str in stream_agent_response(...):
                # 从事件中提取 intent（用于标签）
                if "tool_call" in event_str:
                    _intent = "tool_execution"
                yield event_str
        finally:
            duration = time.monotonic() - _start
            REQUEST_COUNT.labels(intent=_intent, status="success").inc()
            REQUEST_LATENCY.labels(intent=_intent).observe(duration)
            ACTIVE_REQUESTS.dec()

    return StreamingResponse(_wrapped_stream(), media_type="text/event-stream")
```

#### 非流式端点 `/chat`（约第 233 行）

在 `chat_non_stream` 函数的 try/finally 中添加：

```python
from app.core.metrics import REQUEST_COUNT, REQUEST_LATENCY, ACTIVE_REQUESTS
import time

@router.post("/chat")
async def chat_non_stream(request: ChatRequest, http_request: Request):
    ACTIVE_REQUESTS.inc()
    _start = time.monotonic()
    try:
        # ... 现有逻辑不变 ...
        REQUEST_COUNT.labels(intent=intent_value or "unknown", status=status).inc()
        return response
    except Exception as e:
        REQUEST_COUNT.labels(intent="unknown", status="error").inc()
        raise
    finally:
        duration = time.monotonic() - _start
        REQUEST_LATENCY.labels(intent=intent_value or "unknown").observe(duration)
        ACTIVE_REQUESTS.dec()
```

---

### 文件 4：修改 `fastapi-service/app/services/task_executor.py`

在 `_execute_tool_call` 方法（约第 316 行工具执行处），用指标包裹：

```python
from app.core.metrics import TOOL_CALL_COUNT, TOOL_CALL_LATENCY
import time

# 在 handler 调用前后添加
_tool_start = time.monotonic()
try:
    # ... 现有 handler(**processed_params) 调用 ...
    TOOL_CALL_COUNT.labels(tool_name=task.tool_name, status="success").inc()
except PermissionError:
    TOOL_CALL_COUNT.labels(tool_name=task.tool_name, status="permission_denied").inc()
    raise
except Exception:
    TOOL_CALL_COUNT.labels(tool_name=task.tool_name, status="error").inc()
    raise
finally:
    TOOL_CALL_LATENCY.labels(tool_name=task.tool_name).observe(time.monotonic() - _tool_start)
```

---

### 文件 5：修改 `fastapi-service/app/services/llm_client.py`

在 `generate`（约第 60 行）和 `generate_with_tools`（约第 170 行）方法中添加：

```python
from app.core.metrics import LLM_CALL_COUNT, LLM_CALL_LATENCY, LLM_TOKENS
import time

# generate 方法中
_start = time.monotonic()
try:
    # ... 现有 API 调用 ...
    LLM_CALL_COUNT.labels(model=self.model, call_type="generate", status="success").inc()
    # 如果响应中有 usage 信息：
    if usage := result.get("usage"):
        LLM_TOKENS.labels(model=self.model, token_type="prompt").inc(usage.get("prompt_tokens", 0))
        LLM_TOKENS.labels(model=self.model, token_type="completion").inc(usage.get("completion_tokens", 0))
except Exception:
    LLM_CALL_COUNT.labels(model=self.model, call_type="generate", status="error").inc()
    raise
finally:
    LLM_CALL_LATENCY.labels(model=self.model, call_type="generate").observe(time.monotonic() - _start)

# generate_with_tools 方法中，同理，call_type="function_calling"
```

---

### 文件 6：新建 `grafana/datasources/prometheus.yml`

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

---

### 文件 7：Grafana 看板

> Grafana 看板 JSON 较长，建议 agent 用以下 PromQL 查询手动创建，或生成 JSON 导入。

看板名称：**AI Service Overview**

| 面板 | 类型 | PromQL |
|------|------|--------|
| 请求 QPS | Timeseries | `rate(ai_chat_requests_total[5m])` |
| 请求延迟 P95 | Timeseries | `histogram_quantile(0.95, rate(ai_chat_request_duration_seconds_bucket[5m]))` |
| 错误率 | Stat | `sum(rate(ai_chat_requests_total{status="error"}[5m])) / sum(rate(ai_chat_requests_total[5m]))` |
| 活跃请求数 | Gauge | `ai_chat_active_requests` |
| 工具调用分布 | Piechart | `sum by (tool_name) (ai_tool_calls_total)` |
| 工具调用延迟 | Timeseries | `histogram_quantile(0.95, rate(ai_tool_call_duration_seconds_bucket[5m]))` |
| LLM Token 消耗 | Timeseries | `rate(ai_llm_tokens_total[1h])` |
| LLM 调用延迟 | Timeseries | `histogram_quantile(0.95, rate(ai_llm_call_duration_seconds_bucket[5m]))` |
| 意图分布 | Piechart | `sum by (intent) (ai_chat_requests_total)` |

看板 JSON 可以在 Grafana UI 中手动创建后 Export，也可以直接生成 JSON 文件放到 `grafana/dashboards/` 目录。建议先创建核心 4 个面板（QPS、延迟、错误率、活跃请求），其余后续补充。

---

### 验证方法

```bash
# 1. 启动服务
docker-compose up -d

# 2. 验证 /metrics 端点
curl http://localhost:8000/metrics
# 期望：返回 Prometheus 格式的指标文本，包含 ai_chat_requests_total 等

# 3. 发一条请求后再查
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test" \
  -H "X-Entity-Type: employee" \
  -d '{"message": "你好"}'

curl http://localhost:8000/metrics | grep ai_chat
# 期望：ai_chat_requests_total{intent="general_chat",status="success"} 1

# 4. 打开 Grafana
# http://localhost:3000 (admin/admin)
# 确认 Prometheus 数据源可连通，看板有数据
```

---

## Task 3：流式 RAG 输出

### 背景

当前知识问答流程：

```
用户提问 → 检索文档（~1s）→ LLM 生成完整答案（~3-5s，阻塞）→ 一次性返回
```

用户在 3-5 秒内看不到任何输出，体验差。改为流式后：

```
用户提问 → 检索文档（~1s）→ SSE 发送"正在查询知识库..."
  → LLM 逐 token 生成 → SSE 逐块推送 → 用户实时看到文字出现
```

### 技术可行性

- `llm_client.py` 已有 `stream_generate()` 方法（第 99 行），支持 async 逐 token yield
- RAG 的阻塞点在 `langchain_rag.py` 第 410 行的 `chain.invoke()`（同步等待完整结果）
- 解决方案：将 RAG 的 LLM 生成部分改为调用 `_llm_client.stream_generate()`，不依赖 LangChain chain

### 改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `fastapi-service/app/services/langchain_rag.py` | 修改 | 新增 `stream_query()` 方法 |
| `fastapi-service/app/services/langgraph_agent.py` | 修改 | `stream_agent_response` 中对 RAG 结果做流式输出 |

---

### 文件 1：修改 `fastapi-service/app/services/langchain_rag.py`

在现有 `query()` 方法（第 348 行）之后，新增 `stream_query()` 方法：

```python
async def stream_query(self, question: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    流式 RAG 查询：检索阶段同步完成，生成阶段逐 token 流式输出。

    Yields:
        {"type": "retrieval", "message": "正在查询知识库..."}
        {"type": "chunk", "content": "一小段文字"}
        {"type": "done", "sources": [...], "retrieved_count": int}
        或
        {"type": "error", "message": "错误信息"}
    """
    if not self._initialized:
        yield {"type": "error", "message": "RAG 服务未初始化，请稍后重试。"}
        return

    try:
        # 1. 检索（同步阶段，通常 <1s）
        yield {"type": "retrieval", "message": "正在查询知识库..."}

        retriever = self.multi_query_retriever or self.ensemble_retriever
        docs = await asyncio.to_thread(retriever.invoke, question)

        if self._reranker_enabled and self.reranker and docs:
            try:
                docs = await asyncio.to_thread(
                    self.reranker.compress_documents, docs, question
                )
            except Exception as e:
                logger.warning(f"重排序失败，使用原始检索结果: {e}")

        if not docs:
            yield {"type": "chunk", "content": "抱歉，我在知识库中没有找到相关信息。您可以尝试换个问法或联系管理员。"}
            yield {"type": "done", "sources": [], "retrieved_count": 0}
            return

        # 2. 构建上下文
        context = "\n\n".join(doc.page_content for doc in docs[:5])

        # 3. 流式生成（使用 llm_client.stream_generate 而非 LangChain chain）
        system_prompt = (
            "你是工时管理系统的AI助手。请根据以下知识库内容回答用户的问题，"
            "回答要简洁、准确。如果知识库内容不足以完整回答，请如实说明。\n\n"
            f"知识库内容：\n{context}"
        )

        # 需要获取 llm_client 实例
        from app.services.langgraph_agent import _llm_client
        if not _llm_client:
            # 降级到同步模式
            from langchain_core.output_parsers import StrOutputParser
            prompt_template = get_prompt_manager().get_chat_template("rag") or \
                ChatPromptTemplate.from_messages([
                    ("system", "你是工时管理系统的AI助手。请根据以下知识库内容回答用户的问题，"
                     "回答要简洁、准确。如果知识库内容不足以完整回答，请如实说明。\n\n"
                     "知识库内容：\n{context}"),
                    ("human", "{question}"),
                ])
            chain = prompt_template | self.llm | StrOutputParser()
            answer = await asyncio.to_thread(chain.invoke, {"context": context, "question": question})
            yield {"type": "chunk", "content": answer}
        else:
            async for chunk in _llm_client.stream_generate(
                prompt=question,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=1500,
            ):
                if chunk:
                    yield {"type": "chunk", "content": chunk}

        # 4. 来源信息
        sources = []
        seen = set()
        for doc in docs:
            src = doc.metadata.get("source", "未知来源")
            if src not in seen:
                sources.append({
                    "source": Path(src).name if src != "未知来源" else src,
                    "type": doc.metadata.get("type", "document"),
                })
                seen.add(src)

        yield {"type": "done", "sources": sources, "retrieved_count": len(docs)}

    except Exception as e:
        logger.error(f"流式 RAG 查询失败: {e}", exc_info=True)
        yield {"type": "error", "message": f"生成回答时出现错误：{str(e)}"}
```

同时在模块级暴露流式查询函数：

```python
async def langchain_rag_stream_query(question: str):
    """流式 RAG 查询入口"""
    if _rag_service:
        async for item in _rag_service.stream_query(question):
            yield item
    else:
        yield {"type": "error", "message": "RAG 服务未初始化"}
```

---

### 文件 2：修改 `fastapi-service/app/services/langgraph_agent.py`

修改 `stream_agent_response` 函数（约第 619 行），在处理 RAG 结果时改为流式输出。

当前逻辑（非流式）：
```python
# 现在：LangGraph 跑完后一次性提取 rag_result
rag_result = final_state.get("rag_result")
if rag_result:
    yield _format_sse("response", {"message": rag_result.get("response", "")})
```

改为在检测到 intent=knowledge_qa 时，绕过 LangGraph 的 RAG 节点，直接流式输出：

**方案（推荐）**：在 `stream_agent_response` 中，当 LangGraph 运行结束、检测到 intent 为 `knowledge_qa` 时，改用流式 RAG 替代一次性结果。

具体改动点在 `stream_agent_response` 函数中，找到处理 `rag_result` 的位置，替换为：

```python
rag_result = final_state.get("rag_result")
if rag_result:
    # 尝试流式输出（如果 RAG 服务支持）
    if rag_result.get("success") and rag_result.get("response"):
        # 非流式降级：直接输出完整结果
        yield _format_sse("response", {
            "message": rag_result["response"],
            "sources": rag_result.get("sources", []),
        })
    elif rag_result.get("error"):
        yield _format_sse("error", {"message": rag_result.get("response", "RAG 查询失败")})
```

**进阶方案**（体验更好，但改动更大）：在 `node_execute_rag` 之前拦截，直接用 `langchain_rag_stream_query` 流式输出，跳过 LangGraph 的 RAG 节点：

```python
# 在 stream_agent_response 中，graph.ainvoke 之前判断
# 如果 llm_with_tools 节点已确定 intent=knowledge_qa，
# 则不走 execute_rag 节点，改为直接流式 RAG

# 这需要分两步执行 LangGraph：
# Step 1: 只跑 llm_with_tools 节点，获取 intent
# Step 2: 如果 intent=knowledge_qa，走流式 RAG；否则继续 LangGraph

# 实现复杂度较高，建议先用简单方案上线，后续再优化
```

**建议**：先实现 `stream_query()` 方法确保可用，然后用简单方案（非流式降级）上线。后续有体验投诉再做进阶方案。

---

### 验证方法

```bash
# 1. 启动服务（需要 Milvus 在线）
docker-compose up -d

# 2. 测试流式 RAG
curl -N -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test" \
  -H "X-Entity-Type: employee" \
  -d '{"message": "工时填报截止几号"}'

# 期望：
# event: start
# event: thinking (正在查询知识库...)
# event: response (逐步出现文字，而非等待 3-5 秒后一次性出现)
# event: done
```

---

## Task 4：vLLM 本地部署验证

### 背景

当前所有 LLM 调用走阿里云 DashScope API。本地部署 vLLM 可以：
- **成本归零**：无 API 调用费用
- **低延迟**：内网调用，减少 ~200ms 网络开销
- **数据安全**：所有数据不出内网

### 前置条件

- GPU 服务器就绪（至少 1 张 A100 40G 或 2 张 3090 24G）
- CUDA 驱动已安装
- 网络可达 huggingface.co 或有模型离线包

### 部署步骤

#### Step 1：在 GPU 服务器上部署 vLLM

```bash
# 安装 vLLM
pip install vllm

# 启动推理服务（OpenAI 兼容格式）
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct \
  --served-model-name qwen-plus \
  --host 0.0.0.0 \
  --port 8100 \
  --tensor-parallel-size 1 \
  --max-model-len 8192 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes

# 关键参数说明：
# --served-model-name qwen-plus    → 与 .env 中 CHAT_LLM_MODEL 一致，代码零改动
# --enable-auto-tool-choice        → 启用 Function Calling 支持
# --tool-call-parser hermes        → Qwen2.5 使用 hermes 格式的 tool_calls
```

#### Step 2：修改 `.env` 指向本地 vLLM

```env
# 改动前（DashScope）
CHAT_LLM_API_KEY=sk-xxxxx
CHAT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_LLM_MODEL=qwen-plus

# 改动后（本地 vLLM）
CHAT_LLM_API_KEY=empty           # vLLM 默认不需要真实 key，但代码校验非空
CHAT_LLM_API_BASE=http://GPU_SERVER_IP:8100/v1
CHAT_LLM_MODEL=qwen-plus         # 与 --served-model-name 一致，不用改
```

**代码改动量：零。** 只改环境变量。

#### Step 3：验证 Function Calling 兼容性

```bash
# 直接向 vLLM 发送 Function Calling 请求，验证 tool_calls 格式是否兼容
curl http://GPU_SERVER_IP:8100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus",
    "messages": [
      {"role": "system", "content": "你是工时管理助手"},
      {"role": "user", "content": "查一下我本周工时"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "query_timesheet",
          "description": "查询工时记录",
          "parameters": {
            "type": "object",
            "properties": {
              "start_date": {"type": "string"},
              "end_date": {"type": "string"},
              "user_id": {"type": "string"}
            }
          }
        }
      }
    ]
  }'

# 期望响应包含：
# "choices": [{"message": {"tool_calls": [{"function": {"name": "query_timesheet", ...}}]}}]

# 如果 tool_calls 格式不兼容（字段名不同、嵌套结构不同），
# 需要在 llm_client.py 的 generate_with_tools 中加适配层
```

#### Step 4：运行 Layer 1 精度回归

```bash
cd fastapi-service
../.venv/Scripts/python -m pytest tests/test_classification_accuracy.py \
  -n 4 --dist=load --tb=short \
  --json-report --json-report-file=reports/layer1_vllm.json -q
../.venv/Scripts/python tests/utils/accuracy_reporter.py reports/layer1_vllm.json
```

对比 DashScope qwen-plus 的 v5 基线（82.6%）。精度差距应 < 3%，否则需要调整模型或参数。

### 注意事项

| 风险 | 应对 |
|------|------|
| vLLM 的 tool_calls 格式与 DashScope 不兼容 | 在 `llm_client.py` 加适配层，统一格式 |
| 模型精度下降（14B vs DashScope 可能是更大模型） | 尝试 Qwen2.5-32B 或 72B（需要更多 GPU 显存） |
| GPU 显存不足 | 用 `--quantization awq` 加载 4-bit 量化版本 |
| vLLM 服务不稳定 | 配置 `CHAT_LLM_API_BASE` 为 DashScope 作为 fallback |

---

## 执行完成后的检查清单

```
Task 1 完成标志：
  □ config.py 中 MYSQL_PASSWORD 默认值改为空字符串
  □ .env.example 中有明确注释
  □ 不配置密码时服务不崩溃（降级）

Task 2 完成标志：
  □ /metrics 端点返回 Prometheus 格式指标
  □ 发送请求后 ai_chat_requests_total 计数增加
  □ Grafana 可连通 Prometheus 数据源
  □ 至少有"QPS + 延迟 + 错误率 + 活跃请求"4 个面板

Task 3 完成标志：
  □ langchain_rag.py 新增 stream_query() 方法
  □ 模块级 langchain_rag_stream_query() 可调用
  □ （进阶）知识问答 SSE 输出从"一次性"变为"逐块"

Task 4 完成标志：
  □ vLLM 服务在 GPU 服务器上启动
  □ Function Calling 兼容性通过验证
  □ .env 切换到 vLLM 后，Layer 1 精度回归差距 < 3%
  □ 服务运行稳定 > 1 小时无崩溃
```

---

## 不做的事（本次范围外）

| 项目 | 原因 |
|------|------|
| SQL Agent | 无用户需求驱动 |
| MCP Server | 工具数量未超过 10 个 |
| Multi-Agent | 无多角色业务场景 |
| OpenTelemetry 全链路追踪 | Prometheus 指标足够当前阶段用 |
