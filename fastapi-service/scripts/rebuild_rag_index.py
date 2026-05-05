"""重建 RAG 索引脚本：加载知识库文档并重新初始化向量存储和 BM25"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.services.langchain_rag import _load_documents_from_dir, LangChainRAGService

KB_PATH = Path(__file__).parent.parent.parent / "knowledge-base"


async def main():
    print(f"[INFO] 知识库路径: {KB_PATH}")

    # 1. 加载文档
    print("[INFO] 加载知识库文档...")
    docs = _load_documents_from_dir(str(KB_PATH))
    print(f"[INFO] 加载完成: {len(docs)} chunks")

    # 2. 初始化 RAG 服务（会重建向量索引和 BM25）
    print("[INFO] 初始化 RAG 服务（重建索引）...")
    rag = LangChainRAGService()
    await rag.initialize(docs)

    print("[INFO] RAG 索引重建完成!")
    return rag


if __name__ == "__main__":
    asyncio.run(main())
