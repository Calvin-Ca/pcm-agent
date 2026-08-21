"""
LangChain RAG 服务

基于 LangChain 框架实现混合检索（Milvus 向量检索 + BM25 关键词检索）RAG 管道。
向量存储优先使用 Milvus，不可用时自动降级为 FAISS 内存存储。

当前检索链路及后续升级路线详见：docs/rag-upgrade-roadmap.md
"""

import asyncio
from contextlib import contextmanager
import errno
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, AsyncGenerator, Dict, Iterator, List, Optional, TextIO

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.services.prompt_manager import get_prompt_manager
from app.services.reasoning_filter import (
    REASONING_FILTER_FALLBACK,
    ReasoningTraceStreamFilter,
    strip_reasoning_trace,
)

logger = logging.getLogger(__name__)

# 全局 RAG 服务实例
_rag_service: Optional["LangChainRAGService"] = None


@contextmanager
def _exclusive_file_lock(lock_file: TextIO) -> Iterator[None]:
    """跨进程独占文件锁，兼容 Linux 容器和 Windows 本地开发。"""
    if os.name == "nt":
        import msvcrt

        # msvcrt.locking 从当前文件位置开始锁定指定字节；确保第 0 字节存在。
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("\0")
            lock_file.flush()

        while True:
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                time.sleep(0.1)

        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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

    检索策略（三级管道）：
        1. MultiQueryRetriever（LLM 改写为多种表述，提升召回）
        2. EnsembleRetriever（混合检索，向量 60% + BM25 40%）
        3. CrossEncoderReranker（精排，取 Top 5）

    生成策略：
        ChatOpenAI（DashScope OpenAI 兼容接口）via LCEL chain
    """

    def __init__(self) -> None:
        self.embeddings = None
        self.vector_store = None
        self.bm25_retriever = None
        self.ensemble_retriever = None
        self.multi_query_retriever = None
        self.reranker: Optional[Any] = None  # CrossEncoderReranker
        self.llm = None
        self._initialized = False
        self._use_milvus = False
        self._reranker_enabled = False

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

        # 1. 初始化 Embeddings
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        # ── Embedding 模型选择（"ollama" / "vllm" / "dashscope"）───────────────
        # vLLM bge-large-zh-v1.5（推荐）：Recall@5=100%，冷启动 0.07s，18ms/query
        # Ollama qwen3-embedding:8b：Recall@5=75%，冷启动 2.6s，155ms/query（不推荐）
        # DashScope text-embedding-v2：FP16，1536 维，云端 API 无冷启动
        USE_EMBEDDING = "vllm"   # ← 切换时改这里

        if USE_EMBEDDING == "vllm":
            import httpx

            # Windows 上 httpx 会从系统设置读取 Clash HTTP 代理，但不会可靠应用
            # ProxyOverride 中的内网绕过规则。vLLM 是固定内网服务，显式直连，避免
            # /embeddings 被发往 Clash 后每次等待 30 秒并最终返回 502/timeout。
            self.embeddings = OpenAIEmbeddings(
                model="/model",
                openai_api_key="EMPTY",
                openai_api_base="http://172.19.3.136:8097/v1",
                chunk_size=100,
                check_embedding_ctx_length=False,
                http_client=httpx.Client(trust_env=False),
                http_async_client=httpx.AsyncClient(trust_env=False),
            )
            logger.info("Embeddings 初始化完成（vLLM bge-large-zh-v1.5, dim=1024）")
        elif USE_EMBEDDING == "ollama":
            ollama_api_key = os.getenv("CHAT_LLM_API_KEY", "ollama")
            ollama_api_base = os.getenv("CHAT_LLM_API_BASE", "http://172.19.3.136:11434/v1")
            self.embeddings = OpenAIEmbeddings(
                model="qwen3-embedding:8b",
                openai_api_key=ollama_api_key,
                openai_api_base=ollama_api_base,
                chunk_size=100,
                check_embedding_ctx_length=False,
            )
            logger.info("Embeddings 初始化完成（Ollama qwen3-embedding:8b, dim=4096）")
        else:
            dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
            dashscope_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if dashscope_api_key:
                self.embeddings = OpenAIEmbeddings(
                    model="text-embedding-v2",
                    openai_api_key=dashscope_api_key,
                    openai_api_base=dashscope_api_base,
                    chunk_size=20,
                    check_embedding_ctx_length=False,
                )
                logger.info("Embeddings 初始化完成（DashScope text-embedding-v2, dim=1536）")
            else:
                logger.warning("未配置 Embedding API Key，RAG 服务降级")
                return

        # 2. 初始化 LLM
        # langchain-openai: ChatOpenAI 用 openai_api_key / openai_api_base
        api_base_lower = (api_base or "").lower()
        if "11434" in api_base_lower:
            rag_extra_body = {"think": False}
        elif "dashscope" in api_base_lower:
            rag_extra_body = {"enable_thinking": False}
        else:
            rag_extra_body = {
                "chat_template_kwargs": {"enable_thinking": False}
            }
        self.llm = ChatOpenAI(
            model=chat_model,
            openai_api_key=api_key,
            openai_api_base=api_base,
            temperature=0.1,
            max_tokens=800,
            extra_body=rag_extra_body,
        )
        logger.info(f"LLM 初始化完成（{chat_model}）")

        # 3. 初始化向量存储（Milvus 优先，不可用时降级 FAISS）
        vector_retriever = await self._init_vector_store(documents)

        # 4. 初始化 BM25 检索器（纯内存，无需外部依赖）
        from langchain_community.retrievers import BM25Retriever

        def _jieba_tokenizer(text: str):
            """使用 jieba 分词，提升中文专业词汇的 BM25 召回率。"""
            import jieba
            return list(jieba.cut(text))

        self.bm25_retriever = await asyncio.to_thread(
            BM25Retriever.from_documents, documents, k=5,
            preprocess_func=_jieba_tokenizer,
        )
        logger.info("BM25 检索器初始化完成（jieba 分词）")

        # 5. 混合检索（向量 60% + BM25 40%）
        from langchain_classic.retrievers import EnsembleRetriever

        self.ensemble_retriever = EnsembleRetriever(
            retrievers=[vector_retriever, self.bm25_retriever],
            weights=[0.6, 0.4],
        )
        logger.info("混合检索器（EnsembleRetriever）初始化完成")

        # ── MultiQueryRetriever 开关 ───────────────────────────────────────────
        # 设置为 True 启用多查询检索（提升召回率，但显著增加 TTFT）
        # 设置为 False 跳过 LLM 改写，直接用 EnsembleRetriever（TTFT 更低）
        USE_MULTI_QUERY = False  # ← 切换时改这里

        if USE_MULTI_QUERY:
            try:
                from langchain_classic.retrievers.multi_query import MultiQueryRetriever

                self.multi_query_retriever = MultiQueryRetriever.from_llm(
                    retriever=self.ensemble_retriever,
                    llm=self.llm,
                )
                logger.info("MultiQueryRetriever 初始化完成（基于混合检索器）")
            except Exception as e:
                logger.warning(f"MultiQueryRetriever 初始化失败，降级为普通混合检索: {e}")
                self.multi_query_retriever = None
        else:
            self.multi_query_retriever = None
            logger.info("MultiQueryRetriever 已禁用（USE_MULTI_QUERY=False），直接使用 EnsembleRetriever")

        self._initialized = True
        backend = "Milvus" if self._use_milvus else "FAISS"
        logger.info(f"✅ LangChain RAG 服务初始化完成（向量后端: {backend}，文档块: {len(documents)}）")

        # ── CrossEncoder Reranker（精排）切换开关 ──────────────────────────────────
        # 设置为 True 启用精排（提升答案质量，但增加 TTFT）
        # 设置为 False 跳过精排，直接用 EnsembleRetriever 结果（TTFT 更低）
        USE_RERANKER = False  # ← 切换时改这里

        if USE_RERANKER:
            try:
                from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
                from langchain_community.cross_encoders import HuggingFaceCrossEncoder

                model = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")
                self.reranker = CrossEncoderReranker(model=model, top_n=5)
                self._reranker_enabled = True
                logger.info("CrossEncoderReranker 初始化完成（BAAI/bge-reranker-base）")
            except Exception as e:
                logger.warning(f"CrossEncoderReranker 初始化失败，降级为无重排序: {e}")
                self.reranker = None
                self._reranker_enabled = False
        else:
            self.reranker = None
            self._reranker_enabled = False
            logger.info("CrossEncoderReranker 已禁用（USE_RERANKER=False）")

    async def _init_vector_store(self, documents: List[Document]):
        """初始化向量存储，Milvus 优先，降级 FAISS"""
        milvus_host = os.getenv("MILVUS_HOST", "milvus")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        try:
            from langchain_milvus import Milvus
            from pymilvus import MilvusClient as _MilvusClient, connections

            # pymilvus 2.6 的 MilvusClient._using = "cm-{id}"，不在 ORM 连接池中。
            # langchain_milvus 0.3.3 内部用 Collection(using=self.alias) 走 ORM 路径，
            # 导致 ConnectionNotExistException。
            # 修复：预注册 "lc_default" ORM 连接，然后让 MilvusClient._using 指向它。
            ORM_ALIAS = "lc_default"
            connections.connect(alias=ORM_ALIAS, uri=milvus_uri)

            _orig_init = _MilvusClient.__init__

            def _patched_init(self, *args, **kwargs):
                _orig_init(self, *args, **kwargs)
                self._using = ORM_ALIAS

            _MilvusClient.__init__ = _patched_init
            try:
                # Uvicorn workers run lifespan concurrently. Serialize the destructive
                # collection rebuild and let the remaining workers attach to it.
                lock_path = Path(
                    os.getenv(
                        "RAG_MILVUS_INIT_LOCK",
                        str(Path(tempfile.gettempdir()) / "workhour-rag-milvus-init.lock"),
                    )
                )
                try:
                    startup_token = Path("/proc/1/stat").read_text().split()[21]
                except (OSError, IndexError):
                    startup_token = str(os.getppid())
                ready_path = Path(f"{lock_path}.{startup_token}.ready")
                lock_path.parent.mkdir(parents=True, exist_ok=True)

                with lock_path.open("a+") as lock_file:
                    with _exclusive_file_lock(lock_file):
                        if ready_path.exists():
                            self.vector_store = Milvus(
                                embedding_function=self.embeddings,
                                connection_args={"uri": milvus_uri},
                                collection_name="knowledge_base",
                                enable_dynamic_field=True,
                            )
                            logger.info(
                                "Milvus collection initialized by another worker; "
                                "reusing knowledge_base"
                            )
                        else:
                            self.vector_store = Milvus.from_documents(
                                documents=documents,
                                embedding=self.embeddings,
                                connection_args={"uri": milvus_uri},
                                collection_name="knowledge_base",
                                drop_old=True,
                                enable_dynamic_field=True,
                            )
                            ready_path.touch()
            finally:
                _MilvusClient.__init__ = _orig_init
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
        import time as _time_module

        # Lazy import metrics (avoid circular dependency)
        try:
            from app.core.metrics import RAG_QUERY_COUNT, RAG_QUERY_LATENCY
            _has_metrics = True
        except ImportError:
            _has_metrics = False

        _start = _time_module.monotonic()
        _query_status = "success"

        if not self._initialized:
            if _has_metrics:
                RAG_QUERY_COUNT.labels(status="error").inc()
                RAG_QUERY_LATENCY.observe(_time_module.monotonic() - _start)
            return {
                "success": False,
                "response": "RAG 服务未初始化，请稍后重试。",
                "sources": [],
                "context_used": False,
            }

        try:
            # 1. 混合检索（优先 MultiQuery，降级为普通 Ensemble）
            retriever = self.multi_query_retriever or self.ensemble_retriever
            _t0 = _time_module.monotonic()
            docs: List[Document] = await asyncio.to_thread(
                retriever.invoke, question
            )
            _t1 = _time_module.monotonic()
            logger.info(f"[RAG-Timing] 检索阶段耗时: {_t1 - _t0:.3f}s")

            # 2. 重排序（如果启用了 Reranker）
            if self._reranker_enabled and self.reranker and docs:
                try:
                    docs = await asyncio.to_thread(
                        self.reranker.compress_documents, docs, question
                    )
                    logger.debug(f"CrossEncoderReranker 重排序完成，返回 {len(docs)} 个文档")
                except Exception as e:
                    logger.warning(f"重排序失败，使用原始检索结果: {e}")

            if not docs:
                if _has_metrics:
                    RAG_QUERY_COUNT.labels(status="no_results").inc()
                    RAG_QUERY_LATENCY.observe(_time_module.monotonic() - _start)
                return {
                    "success": True,
                    "response": "抱歉，我在知识库中没有找到相关信息。您可以尝试换个问法或联系管理员。",
                    "sources": [],
                    "context_used": False,
                }

            # 2. 构建上下文（取前 5 块，避免 token 超限）
            context = "\n\n".join(doc.page_content for doc in docs[:5])

            # 3. LCEL 生成链
            prompt = get_prompt_manager().get_chat_template("rag") or ChatPromptTemplate.from_messages(
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
            _t2 = _time_module.monotonic()
            answer: str = await asyncio.to_thread(
                chain.invoke, {"context": context, "question": question}
            )
            answer = strip_reasoning_trace(answer) or REASONING_FILTER_FALLBACK
            _t3 = _time_module.monotonic()
            logger.info(f"[RAG-Timing] 生成阶段耗时: {_t3 - _t2:.3f}s | 总耗时: {_t3 - _start:.3f}s")

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

            if _has_metrics:
                RAG_QUERY_COUNT.labels(status="success").inc()
                RAG_QUERY_LATENCY.observe(_time_module.monotonic() - _start)
            return {
                "success": True,
                "response": answer,
                "sources": sources,
                "context_used": True,
                "retrieved_count": len(docs),
            }

        except Exception as e:
            _query_status = "error"
            logger.error(f"RAG 查询失败: {e}", exc_info=True)
            if _has_metrics:
                RAG_QUERY_COUNT.labels(status="error").inc()
                RAG_QUERY_LATENCY.observe(_time_module.monotonic() - _start)
            return {
                "success": False,
                "error": str(e),
                "response": "抱歉，生成回答时出现错误，请稍后重试。",
                "sources": [],
            }

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
            import time as _time_module
            _rag_start = _time_module.monotonic()

            # 1. 检索阶段（同步，通常 <1s）
            yield {"type": "retrieval", "message": "正在查询知识库..."}

            retriever = self.multi_query_retriever or self.ensemble_retriever
            _t0 = _time_module.monotonic()
            docs: List[Document] = await asyncio.to_thread(retriever.invoke, question)
            _t1 = _time_module.monotonic()
            logger.info(f"[RAG-Timing] 检索阶段耗时: {_t1 - _t0:.3f}s")

            if self._reranker_enabled and self.reranker and docs:
                try:
                    docs = await asyncio.to_thread(
                        self.reranker.compress_documents, docs, question
                    )
                except Exception as e:
                    logger.warning(f"重排序失败，使用原始检索结果: {e}")

            if not docs:
                yield {
                    "type": "chunk",
                    "content": "抱歉，我在知识库中没有找到相关信息。您可以尝试换个问法或联系管理员。",
                }
                yield {"type": "done", "sources": [], "retrieved_count": 0}
                return

            # 2. 构建上下文
            context = "\n\n".join(doc.page_content for doc in docs[:5])

            # 3. 流式生成（使用 self.llm.astream()）
            prompt = get_prompt_manager().get_chat_template("rag") or ChatPromptTemplate.from_messages([
                (
                    "system",
                    "你是工时管理系统的AI助手。请根据以下知识库内容回答用户的问题，"
                    "回答要简洁、准确。如果知识库内容不足以完整回答，请如实说明。\n\n"
                    "知识库内容：\n{context}",
                ),
                ("human", "{question}"),
            ])
            chain = prompt | self.llm | StrOutputParser()

            _t2 = _time_module.monotonic()
            reasoning_filter = ReasoningTraceStreamFilter()
            visible_content_emitted = False
            async for chunk in chain.astream({"context": context, "question": question}):
                visible = reasoning_filter.feed(chunk)
                if visible:
                    visible_content_emitted = True
                    yield {"type": "chunk", "content": visible}
            remaining = reasoning_filter.finish()
            if remaining:
                visible_content_emitted = True
                yield {"type": "chunk", "content": remaining}
            if not visible_content_emitted:
                yield {"type": "chunk", "content": REASONING_FILTER_FALLBACK}
            _t3 = _time_module.monotonic()
            logger.info(f"[RAG-Timing] 生成阶段耗时: {_t3 - _t2:.3f}s | 流式总耗时: {_t3 - _rag_start:.3f}s")

            # 4. 来源信息
            sources: List[Dict[str, str]] = []
            seen: set = set()
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


async def langchain_rag_stream_query(question: str) -> AsyncGenerator[Dict[str, Any], None]:
    """流式 RAG 查询（全局便捷函数）"""
    global _rag_service
    if not _rag_service:
        yield {"type": "error", "message": "RAG 服务未初始化，请先调用 initialize_langchain_rag()"}
        return
    async for item in _rag_service.stream_query(question):
        yield item
