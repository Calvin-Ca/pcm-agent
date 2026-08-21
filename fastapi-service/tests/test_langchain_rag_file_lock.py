from unittest.mock import MagicMock

import langchain_milvus
import pymilvus
import pytest
from langchain_core.documents import Document

from app.services.langchain_rag import LangChainRAGService, _exclusive_file_lock


def test_exclusive_file_lock_supports_current_platform(tmp_path):
    lock_path = tmp_path / "rag-init.lock"

    with lock_path.open("a+") as lock_file:
        with _exclusive_file_lock(lock_file):
            assert lock_path.exists()

        # 锁释放后应能在同一进程中再次获取，覆盖 Windows 的 unlock 路径。
        with _exclusive_file_lock(lock_file):
            assert lock_file.fileno() >= 0


@pytest.mark.asyncio
async def test_init_vector_store_uses_milvus_with_platform_lock(monkeypatch, tmp_path):
    vector_store = MagicMock()
    vector_store.as_retriever.return_value = "retriever"
    milvus = MagicMock()
    milvus.from_documents.return_value = vector_store
    connect = MagicMock()

    monkeypatch.setattr(langchain_milvus, "Milvus", milvus)
    monkeypatch.setattr(pymilvus.connections, "connect", connect)
    monkeypatch.setenv("MILVUS_HOST", "milvus.test")
    monkeypatch.setenv("MILVUS_PORT", "19530")
    monkeypatch.setenv("RAG_MILVUS_INIT_LOCK", str(tmp_path / "rag-init.lock"))

    service = LangChainRAGService()
    service.embeddings = MagicMock()
    retriever = await service._init_vector_store([Document(page_content="test")])

    connect.assert_called_once_with(alias="lc_default", uri="http://milvus.test:19530")
    milvus.from_documents.assert_called_once()
    assert service._use_milvus is True
    assert retriever == "retriever"
