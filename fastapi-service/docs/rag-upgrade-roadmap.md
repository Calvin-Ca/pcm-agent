# RAG 检索管道 — 当前架构与升级路线

## 当前检索链路

```
知识库目录（knowledge-base/）
    ↓
_load_documents_from_dir()          支持 .md / .txt / .pdf / .docx / .csv / .xlsx
    ↓
_split_documents()                  Markdown 按 #/##/### 标题分割 → Recursive 二次切分
    ↓                               其他格式直接 Recursive 切分（512 字，50 重叠）
初始化阶段
    ├── Milvus / FAISS 向量存储     语义向量检索（DashScope text-embedding-v2）
    └── BM25Retriever               关键词检索（纯内存，rank-bm25）
    ↓
EnsembleRetriever                   混合检索（向量 60% + BM25 40%）
    ↓
MultiQueryRetriever                 LLM 将用户问题改写为多种表述，多路召回合并
    ↓
LCEL chain                          ChatPromptTemplate → ChatOpenAI → StrOutputParser
    ↓
返回 answer + sources
```

核心代码：`fastapi-service/app/services/langchain_rag.py`

---

## 升级路线（按优先级排列）

### 1. ContextualCompressionRetriever（压缩检索结果）

**解决什么问题：** 检索到的文档块可能包含大量与问题无关的内容，浪费 LLM token，影响回答质量。

**做法：** 在 MultiQueryRetriever 外再包一层，用 LLM 从每个检索结果中只提取与问题相关的句子。

```python
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor

compressor = LLMChainExtractor.from_llm(self.llm)
compression_retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=self.multi_query_retriever,  # 包在最外层
)
```

**改动量：** ~10 行，在 `initialize()` 中 MultiQueryRetriever 之后添加。

**注意：** 每次检索会多消耗一次 LLM 调用（用于压缩），适合对回答质量要求高、对延迟不敏感的场景。

---

### 2. ParentDocumentRetriever（小块匹配 + 大块返回）

**解决什么问题：** 小块向量化匹配精度高，但返回给 LLM 的上下文太碎、缺乏完整性。

**做法：** 索引时用小块（如 128 字）做向量化，检索命中后返回对应的大块（如 1024 字）。

```python
from langchain.retrievers import ParentDocumentRetriever
from langchain.storage import InMemoryStore

parent_splitter = RecursiveCharacterTextSplitter(chunk_size=1024)
child_splitter = RecursiveCharacterTextSplitter(chunk_size=128)

store = InMemoryStore()
retriever = ParentDocumentRetriever(
    vectorstore=self.vector_store,
    docstore=store,
    child_splitter=child_splitter,
    parent_splitter=parent_splitter,
)
retriever.add_documents(documents)
```

**改动量：** 较大，需要重构 `_split_documents` 和 `_init_vector_store`。

---

### 3. SelfQueryRetriever（自动 metadata 过滤）

**解决什么问题：** 用户说"2024年的填报规则"时，应该先按 metadata 过滤（year=2024），再做语义检索。

**前提条件：** 文档的 metadata 中有明确的属性字段（如 year、category、department）。

```python
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.base import AttributeInfo

metadata_field_info = [
    AttributeInfo(name="h1", description="文档一级标题", type="string"),
    AttributeInfo(name="h2", description="文档二级标题", type="string"),
    AttributeInfo(name="source", description="来源文件名", type="string"),
]

self_query_retriever = SelfQueryRetriever.from_llm(
    llm=self.llm,
    vectorstore=self.vector_store,
    document_contents="工时管理系统知识库文档",
    metadata_field_info=metadata_field_info,
)
```

**改动量：** ~20 行，但需要确保 metadata 字段足够丰富才有效。当前 Markdown 标题分割已自动生成 h1/h2/h3 metadata，可以直接用。

---

### 4. 流式 RAG 输出

**解决什么问题：** 当前 `query()` 等 LLM 全部生成完才返回，用户体验像"卡住了"。

**做法：** 用 `chain.astream()` 替代 `chain.invoke()`，逐 token 输出。

```python
# 在 query() 方法中，改为：
async for chunk in chain.astream({"context": context, "question": question}):
    yield chunk  # 每个 chunk 是一小段文本
```

需要把 `query()` 改为 `async generator`，上层 `langgraph_agent.py` 的 `execute_rag` 节点也要适配流式输出。

**改动量：** 中等，涉及 `langchain_rag.py` 和 `langgraph_agent.py` 两个文件。

---

### 5. 新文件格式支持

在 `_load_documents_from_dir()` 中添加对应 LangChain Loader 即可，每种格式的 import 都包在 `try/except ImportError` 中，依赖缺失时自动跳过。

| 格式 | Loader | 安装依赖 |
|------|--------|----------|
| HTML | `BSHTMLLoader` | `pip install beautifulsoup4` |
| PPT | `UnstructuredPowerPointLoader` | `pip install unstructured python-pptx` |
| JSON | `JSONLoader` | `pip install jq`（可选） |
| Notion | `NotionDirectoryLoader` | 无额外依赖 |
| Confluence | `ConfluenceLoader` | `pip install atlassian-python-api` |

添加模式：
```python
# ── HTML ──────────────────────────────────────────────
try:
    from langchain_community.document_loaders import BSHTMLLoader

    for html_file in path.rglob("*.html"):
        try:
            loader = BSHTMLLoader(str(html_file), open_encoding="utf-8")
            docs.extend(loader.load())
        except Exception as e:
            logger.warning(f"加载 HTML 失败 {html_file}: {e}")
except ImportError:
    html_count = len(list(path.rglob("*.html")))
    if html_count:
        logger.warning(f"发现 {html_count} 个 HTML 文件但 beautifulsoup4 未安装，跳过")
```

---

## 混合检索进一步优化思路

如果未来想对检索质量做更精细的调优：

1. **调整 EnsembleRetriever 权重：** 当前 向量:BM25 = 6:4，如果知识库偏专业术语（如制度文档），可以提高 BM25 权重到 5:5 甚至 4:6，因为专业术语的关键词匹配比语义匹配更准。

2. **中文分词优化：** BM25Retriever 默认按空格分词，对中文效果差。可以在构建 BM25 时传入自定义分词函数：
   ```python
   import jieba
   bm25 = BM25Retriever.from_documents(
       documents,
       preprocess_func=lambda text: list(jieba.cut(text)),
       k=5,
   )
   ```
   需要 `pip install jieba`。

3. **Reranker（重排序）：** 在检索后、送入 LLM 前，用专门的 cross-encoder 模型对结果重新排序。比简单合并分数更准确：
   ```python
   from langchain.retrievers.document_compressors import CrossEncoderReranker
   from langchain_community.cross_encoders import HuggingFaceCrossEncoder

   model = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")
   compressor = CrossEncoderReranker(model=model, top_n=5)
   ```
   需要 GPU 或较大内存。

4. **知识库增量更新：** 当前 `drop_old=True` 每次重启全量重建。改为增量（检查文件 hash 是否变化，只更新变化的文档），可以显著缩短重启时间。
