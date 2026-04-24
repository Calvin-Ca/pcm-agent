#!/usr/bin/env python3
"""
RAG Recall@K 基准测试

4 组对照：
  A: 仅 Milvus 语义
  B: 仅 BM25
  C: Hybrid 60/40
  D: Hybrid 60/40 + CrossEncoder Reranker

指标：Recall@5 / Recall@10 / MRR@10
"""

import asyncio
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fastapi-service"))

from langchain_core.documents import Document

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _load_documents_from_dir(kb_path: str) -> List[Document]:
    from langchain_community.document_loaders import DirectoryLoader, TextLoader

    path = Path(kb_path)
    docs: List[Document] = []
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
    logger.info(f"从 {kb_path} 加载了 {len(docs)} 个文档")
    return docs


def _split_documents(docs: List[Document], chunk_size: int = 512, chunk_overlap: int = 50) -> List[Document]:
    from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

    md_headers_to_split_on = [("#", "h1"), ("##", "h2"), ("###", "h3")]
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=md_headers_to_split_on, strip_headers=False)
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
            try:
                md_chunks = md_splitter.split_text(doc.page_content)
                for md_chunk in md_chunks:
                    md_chunk.metadata = {**doc.metadata, **md_chunk.metadata}
                sub_chunks = recursive_splitter.split_documents(md_chunks)
                chunks.extend(sub_chunks)
            except Exception as e:
                logger.warning(f"Markdown 标题分割失败: {e}")
                chunks.extend(recursive_splitter.split_documents([doc]))
        else:
            chunks.extend(recursive_splitter.split_documents([doc]))
    logger.info(f"文档分割完成：{len(docs)} 篇 -> {len(chunks)} 个块")
    return chunks


def _init_embeddings():
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model="/model",
        openai_api_key="EMPTY",
        openai_api_base="http://172.19.3.136:8097/v1",
        chunk_size=100,
        check_embedding_ctx_length=False,
    )


def _init_vector_store(documents: List[Document], embeddings):
    milvus_host = os.getenv("MILVUS_HOST", "milvus")
    milvus_port = os.getenv("MILVUS_PORT", "19530")
    milvus_uri = f"http://{milvus_host}:{milvus_port}"

    try:
        from langchain_milvus import Milvus
        from pymilvus import MilvusClient as _MilvusClient, connections

        ORM_ALIAS = "lc_default"
        connections.connect(alias=ORM_ALIAS, uri=milvus_uri)
        _orig_init = _MilvusClient.__init__

        def _patched_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            self._using = ORM_ALIAS

        _MilvusClient.__init__ = _patched_init
        try:
            vector_store = Milvus.from_documents(
                documents=documents,
                embedding=embeddings,
                connection_args={"uri": milvus_uri},
                collection_name="knowledge_base_bench",
                drop_old=True,
            )
        finally:
            _MilvusClient.__init__ = _orig_init
        logger.info(f"Milvus 向量存储初始化完成 ({milvus_uri})")
        return vector_store
    except Exception as e:
        logger.warning(f"Milvus 不可用 ({e})，降级为 FAISS")
        from langchain_community.vectorstores import FAISS
        return FAISS.from_documents(documents=documents, embedding=embeddings)


def _init_bm25(documents: List[Document]):
    from langchain_community.retrievers import BM25Retriever
    import jieba

    def _jieba_tokenizer(text: str):
        return list(jieba.cut(text))

    return BM25Retriever.from_documents(documents, k=10, preprocess_func=_jieba_tokenizer)


def _init_reranker():
    from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
    from langchain_community.cross_encoders import HuggingFaceCrossEncoder
    model = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-base")
    return CrossEncoderReranker(model=model, top_n=10)


