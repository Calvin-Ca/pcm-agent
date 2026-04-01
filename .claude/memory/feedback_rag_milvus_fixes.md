---
name: RAG/Milvus 兼容性修复经验
description: 2026-03-31 排查 langchain_milvus + pymilvus 2.6 不兼容问题的完整过程与结论
type: feedback
---

## pymilvus 2.6 + langchain_milvus 0.3.3 兼容性 bug

**结论（已验证）**：pymilvus 2.6 的 `MilvusClient._using = "cm-{id(self._handler)}"` 不注册到 ORM 连接池（`connections._alias_handlers`）。`langchain_milvus 0.3.3` 内部 `col` 属性调用 `Collection(name, using=self.alias)` 走 ORM 路径 → `ConnectionNotExistException`。

**修复方案**（`langchain_rag.py` `_init_vector_store`）：
1. 预注册 ORM 连接：`connections.connect(alias="lc_default", uri=milvus_uri)`
2. Monkey-patch `MilvusClient.__init__`，在原始 init 后把 `self._using` 改为 `"lc_default"`
3. 调用 `Milvus.from_documents(...)` 后恢复原始 `__init__`

**Why:** `host`/`port` 格式和 `uri` 格式结果相同（`_using` 都是 `"cm-xxx"`），不能绕过此问题。`asyncio.to_thread` 不是根因，直接同步调用也会报错。

**How to apply:** 遇到 `ConnectionNotExistException: should create connection first` 时，直接看 pymilvus 版本是否 >=2.6，是则需要此 monkey-patch。

---

## DashScope Embedding 兼容性

`langchain_openai.OpenAIEmbeddings` 默认用 tiktoken 把文本 tokenize 成整数数组再发给 API，DashScope 只接受原始字符串，返回 400。

**修复**：`check_embedding_ctx_length=False`（跳过 tokenize，直接发文本）+ `chunk_size=20`（DashScope 单批≤25条）。

**Why:** `_split_documents(chunk_size=512)` 已保证分块≤512字符，不存在超长文本风险，`check_embedding_ctx_length=False` 是根本修复而非治标。

---

## langchain 包结构变化

`langchain.retrievers` 在当前版本不存在，MultiQueryRetriever 和 CrossEncoderReranker 都在 `langchain_classic.retrievers`。

**How to apply:** 遇到 `No module named 'langchain.retrievers'` 改为 `langchain_classic.retrievers`。

---

## CrossEncoderReranker 模型下载

`HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")` 初始化时立即触发下载（~270MB）。已加 `ENABLE_RERANKER=true` 环境变量开关，默认关闭。
