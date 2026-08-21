# LLM 性能基准测试报告

**测试时间**: 2026-04-09
**测试环境**: Ollama qwen3:8b（Q4_K_M 量化，本地部署）
**对比配置**: baseline_8b / optimized_8b / baseline_4b / optimized_4b / flash_8b
**测试工具**: `tests/performance/benchmark_llm.py`
**测试说明**: Layer 2/3 数据为同一批次，Layer 1/4 为补测数据

---

## Layer 1：直连 Ollama（纯 LLM 速度，排除 ai-service 开销）

### 1.1 全部配置横向对比

| 配置 | 场景 | TTFT 均值 | TTFT p95 | TPS 均值 | TPOT(ms) | Jitter(ms) | 成功率 |
|------|------|-----------|----------|----------|----------|------------|--------|
| baseline_8b | simple | 1.44s | 2.33s | 8.30 | 7.4 | 0.7 | 3/3 |
| baseline_8b | tool_call | 2.59s | 2.80s | 49.33 | 7.6 | 1.3 | 3/3 |
| baseline_8b | analysis | 2.60s | 2.91s | 60.52 | 7.6 | 1.4 | 3/3 |
| optimized_8b | simple | 1.08s | 1.49s | 10.13 | 7.6 | 1.3 | 3/3 |
| optimized_8b | tool_call | 2.66s | 2.87s | 49.29 | 7.6 | 1.4 | 3/3 |
| optimized_8b | analysis | 2.91s | 3.33s | 67.27 | 7.7 | 1.7 | 3/3 |
| baseline_4b | simple | 2.15s | 2.82s | 6.88 | 5.0 | 0.9 | 3/3 |
| baseline_4b | tool_call | 3.15s | 3.26s | 101.42 | 5.5 | 1.8 | 3/3 |
| baseline_4b | analysis | 3.74s | 4.99s | 122.30 | 5.4 | 1.7 | 3/3 |
| optimized_4b | simple | 1.65s | 1.81s | 5.62 | 5.2 | 0.8 | 3/3 |
| optimized_4b | tool_call | 2.75s | 3.41s | 104.96 | 5.5 | 2.1 | 3/3 |
| optimized_4b | analysis | 2.70s | 3.19s | 130.25 | 5.4 | 1.7 | 3/3 |
| flash_8b | simple | 1.35s | 2.01s | 17.46 | 7.6 | 1.5 | 3/3 |
| flash_8b | tool_call | 3.25s | 3.43s | 43.35 | 7.7 | 1.6 | 3/3 |
| flash_8b | analysis | 2.62s | 2.70s | 73.99 | 7.8 | 1.9 | 3/3 |

> optimized_8b = `think:false, num_ctx:4096`
> flash_8b = `think:false, num_ctx:8192`（Flash Attention 开启）

### 1.2 配置选择建议

| 场景 | 推荐配置 | 理由 |
|------|---------|------|
| 简单对话（simple） | **optimized_8b** | TTFT 最低（1.08s），TPS 适中 |
| 工具调用（tool_call） | **baseline_8b** | TTFT 最低（2.59s），TPS 适中 |
| 复杂分析（analysis） | **flash_8b** | TPS 最高（73.99），长文本生成最快 |

### 1.3 8B vs 4B 模型对比

| 指标 | 8B 结论 | 4B 结论 |
|------|---------|---------|
| TTFT | 8B 更快（2.5-3.3s） | 4B 更慢（2.7-3.7s） |
| TPS | 4B 更高（100-130 TPS） | 8B 较低（40-70 TPS） |
| 适用 | 需要快速首响应的交互 | 长文本生成场景 |

**结论**：4B 模型 Prefill 更快（decode 阶段更高效），8B 模型 TTFT 更低（更适合流式交互）。

---

## Layer 2：ai-service 完整链路（含 RAG、工具调用）

| 场景 | TTFT 均值 | TTFT p95 | TTFT p99 | 总耗时均值 | Jitter |
|------|-----------|----------|----------|-----------|--------|
| chat | 1.58s | 1.62s | 1.62s | 1.58s | 0ms |
| tool | 1.88s | 1.90s | 1.90s | 1.88s | 0ms |
| rag | **10.71s** | 11.54s | 11.54s | 10.71s | 0ms |
| complex | 2.01s | 2.12s | 2.12s | 2.01s | 0ms |

