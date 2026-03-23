# 废弃代码归档

此目录存放已被新实现替代、仅保留供学习参考的历史代码。
这些文件**不再被生产代码引用**，不会影响运行。

| 文件 | 原路径 | 替代方案 | 说明 |
|------|--------|----------|------|
| `stream_response.py` | `app/services/` | `app/services/langgraph_agent.py` → `stream_agent_response()` | 早期 SSE 流式响应实现。LangGraph 版本新增了 Redis 短期记忆、用户长期记忆、多轮上下文、会话日志等能力。 |
| `document_loader.py` | `app/services/` | `app/services/langchain_rag.py` → `_load_documents_from_dir()` | 早期手写的文档加载器（PDF/Word/Markdown/TXT）。已被 LangChain 内置 Loader（PyPDFLoader、Docx2txtLoader 等）替代。 |
| `knowledge_loader.py` | `app/services/` | `app/services/langchain_rag.py` → `initialize_langchain_rag()` | 早期知识库加载流程，依赖上面的 `document_loader.py`。已被 LangChain RAG 初始化流程替代。 |
| `tool_initialization.py` | `app/core/` | `app/main.py` lifespan 中直接初始化 | 早期工具注册入口，包含知识库初始化调用。当前 `main.py` 通过模块导入自动注册工具，不再需要此文件。 |
| `rag_service.py` | `app/services/` | `app/services/langchain_rag.py` | 早期 RAG 服务封装，依赖旧的 `search_knowledge` 工具。已被 LangChain RAG 服务替代。 |
| `vector_store.py` | `app/services/` | `app/services/langchain_rag.py` → Milvus/FAISS 向量存储 | 早期手写的向量存储管理器。已被 LangChain 的 VectorStore 抽象替代。 |
| `search_knowledge.py` | `app/tools/` | `app/services/langchain_rag.py` → `langchain_rag_query()` | 早期知识库搜索工具，依赖 `vector_store.py`。RAG 查询现在通过 LangGraph 的 `execute_rag` 节点直接调用 LangChain RAG。 |

## 学习建议

- **想了解原始 SSE 流程：** 看 `stream_response.py`，重点关注 `generate_stream()` 方法中如何拼装 `event: xxx\ndata: {...}\n\n` 格式
- **想了解手写文档分块思路：** 看 `document_loader.py` 的 `ChunkSplitter` 类，对比 LangChain 的 `RecursiveCharacterTextSplitter`
- **想了解当前生产实现：** 直接看对应的替代文件，以及 `docs/rag-upgrade-roadmap.md` 中的架构图