async def run_benchmark():
    kb_path = os.getenv("KB_PATH", "knowledge-base")
    data_path = Path(__file__).with_suffix("").parent / "data" / "rag_eval_50.jsonl"
    results_dir = Path(__file__).with_suffix("").parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # 1. 加载测试集
    cases = []
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    logger.info(f"加载测试集: {len(cases)} 条")

    # 2. 加载文档并分块
    raw_docs = _load_documents_from_dir(kb_path)
    chunks = _split_documents(raw_docs)

    # 3. 初始化 embedding 和 vector store（只做一次）
    embeddings = _init_embeddings()
    vector_store = _init_vector_store(chunks, embeddings)
    vector_retriever = vector_store.as_retriever(search_kwargs={"k": 10})

    bm25_retriever = _init_bm25(chunks)

    from langchain_classic.retrievers import EnsembleRetriever
    ensemble_retriever = EnsembleRetriever(
        retrievers=[vector_retriever, bm25_retriever],
        weights=[0.6, 0.4],
    )

    try:
        reranker = _init_reranker()
        logger.info("CrossEncoderReranker 初始化完成")
    except Exception as e:
        logger.warning(f"Reranker 初始化失败: {e}")
        reranker = None

    # 4. 定义 4 组策略
    strategies = {
        "A_milvus": vector_retriever,
        "B_bm25": bm25_retriever,
        "C_hybrid": ensemble_retriever,
    }
    if reranker:
        strategies["D_hybrid_rerank"] = "hybrid_rerank"  # 特殊标记，下面单独处理

    # 5. 逐条测试
    all_results = {name: [] for name in strategies}

    for case in cases:
        q = case["question"]
        expected_doc = case["expected_doc"]

        for name, retriever in strategies.items():
            if name == "D_hybrid_rerank":
                docs = await asyncio.to_thread(ensemble_retriever.invoke, q)
                try:
                    docs = await asyncio.to_thread(reranker.compress_documents, docs, q)
                except Exception as e:
                    logger.warning(f"Reranker 失败: {e}")
            else:
                docs = await asyncio.to_thread(retriever.invoke, q)

            doc_names = [Path(d.metadata.get("source", "")).name for d in docs]
            hit5 = expected_doc in doc_names[:5]
            hit10 = expected_doc in doc_names[:10]

            # MRR@10
            mrr = 0.0
            for i, dname in enumerate(doc_names[:10]):
                if dname == expected_doc:
                    mrr = 1.0 / (i + 1)
                    break

            all_results[name].append({
                "id": case["id"],
                "hit5": hit5,
                "hit10": hit10,
                "mrr": mrr,
            })

    # 6. 统计
    def calc(results):
        n = len(results)
        recall5 = sum(1 for r in results if r["hit5"]) / n
        recall10 = sum(1 for r in results if r["hit10"]) / n
        mrr = sum(r["mrr"] for r in results) / n
        return recall5, recall10, mrr

    logger.info("\n=== RAG Recall@K 结果 ===")
    logger.info(f"{'策略':<20} {'Recall@5':>10} {'Recall@10':>10} {'MRR@10':>10}")
    summary = []
    for name, results in all_results.items():
        r5, r10, mrr = calc(results)
        logger.info(f"{name:<20} {r5:>10.2%} {r10:>10.2%} {mrr:>10.4f}")
        summary.append({"strategy": name, "recall@5": r5, "recall@10": r10, "mrr@10": mrr})

    # 7. 写入 CSV
    ts = time.strftime("%Y%m%d_%H%M%S")
    csv_path = results_dir / f"rag_recall_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["strategy", "recall@5", "recall@10", "mrr@10"])
        writer.writeheader()
        writer.writerows(summary)
    logger.info(f"\n结果已保存: {csv_path}")

    # 8. 逐条明细 CSV
    detail_path = results_dir / f"rag_recall_detail_{ts}.csv"
    with open(detail_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "question", "expected_doc", "strategy", "hit@5", "hit@10", "mrr"])
        for name, results in all_results.items():
            for r in results:
                case = cases[r["id"] - 1]
                writer.writerow([
                    r["id"], case["question"], case["expected_doc"],
                    name, int(r["hit5"]), int(r["hit10"]), f"{r['mrr']:.4f}",
                ])
    logger.info(f"明细已保存: {detail_path}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
