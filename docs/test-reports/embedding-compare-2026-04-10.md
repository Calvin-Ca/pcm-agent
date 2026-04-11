# Embedding 模型对比报告（2026-04-10）

**知识库**：4 篇文档 → 36 个 chunks
**评估集**：24 条 query（来自 knowledge_qa.json）
**判命中规则**：top-k 中出现至少一个 chunk 来自 expected_doc 即算命中

---

## 一、核心指标对比

| 指标 | bge-large-zh-v1.5 (vLLM 8097) | bge-base-zh-v1.5 (vLLM 8098) | qwen3-embedding:8b (Ollama) |
|------|------|------|------|
| 向量维度 | 1024 | 768 | 4096 |
| 冷启动 (s) | 0.067 | **0.055** | 2.57 |
| Chunks 编码 (ms/块) | 2.1 | **2.0** | 32.1 |
| 单 query 延迟 (ms) | 18.0 | **13.9** | 155.0 |
| Recall@3 (%) | **100.0** | 87.5 | 70.8 |
| Recall@5 (%) | **100.0** | 95.8 | 75.0 |
| Recall@10 (%) | **100.0** | 100.0 | 100.0 |

---

## 二、结论

**bge-large-zh-v1.5 (vLLM, port 8097) 全面胜出**：
- 100% 召回率（Recall@5）
- 冷启动仅 0.067s
- 查询延迟 18ms
- 向量维度 1024（比 qwen3-embedding 小 4 倍）

**建议**：切换 RAG embedding 到 `http://172.19.3.136:8097/v1`（bge-large）

---

## 三、失败用例（bge-base）

| ID | query | expected | top5_sources |
|----|-------|----------|--------------|
| kq_001 | 工时截止日期是哪天 | 工时填报管理制度.md | FAQ, 假期与加班政策, FAQ... |
