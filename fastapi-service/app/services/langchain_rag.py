"""
LangChain RAG 服务

基于 LangChain 框架实现混合检索（Milvus 向量检索 + BM25 关键词检索）RAG 管道。
向量存储优先使用 Milvus，不可用时自动降级为 FAISS 内存存储。
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

# 全局 RAG 服务实例
_rag_service: Optional["LangChainRAGService"] = None


def _load_documents_from_dir(kb_path: str) -> List[Document]:
    """
    从目录加载文档，使用 LangChain 文档加载器。
    支持 .md / .txt / .pdf / .docx / .csv 文件，递归扫描子目录。
    """
    from langchain_community.document_loaders import DirectoryLoader, TextLoader

    path = Path(kb_path)
    if not path.exists():
        logger.warning(f"知识库目录不存在: {kb_path}")
        return []

    docs: List[Document] = []

    # ── 文本类：.md / .txt ───────────────────────────────────────────────────
    for pattern in ("**/*.md", "**/*.txt"):
        try:
            loader = DirectoryLoader(
                str(path),
                glob=pattern,
                loader_cls=TextLoader,
                loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True},
                silent_errors=True,
            )
            docs.extend(loader.load())
        except Exception as e:
            logger.warning(f"加载 {pattern} 失败: {e}")

    # ── PDF ───────────────────────────────────────────────────────────────────
    try:
        from langchain_community.document_loaders import PyPDFLoader

        for pdf_file in path.rglob("*.pdf"):
            try:
                loader = PyPDFLoader(str(pdf_file))
                docs.extend(loader.load())
            except Exception as e:
                logger.warning(f"加载 PDF 失败 {pdf_file}: {e}")
    except ImportError:
        pdf_count = len(list(path.rglob("*.pdf")))
        if pdf_count:
            logger.warning(f"发现 {pdf_count} 个 PDF 文件但 pypdf 未安装，跳过。pip install pypdf")

    # ── Word (.docx) ─────────────────────────────────────────────────────────
    try:
        from langchain_community.document_loaders import Docx2txtLoader

        for docx_file in path.rglob("*.docx"):
            try:
                loader = Docx2txtLoader(str(docx_file))
                docs.extend(loader.load())
            except Exception as e:
                logger.warning(f"加载 Word 文档失败 {docx_file}: {e}")
    except ImportError:
        docx_count = len(list(path.rglob("*.docx")))
        if docx_count:
            logger.warning(f"发现 {docx_count} 个 Word 文件但 docx2txt 未安装，跳过。pip install docx2txt")

    # ── CSV ───────────────────────────────────────────────────────────────────
    try:
        from langchain_community.document_loaders import CSVLoader

        for csv_file in path.rglob("*.csv"):
            try:
                loader = CSVLoader(str(csv_file), encoding="utf-8")
                docs.extend(loader.load())
            except Exception as e:
                logger.warning(f"加载 CSV 失败 {csv_file}: {e}")
    except ImportError:
        pass  # csv 是标准库，CSVLoader 一般都可用

    # ── Excel (.xlsx) ────────────────────────────────────────────────────────
    try:
        from langchain_community.document_loaders import UnstructuredExcelLoader

        for xlsx_file in path.rglob("*.xlsx"):
            try:
                loader = UnstructuredExcelLoader(str(xlsx_file))
                docs.extend(loader.load())
            except Exception as e:
                logger.warning(f"加载 Excel 失败 {xlsx_file}: {e}")
    except ImportError:
        xlsx_count = len(list(path.rglob("*.xlsx")))
        if xlsx_count:
            logger.warning(f"发现 {xlsx_count} 个 Excel 文件但 openpyxl/unstructured 未安装，跳过")

    logger.info(f"从 {kb_path} 加载了 {len(docs)} 个文档")
    return docs


def _split_documents(docs: List[Document], chunk_size: int = 512, chunk_overlap: int = 50) -> List[Document]:
    """
    智能文档分割：
    - Markdown 文件：先按标题层级分割（保留标题上下文到 metadata），再对大块做 Recursive 分割
    - 其他文件：直接用 RecursiveCharacterTextSplitter
    """
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )

    # Markdown 标题分割配置
    md_headers_to_split_on = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=md_headers_to_split_on,
        strip_headers=False,  # 保留标题文本在 content 中，便于检索命中
    )

    # 二次分割器：对超过 chunk_size 的块做进一步切分
    recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],
    )

    chunks: List[Document] = []

    for doc in docs:
        source = doc.metadata.get("source", "")
        is_markdown = source.lower().endswith((".md", ".markdown"))

        if is_markdown:
            # 第一步：按标题分割，每个块的 metadata 自动带有 h1/h2/h3
            try:
                md_chunks = md_splitter.split_text(doc.page_content)
                for md_chunk in md_chunks:
                    # 合并原始 metadata（source 等）和标题 metadata（h1/h2/h3）
                    merged_meta = {**doc.metadata, **md_chunk.metadata}
                    md_chunk.metadata = merged_meta

                # 第二步：大块再做 Recursive 切分
                sub_chunks = recursive_splitter.split_documents(md_chunks)
                chunks.extend(sub_chunks)
            except Exception as e:
                logger.warning(f"Markdown 标题分割失败，降级为普通分割: {e}")
                chunks.extend(recursive_splitter.split_documents([doc]))
        else:
            chunks.extend(recursive_splitter.split_documents([doc]))

    logger.info(f"文档分割完成：{len(docs)} 篇 → {len(chunks)} 个块")
    return chunks


class LangChainRAGService:
    """
    基于 LangChain 的 RAG 服务。

    检索策略：
        EnsembleRetriever（权重 6:4）
            ├── MilvusVectorStore（语义向量检索，fallback: FAISS）
            └── BM25Retriever（关键词检索）

    生成策略：
        ChatOpenAI（DashScope OpenAI 兼容接口）via LCEL chain
    """

    def __init__(self) -> None:
        self.embeddings = None
        self.vector_store = None
        self.bm25_retriever = None
        self.ensemble_retriever = None
        self.llm = None
        self._initialized = False
        self._use_milvus = False

    async def initialize(self, documents: List[Document]) -> None:
        """异步初始化 RAG 管道"""
        if not documents:
            logger.warning("知识库文档为空，RAG 服务不可用")
            return

        api_key = os.getenv("CHAT_LLM_API_KEY", "")
        api_base = os.getenv(
            "CHAT_LLM_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        chat_model = os.getenv("CHAT_LLM_MODEL", "qwen-plus")

        if not api_key:
            logger.warning("未配置 CHAT_LLM_API_KEY，RAG 服务不可用")
            return

        # 1. 初始化 Embeddings（DashScope text-embedding-v2，OpenAI 兼容）
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        # langchain-openai: OpenAIEmbeddings 用 openai_api_key / openai_api_base
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-v2",
            openai_api_key=api_key,
            openai_api_base=api_base,
        )
        logger.info("Embeddings 初始化完成（DashScope text-embedding-v2）")

        # 2. 初始化 LLM
        # langchain-openai: ChatOpenAI 用 openai_api_key / openai_api_base
        self.llm = ChatOpenAI(
            model=chat_model,
            openai_api_key=api_key,
            openai_api_base=api_base,
            temperature=0.1,
            max_tokens=1000,
        )
        logger.info(f"LLM 初始化完成（{chat_model}）")

        # 3. 初始化向量存储（Milvus 优先，不可用时降级 FAISS）
        vector_retriever = await self._init_vector_store(documents)

        # 4. 初始化 BM25 检索器（纯内存，无需外部依赖）
        from langchain_community.retrievers import BM25Retriever

        self.bm25_retriever = await asyncio.to_thread(
            BM25Retriever.from_documents, documents, k=5
        )
        logger.info("BM25 检索器初始化完成")

        # 5. 混合检索（向量 60% + BM25 40%）
        from langchain_classic.retrievers import EnsembleRetriever

        self.ensemble_retriever = EnsembleRetriever(
            retrievers=[vector_retriever, self.bm25_retriever],
            weights=[0.6, 0.4],
        )
        logger.info("混合检索器（EnsembleRetriever）初始化完成")

        # 6. MultiQueryRetriever：让 LLM 将用户问题改写为多种表述，提升召回率
        try:
            from langchain.retrievers.multi_query import MultiQueryRetriever

            self.multi_query_retriever = MultiQueryRetriever.from_llm(
                retriever=self.ensemble_retriever,
                llm=self.llm,
            )
            logger.info("MultiQueryRetriever 初始化完成（基于混合检索器）")
        except Exception as e:
            logger.warning(f"MultiQueryRetriever 初始化失败，降级为普通混合检索: {e}")
            self.multi_query_retriever = None

        self._initialized = True
        backend = "Milvus" if self._use_milvus else "FAISS"
        logger.info(f"✅ LangChain RAG 服务初始化完成（向量后端: {backend}，文档块: {len(documents)}）")

    async def _init_vector_store(self, documents: List[Document]):
        """初始化向量存储，Milvus 优先，降级 FAISS"""
        milvus_host = os.getenv("MILVUS_HOST", "milvus")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        try:
            from langchain_milvus import Milvus

            self.vector_store = await asyncio.to_thread(
                Milvus.from_documents,
                documents=documents,
                embedding=self.embeddings,
                connection_args={"uri": milvus_uri},
                collection_name="knowledge_base",
                drop_old=True,
            )
            self._use_milvus = True
            logger.info(f"Milvus 向量存储初始化完成（{milvus_uri}）")
        except Exception as e:
            logger.warning(f"Milvus 不可用（{e}），降级为 FAISS 内存存储")
            from langchain_community.vectorstores import FAISS

            self.vector_store = await asyncio.to_thread(
                FAISS.from_documents,
                documents=documents,
                embedding=self.embeddings,
            )
            self._use_milvus = False
            logger.info("FAISS 内存向量存储初始化完成")

        return self.vector_store.as_retriever(search_kwargs={"k": 5})

    async def query(self, question: str) -> Dict[str, Any]:
        """
        执行 RAG 查询：检索 → 生成

        Returns:
            {
                "success": bool,
                "response": str,
                "sources": List[{"source": str, "type": str}],
                "context_used": bool,
                "retrieved_count": int,
            }
        """
        if not self._initialized:
            return {
                "success": False,
                "response": "RAG 服务未初始化，请稍后重试。",
                "sources": [],
                "context_used": False,
            }

        try:
            # 1. 混合检索（优先 MultiQuery，降级为普通 Ensemble）
            retriever = self.multi_query_retriever or self.ensemble_retriever
            docs: List[Document] = await asyncio.to_thread(
                retriever.invoke, question
            )

            if not docs:
                return {
                    "success": True,
                    "response": "抱歉，我在知识库中没有找到相关信息。您可以尝试换个问法或联系管理员。",
                    "sources": [],
                    "context_used": False,
                }

            # 2. 构建上下文（取前 5 块，避免 token 超限）
            context = "\n\n".join(doc.page_content for doc in docs[:5])

            # 3. LCEL 生成链
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是工时管理系统的AI助手。请根据以下知识库内容回答用户的问题，"
                        "回答要简洁、准确。如果知识库内容不足以完整回答，请如实说明。\n\n"
                        "知识库内容：\n{context}",
                    ),
                    ("human", "{question}"),
                ]
            )
            chain = prompt | self.llm | StrOutputParser()
            answer: str = await asyncio.to_thread(
                chain.invoke, {"context": context, "question": question}
            )

            # 4. 来源去重
            sources: List[Dict[str, str]] = []
            seen: set = set()
            for doc in docs:
                src = doc.metadata.get("source", "未知来源")
                if src not in seen:
                    sources.append(
                        {
                            "source": Path(src).name if src != "未知来源" else src,
                            "type": doc.metadata.get("type", "document"),
                        }
                    )
                    seen.add(src)

            return {
                "success": True,
                "response": answer,
                "sources": sources,
                "context_used": True,
                "retrieved_count": len(docs),
            }

        except Exception as e:
            logger.error(f"RAG 查询失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "response": "抱歉，生成回答时出现错误，请稍后重试。",
                "sources": [],
            }


# ─── 模块级便捷接口 ─────────────────────────────────────────────────────────────

async def initialize_langchain_rag(kb_path: str = "knowledge-base") -> Dict[str, Any]:
    """
    初始化全局 LangChain RAG 服务。

    Args:
        kb_path: 知识库目录路径（宿主机或容器内挂载路径）

    Returns:
        {"success": bool, "loaded_files": int, "total_chunks": int, ...}
    """
    global _rag_service

    try:
        # 使用 LangChain 文档加载器加载知识库
        raw_docs = await asyncio.to_thread(_load_documents_from_dir, kb_path)
        if not raw_docs:
            return {
                "success": False,
                "error": f"未在 {kb_path} 找到任何文档",
                "loaded_files": 0,
                "total_chunks": 0,
            }

        # 统计文件数（去重 source）
        loaded_files = len({doc.metadata.get("source", "") for doc in raw_docs})

        # 分割文档
        chunks = await asyncio.to_thread(_split_documents, raw_docs)

        # 初始化 RAG 服务
        _rag_service = LangChainRAGService()
        await _rag_service.initialize(chunks)

        return {
            "success": True,
            "loaded_files": loaded_files,
            "total_chunks": len(chunks),
        }

    except Exception as e:
        logger.error(f"LangChain RAG 初始化失败: {e}", exc_info=True)
        return {"success": False, "error": str(e), "loaded_files": 0, "total_chunks": 0}


async def langchain_rag_query(question: str) -> Dict[str, Any]:
    """执行 RAG 查询（全局便捷函数）"""
    global _rag_service
    if not _rag_service:
        return {
            "success": False,
            "response": "RAG 服务未初始化，请先调用 initialize_langchain_rag()",
            "sources": [],
        }
    return await _rag_service.query(question)