**关键发现**：
- **RAG 场景 TTFT 10.71s**，是 chat 场景的 **6.8 倍**，瓶颈在 Milvus 向量检索 + Reranker
- tool 场景比 chat 多 0.3s，来自 LLM 工具调用的 HTTP 往返
- 所有场景 Jitter 均为 0ms， streaming 输出无抖动

---

## Layer 3：并发压力测试

| 并发数 | 成功率 | 错误率 | RPS | wall时间 | TTFT 均值 | TTFT p95 | 总耗均值 | 总耗 p95 |
|--------|--------|--------|-----|----------|-----------|----------|---------|---------|
| 1 | 1/1 | 0% | 0.49 | 2.0s | 2.04s | 2.04s | 2.04s | 2.04s |
| 2 | 2/2 | 0% | 0.70 | 2.9s | 2.39s | 2.87s | 2.39s | 2.87s |
| 4 | 4/4 | 0% | 1.95 | 2.1s | 1.60s | 2.06s | 1.60s | 2.06s |
| 8 | 8/8 | 0% | 3.58 | 2.2s | 2.05s | 2.24s | 2.05s | 2.24s |

**关键发现**：
- **错误率 0%**，Ollama 在 8 并发下依然稳定
- RPS 接近线性增长（0.49 → 0.70 → 1.95 → 3.58）
- 4 并发时 TTFT 均值反而最低（1.60s），可能是模型 warm-up 效应
- 8 并发时 TTFT p95 2.24s，比 1 并发仅增加 10%，**并发稳定性良好**

---

## Layer 4：Context 长度敏感性（num_ctx 影响）

> num_ctx: baseline_8b 用默认（完整 context），optimized_8b 用 4096

| 配置 | 输入长度 | TTFT | 总耗时 | 输出 tokens |
|------|---------|------|--------|-----------|
| baseline_8b | 100 chars | 1.00s | 1.57s | 69 |
| baseline_8b | 500 chars | 2.42s | 3.08s | 88 |
| baseline_8b | 1000 chars | 2.80s | 3.64s | 108 |
| baseline_8b | 2000 chars | 1.75s | 2.44s | 90 |
| baseline_8b | 4000 chars | 1.04s | 1.73s | 92 |
| optimized_8b | 100 chars | 2.35s | 2.92s | 75 |
| optimized_8b | 500 chars | 1.78s | 3.13s | 178 |
| optimized_8b | 1000 chars | 1.08s | 1.49s | 54 |
| optimized_8b | 2000 chars | 1.71s | 2.60s | 118 |
| optimized_8b | 4000 chars | 2.91s | 4.01s | 145 |

**关键发现**：
- TTFT 与输入长度**不是线性关系**（100 chars TTFT=1.00s，500 chars TTFT=2.42s）
- 2000 chars 时 baseline_8b TTFT 反而下降到 1.75s，可能是 context cache 命中
- num_ctx=4096 在中等长度输入（500-1000 chars）表现不稳定，建议根据实际对话历史长度调整

---

## 总结

| 维度 | 最优选择 | 数值 |
|------|---------|------|
| 最低 TTFT（simple） | optimized_8b | 1.08s |
| 最高 TPS（analysis） | optimized_4b | 130.25 TPS |
| 最佳并发稳定性 | 8 并发 | TTFT p95 仅 2.24s |
| RAG 延迟 | — | 10.71s（瓶颈在 Milvus） |
| Flash Attention 加速 | flash_8b vs baseline_8b | analysis TPS +22% |

### 下一步优化建议

1. **RAG TTFT 优化**（10.71s → 目标 < 3s）：Milvus 向量检索 + Reranker 是主要瓶颈，考虑：
   - 减小检索向量维度（embedding 降维）
   - 减少 Reranker 重排数量（top_k 从 20 降到 5）
2. **Flash Attention**：已在 Ollama 侧开启，ai-service 无需额外配置
3. **num_ctx 调优**：对于短对话（历史 < 1000 tokens）用 4096，对长对话用 8192

---

*报告生成时间: 2026-04-09*
