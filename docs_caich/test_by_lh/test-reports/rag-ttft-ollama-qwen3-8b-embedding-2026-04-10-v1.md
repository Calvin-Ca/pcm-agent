# RAG TTFT 性能测试报告（2026-04-10）

> 原文件名：`improvement-results-2026-04-10.md`（由原报告“RAG TTFT 性能”章节拆分）

## 前因后果

### 背景

2026-04-10 的 AI Service 改进验证中发现，普通聊天和工具调用能够较快返回，但知识库问答第一次请求需要较长时间才能输出正文。为确认延迟发生在 Agent 路由、RAG 检索还是模型推理阶段，原综合报告增加了这次 RAG TTFT 测试；整理测试报告时，该章节被单独拆分成本文件。

### 要解决的问题

本次测试重点回答两个问题：

1. 从开始调用 RAG 到前端收到首个答案内容需要多久；
2. 首次请求和后续请求的延迟差异，是否主要来自本地 Embedding 模型冷启动。

当时使用 Ollama 运行 `qwen3-embedding:8b`。该模型约 4.6GB，第一次请求需要加载模型并初始化 GPU 推理环境，因此怀疑它是知识问答冷启动慢的主要原因。

### 测试结论

测试记录到冷启动时 RAG 净 TTFT 约为 13.8 秒，模型已缓存后的暖启动约为 2.7 秒，冷暖差约 11.1 秒。由此初步判断，Ollama 本地 `qwen3-embedding:8b` 的模型加载过程是首次 RAG 请求延迟的主要来源，而不是每次知识库检索都会固定耗时 13 秒。

### 后续影响

该结果推动了后续 Embedding 模型对比：需要同时比较召回质量和延迟，不能只依据速度切换模型。候选方向包括云端 `text-embedding-v2` 和更轻量的中文 BGE 模型。后续独立评测最终进一步对比了 BGE 与 Qwen Embedding 的 Recall@K 和查询延迟。

### 结论边界

这是一轮冷、暖请求的定位性测试，并未逐段记录 Embedding、Milvus/BM25 检索和回答模型各自耗时，也没有统计 P50/P95/P99。因此“Embedding 冷启动是主因”属于根据冷暖差异作出的初步诊断，不应视为完整的分阶段性能归因。

**测试环境**：Ollama qwen3:8b (Q4_K_M 量化) + qwen3-embedding:8b (Q4_K_M 量化)  
**配置**：Reranker 关闭 (`USE_RERANKER=False`)  
**测试时间**：2026-04-10

---

## 测试方法

发送知识问答请求，测量从 RAG 检索开始到首个内容 token 的时间。

## 结果

| 场景 | TTFT | 说明 |
|------|------|------|
| RAG 检索开始 | 1.9s | 模型 thinking 后开始调用 RAG |
| 首个 RAG 内容 | 15.7s | 冷启动（qwen3-embedding:8b 加载到 GPU） |
| **RAG 净 TTFT（冷）** | **~13.8s** | embedding 模型冷启动是主因 |
| RAG 暖启动 | 2.7s | embedding 模型已缓存 |

## Embedding 模型对比路径

**当前**：`qwen3-embedding:8b`（Ollama，Q4_K_M，4096维）

- 优点：本地推理，无网络延迟
- 缺点：冷启动约 13s（GPU 加载 4.6GB 模型）

**对比方案**：

| 方案 | 冷启动 | 质量 | 操作 |
|------|--------|------|------|
| DashScope text-embedding-v2 | **无**（云端 API） | 1536维 FP16 | 改 `USE_OLLAMA_EMBEDDING=False` |
| bge-base-zh-v1.5 | 待测 | 中文优化 Embedding | Ollama 无，需单独部署 |

## 建议行动

将 `USE_OLLAMA_EMBEDDING=False` 切换到 DashScope text-embedding-v2，测试20条知识问答的召回质量与 TTFT，验证量化版与 FP16 版的差距。
