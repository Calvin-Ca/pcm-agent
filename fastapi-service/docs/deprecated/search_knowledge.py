"""
知识库搜索工具

提供基于向量相似度的知识库检索功能
"""

import logging
from typing import Dict, Any, List, Optional
from app.services.vector_store import VectorStoreManager
from app.services.embedding_service import EmbeddingManager

logger = logging.getLogger(__name__)

# 全局实例
vector_store_manager: Optional[VectorStoreManager] = None
embedding_manager: Optional[EmbeddingManager] = None


async def initialize_knowledge_search():
    """初始化知识库搜索服务"""
    global vector_store_manager, embedding_manager

    try:
        import os

        # 初始化向量存储（使用内存存储）
        vector_store_manager = VectorStoreManager(store_type="memory")
        await vector_store_manager.initialize()

        # 优先使用 DashScope 语义 embedding，降级为简单 embedding
        api_key = os.getenv("CHAT_LLM_API_KEY", "")
        api_base = os.getenv(
            "CHAT_LLM_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        if api_key:
            embedding_manager = EmbeddingManager(
                service_type="openai",
                api_key=api_key,
                model="text-embedding-v2",
                api_base=api_base
            )
            logger.info("使用 DashScope text-embedding-v2 语义向量化")
        else:
            embedding_manager = EmbeddingManager(service_type="simple", dimension=1536)
            logger.warning("未配置 CHAT_LLM_API_KEY，使用 SimpleEmbeddingService（仅供测试）")

        # 创建知识库集合
        await vector_store_manager.create_knowledge_collection(dimension=embedding_manager.dimension)

        logger.info("知识库搜索服务初始化完成")

    except Exception as e:
        logger.error(f"初始化知识库搜索服务失败: {e}")
        raise


async def search_knowledge_tool(
    query: str,
    top_k: int = 5,
    min_score: float = 0.1
) -> Dict[str, Any]:
    """
    搜索知识库工具
    
    Args:
        query: 搜索查询
        top_k: 返回结果数量
        min_score: 最小相似度分数
        
    Returns:
        搜索结果
    """
    global vector_store_manager, embedding_manager
    
    if not vector_store_manager or not embedding_manager:
        await initialize_knowledge_search()
    
    try:
        # 生成查询向量
        query_vector = await embedding_manager.embed_query(query)
        
        # 搜索向量数据库
        search_results = await vector_store_manager.search_knowledge(
            query_vector=query_vector,
            top_k=top_k
        )
        
        # 过滤低分结果
        filtered_results = [
            result for result in search_results 
            if result.get("score", 0) >= min_score
        ]
        
        # 格式化结果
        formatted_results = []
        for result in filtered_results:
            metadata = result.get("metadata", {})
            formatted_results.append({
                "content": metadata.get("content", ""),
                "source": metadata.get("source", "未知来源"),
                "score": result.get("score", 0),
                "type": metadata.get("type", "unknown"),
                "page": metadata.get("page"),
                "chunk_index": metadata.get("chunk_index")
            })
        
        return {
            "success": True,
            "query": query,
            "results": formatted_results,
            "total_found": len(search_results),
            "filtered_count": len(formatted_results)
        }
        
    except Exception as e:
        logger.error(f"知识库搜索失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "query": query,
            "results": []
        }


async def add_knowledge_documents(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    添加文档到知识库
    
    Args:
        documents: 文档列表，每个文档包含content和metadata
        
    Returns:
        添加结果
    """
    global vector_store_manager, embedding_manager
    
    if not vector_store_manager or not embedding_manager:
        await initialize_knowledge_search()
    
    try:
        # 生成文档向量
        texts = [doc["content"] for doc in documents]
        vectors = await embedding_manager.service.embed_texts(texts)
        
        # 准备元数据
        metadata_list = []
        for doc in documents:
            metadata = doc.get("metadata", {})
            metadata["content"] = doc["content"]  # 保存原始内容
            metadata_list.append(metadata)
        
        # 插入向量数据库
        ids = await vector_store_manager.add_documents(vectors, metadata_list)
        
        logger.info(f"成功添加 {len(documents)} 个文档到知识库")
        
        return {
            "success": True,
            "added_count": len(documents),
            "document_ids": ids
        }
        
    except Exception as e:
        logger.error(f"添加文档到知识库失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "added_count": 0
        }


# 工具定义
SEARCH_KNOWLEDGE_TOOL = {
    "name": "search_knowledge",
    "description": "搜索企业知识库，包括制度文档、FAQ等",
    "category": "knowledge",
    "json_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询，支持自然语言"
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量，默认5",
                "default": 5,
                "minimum": 1,
                "maximum": 20
            },
            "min_score": {
                "type": "number",
                "description": "最小相似度分数，默认0.1",
                "default": 0.1,
                "minimum": 0.0,
                "maximum": 1.0
            }
        },
        "required": ["query"]
    },
    "requires_permission": False,
    "timeout": 30
}


async def handle_search_knowledge(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """处理知识库搜索请求"""
    query = parameters.get("query", "")
    top_k = parameters.get("top_k", 5)
    min_score = parameters.get("min_score", 0.1)
    
    if not query.strip():
        return {
            "success": False,
            "error": "查询不能为空",
            "results": []
        }
    
    return await search_knowledge_tool(
        query=query,
        top_k=top_k,
        min_score=min_score
    )