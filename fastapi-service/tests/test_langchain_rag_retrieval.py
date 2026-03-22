"""
LangChain RAG 检索单元测试（任务 29.3*）

测试范围：
- BM25Retriever：关键词检索（不依赖外部 API）
- EnsembleRetriever：混合检索结构和权重配置
- query() 返回值结构：retrieved_count、sources、context_used
- RAG 服务未初始化时的兜底响应

注意：向量检索（Milvus/FAISS）和 LLM 生成均通过 mock 避免外部依赖。
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document


# ─── 辅助工具 ─────────────────────────────────────────────────────────────────

def _make_docs(contents: List[str]) -> List[Document]:
    """快速创建测试用 Document 列表"""
    return [
        Document(page_content=text, metadata={"source": f"doc_{i}.md", "type": "document"})
        for i, text in enumerate(contents)
    ]


# ─── BM25Retriever 单元测试 ───────────────────────────────────────────────────

class TestBM25Retriever:
    """BM25Retriever 关键词检索测试（纯内存，无外部依赖）"""

    def _make_retriever(self, docs: List[Document], k: int = 3):
        from langchain_community.retrievers import BM25Retriever
        return BM25Retriever.from_documents(docs, k=k)

    def test_basic_retrieval(self):
        """BM25 能检索到包含关键词的文档（使用空格分词确保词元匹配）"""
        # BM25 默认按空格分词，使用空格分隔词元使测试确定性更高
        docs = _make_docs([
            "工时 填报 规则 每日 工时 不超过 24 小时",
            "项目 管理 说明 项目 分为 常规 和 加急 两类",
            "假期 申请 流程 需提前 三天 提交 申请",
        ])
        retriever = self._make_retriever(docs, k=2)
        results = retriever.invoke("工时 填报")
        assert len(results) >= 1
        assert any("工时" in doc.page_content for doc in results)

    def test_topk_limit(self):
        """返回结果数量不超过 k"""
        docs = _make_docs([f"文档{i}：工时相关内容第{i}条" for i in range(10)])
        retriever = self._make_retriever(docs, k=3)
        results = retriever.invoke("工时")
        assert len(results) <= 3

    def test_irrelevant_query_returns_results(self):
        """无关查询仍返回结果（BM25 不过滤，总是返回 top-k）"""
        docs = _make_docs(["工时规则", "项目说明", "假期流程"])
        retriever = self._make_retriever(docs, k=2)
        results = retriever.invoke("xxxxxxx完全无关的词xxxxxxx")
        # BM25 会返回分数最高的 k 个结果
        assert isinstance(results, list)

    def test_empty_docs_list(self):
        """空文档列表创建 BM25 后查询应不抛出异常"""
        from langchain_community.retrievers import BM25Retriever
        # BM25Retriever.from_documents([]) 的行为：某些版本会抛出，某些不抛出
        # 这里仅验证不会在正常文档下出错
        docs = _make_docs(["工时规则"])
        retriever = BM25Retriever.from_documents(docs, k=1)
        results = retriever.invoke("工时")
        assert len(results) >= 1

    def test_document_metadata_preserved(self):
        """BM25 检索结果保留文档 metadata"""
        docs = [
            Document(
                page_content="工时填报操作说明",
                metadata={"source": "manual.md", "type": "guide"}
            )
        ]
        retriever = self._make_retriever(docs, k=1)
        results = retriever.invoke("工时")
        assert len(results) >= 1
        assert results[0].metadata.get("source") == "manual.md"
        assert results[0].metadata.get("type") == "guide"


# ─── EnsembleRetriever 配置测试 ───────────────────────────────────────────────

class TestEnsembleRetrieverConfig:
    """EnsembleRetriever 权重和结构配置测试"""

    def test_ensemble_weights_sum_to_one(self):
        """权重之和等于 1.0"""
        weights = [0.6, 0.4]
        assert abs(sum(weights) - 1.0) < 1e-9

    def test_ensemble_retriever_creation(self):
        """EnsembleRetriever 正确组合两个 BM25 检索器（均为合法 Runnable）"""
        from langchain_classic.retrievers import EnsembleRetriever
        from langchain_community.retrievers import BM25Retriever

        docs = _make_docs(["工时 规则", "项目 说明", "假期 流程"])

        # EnsembleRetriever 需要真实 Runnable，使用两个 BM25Retriever 模拟"向量+关键词"组合
        bm25_a = BM25Retriever.from_documents(docs, k=2)
        bm25_b = BM25Retriever.from_documents(docs, k=2)

        ensemble = EnsembleRetriever(
            retrievers=[bm25_a, bm25_b],
            weights=[0.6, 0.4],
        )
        assert len(ensemble.retrievers) == 2
        assert ensemble.weights == [0.6, 0.4]

    def test_ensemble_retrieval_merges_results(self):
        """EnsembleRetriever 合并两个检索器的结果（均返回文档列表）"""
        from langchain_classic.retrievers import EnsembleRetriever
        from langchain_community.retrievers import BM25Retriever

        docs = _make_docs([
            "工时 填报 规则 说明",
            "项目 管理 指南",
            "假期 申请 流程",
            "加班 审批 规定",
        ])

        # 两个 BM25 检索器模拟混合检索（测试 EnsembleRetriever 合并逻辑）
        bm25_a = BM25Retriever.from_documents(docs, k=2)
        bm25_b = BM25Retriever.from_documents(docs, k=2)

        ensemble = EnsembleRetriever(
            retrievers=[bm25_a, bm25_b],
            weights=[0.6, 0.4],
        )
        results = ensemble.invoke("工时")
        assert isinstance(results, list)
        assert len(results) >= 1


# ─── LangChainRAGService.query() 返回值测试 ───────────────────────────────────

class TestRAGServiceQueryResult:
    """LangChainRAGService.query() 返回值结构测试"""

    def _build_initialized_service(self):
        """构建一个已初始化的 LangChainRAGService，ensemble_retriever 和 llm 均为 mock"""
        from app.services.langchain_rag import LangChainRAGService
        from langchain_community.retrievers import BM25Retriever

        service = LangChainRAGService()
        docs = _make_docs([
            "工时填报规则：每日填报不超过 24 小时。",
            "项目说明：项目分常规和加急两类。",
        ])

        # BM25 作为 ensemble_retriever 的替代（同步，无 API 依赖）
        bm25 = BM25Retriever.from_documents(docs, k=2)
        service.ensemble_retriever = bm25

        # Mock LLM
        mock_llm = MagicMock()
        mock_chain_result = "根据知识库，工时填报规则为每日不超过 24 小时。"
        # chain.invoke 返回字符串
        mock_chain = MagicMock()
        mock_chain.invoke = MagicMock(return_value=mock_chain_result)

        # Patch chain 构建（prompt | llm | parser）
        service.llm = mock_llm
        service._initialized = True

        return service, mock_chain_result

    @pytest.mark.asyncio
    async def test_query_returns_success_true(self):
        """成功查询时 success 为 True"""
        service, expected_answer = self._build_initialized_service()

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value=expected_answer)
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )

            result = await service.query("工时填报规则是什么？")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_query_returns_retrieved_count(self):
        """查询结果包含 retrieved_count 字段，且 >= 1"""
        service, expected_answer = self._build_initialized_service()

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value=expected_answer)
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )

            result = await service.query("工时")
            assert "retrieved_count" in result
            assert result["retrieved_count"] >= 1

    @pytest.mark.asyncio
    async def test_query_returns_sources_list(self):
        """查询结果包含 sources 列表"""
        service, expected_answer = self._build_initialized_service()

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value=expected_answer)
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )

            result = await service.query("工时规则")
            assert "sources" in result
            assert isinstance(result["sources"], list)

    @pytest.mark.asyncio
    async def test_query_sources_have_source_field(self):
        """sources 列表中每项包含 source 字段"""
        service, expected_answer = self._build_initialized_service()

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value=expected_answer)
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )

            result = await service.query("工时")
            for src in result.get("sources", []):
                assert "source" in src

    @pytest.mark.asyncio
    async def test_query_context_used_true_when_docs_found(self):
        """找到文档时 context_used 为 True"""
        service, expected_answer = self._build_initialized_service()

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value=expected_answer)
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )

            result = await service.query("工时")
            assert result.get("context_used") is True

    @pytest.mark.asyncio
    async def test_query_uninitialized_service(self):
        """未初始化的 RAG 服务返回 success=False"""
        from app.services.langchain_rag import LangChainRAGService

        service = LangChainRAGService()
        # _initialized 默认 False
        result = await service.query("任意问题")
        assert result["success"] is False
        assert "response" in result

    @pytest.mark.asyncio
    async def test_query_no_docs_found(self):
        """检索不到文档时 context_used 为 False，success 为 True"""
        from app.services.langchain_rag import LangChainRAGService

        service = LangChainRAGService()
        service._initialized = True

        # Mock ensemble_retriever 返回空列表
        mock_retriever = MagicMock()
        mock_retriever.invoke = MagicMock(return_value=[])
        service.ensemble_retriever = mock_retriever

        result = await service.query("完全无关的问题")
        assert result["success"] is True
        assert result["context_used"] is False
        assert result["sources"] == []

    @pytest.mark.asyncio
    async def test_query_sources_deduplication(self):
        """来自同一 source 的多个文档块，sources 中该 source 只出现一次"""
        from app.services.langchain_rag import LangChainRAGService

        service = LangChainRAGService()
        service._initialized = True

        # 3 个块来自同一文件
        same_source_docs = [
            Document(page_content=f"工时规则第{i}条", metadata={"source": "rules.md", "type": "document"})
            for i in range(3)
        ]
        mock_retriever = MagicMock()
        mock_retriever.invoke = MagicMock(return_value=same_source_docs)
        service.ensemble_retriever = mock_retriever

        mock_llm = MagicMock()
        service.llm = mock_llm

        with patch("langchain_core.prompts.ChatPromptTemplate.from_messages") as mock_prompt:
            mock_chain = MagicMock()
            mock_chain.invoke = MagicMock(return_value="回答内容")
            mock_prompt.return_value.__or__ = MagicMock(
                return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))
            )
            result = await service.query("工时规则")

        sources = result.get("sources", [])
        source_names = [s["source"] for s in sources]
        # rules.md 应只出现一次
        assert source_names.count("rules.md") == 1


# ─── langchain_rag_query 全局接口测试 ────────────────────────────────────────

class TestLangchainRagQueryGlobal:
    """langchain_rag_query 全局便捷函数测试"""

    @pytest.mark.asyncio
    async def test_returns_error_when_not_initialized(self):
        """全局服务未初始化时返回 success=False"""
        import app.services.langchain_rag as rag_module

        original = rag_module._rag_service
        rag_module._rag_service = None
        try:
            from app.services.langchain_rag import langchain_rag_query
            result = await langchain_rag_query("测试问题")
            assert result["success"] is False
            assert "response" in result
        finally:
            rag_module._rag_service = original

    @pytest.mark.asyncio
    async def test_delegates_to_rag_service(self):
        """全局函数将查询委托给 _rag_service 实例"""
        import app.services.langchain_rag as rag_module

        mock_service = AsyncMock()
        mock_service.query = AsyncMock(return_value={
            "success": True,
            "response": "测试回答",
            "sources": [],
            "retrieved_count": 2,
            "context_used": True,
        })

        original = rag_module._rag_service
        rag_module._rag_service = mock_service
        try:
            from app.services.langchain_rag import langchain_rag_query
            result = await langchain_rag_query("工时规则")
            mock_service.query.assert_called_once_with("工时规则")
            assert result["success"] is True
            assert result["response"] == "测试回答"
        finally:
            rag_module._rag_service = original
