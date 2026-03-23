"""
RAG响应生成服务

基于检索到的文档生成回答
"""

import logging
from typing import Dict, Any, List, Optional
from app.tools.search_knowledge import search_knowledge_tool

logger = logging.getLogger(__name__)


class RAGService:
    """RAG响应生成服务"""
    
    def __init__(self):
        self.max_context_length = 2000  # 最大上下文长度
        self.min_relevance_score = 0.3  # 最小相关性分数
    
    async def generate_rag_response(
        self, 
        query: str, 
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        生成基于RAG的响应
        
        Args:
            query: 用户查询
            top_k: 检索文档数量
            
        Returns:
            RAG响应结果
        """
        try:
            # 1. 检索相关文档
            search_result = await search_knowledge_tool(
                query=query,
                top_k=top_k,
                min_score=self.min_relevance_score
            )
            
            if not search_result.get("success", False):
                return {
                    "success": False,
                    "error": "知识库检索失败",
                    "response": "抱歉，无法搜索相关信息，请稍后重试。"
                }
            
            retrieved_docs = search_result.get("results", [])
            
            # 2. 构建上下文
            context = self._build_context(retrieved_docs)
            
            # 3. 生成响应
            if not context:
                return {
                    "success": True,
                    "response": "抱歉，我在知识库中没有找到相关信息。您可以尝试换个问法或联系管理员。",
                    "sources": [],
                    "context_used": False
                }
            
            # 4. 构建带上下文的回答
            response = await self._generate_contextual_response(query, context, retrieved_docs)
            
            return {
                "success": True,
                "response": response,
                "sources": self._format_sources(retrieved_docs),
                "context_used": True,
                "retrieved_count": len(retrieved_docs)
            }
            
        except Exception as e:
            logger.error(f"RAG响应生成失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": "抱歉，生成回答时出现错误，请稍后重试。"
            }
    
    def _build_context(self, retrieved_docs: List[Dict[str, Any]]) -> str:
        """构建上下文"""
        if not retrieved_docs:
            return ""
        
        context_parts = []
        current_length = 0
        
        for doc in retrieved_docs:
            content = doc.get("content", "").strip()
            if not content:
                continue
            
            # 检查是否超过最大长度
            if current_length + len(content) > self.max_context_length:
                # 截断内容
                remaining_length = self.max_context_length - current_length
                if remaining_length > 100:  # 至少保留100字符
                    content = content[:remaining_length] + "..."
                    context_parts.append(content)
                break
            
            context_parts.append(content)
            current_length += len(content)
        
        return "\n\n".join(context_parts)
    
    async def _generate_contextual_response(
        self,
        query: str,
        context: str,
        sources: List[Dict[str, Any]]
    ) -> str:
        """调用 LLM 生成基于上下文的回答"""
        from app.services.llm_client import LLMClient

        system_prompt = (
            "你是工时管理系统的AI助手。请根据以下知识库内容回答用户的问题，"
            "回答要简洁、准确。如果知识库内容不足以完整回答，请如实说明。"
        )
        user_prompt = f"知识库内容：\n{context}\n\n用户问题：{query}"

        try:
            llm = LLMClient(env_prefix="CHAT_LLM")
            answer = await llm.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=1000
            )
            return answer
        except Exception as e:
            logger.error(f"LLM 生成失败，降级为模板回答: {e}")
            # 降级：直接返回检索到的上下文内容
            lines = [line.strip() for line in context.split("\n") if line.strip()]
            return "根据知识库信息：\n\n" + "\n".join(f"• {l}" for l in lines[:10])
    
    def _format_sources(self, retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """格式化来源信息"""
        sources = []
        seen_sources = set()
        
        for doc in retrieved_docs:
            source_info = {
                "source": doc.get("source", "未知来源"),
                "type": doc.get("type", "unknown"),
                "score": doc.get("score", 0),
                "page": doc.get("page"),
                "chunk_index": doc.get("chunk_index")
            }
            
            # 去重
            source_key = f"{source_info['source']}_{source_info.get('page', '')}"
            if source_key not in seen_sources:
                sources.append(source_info)
                seen_sources.add(source_key)
        
        return sources
    
    async def check_knowledge_availability(self, query: str) -> Dict[str, Any]:
        """检查知识库中是否有相关信息"""
        try:
            search_result = await search_knowledge_tool(
                query=query,
                top_k=1,
                min_score=self.min_relevance_score
            )
            
            has_knowledge = (
                search_result.get("success", False) and 
                len(search_result.get("results", [])) > 0
            )
            
            return {
                "has_knowledge": has_knowledge,
                "confidence": search_result.get("results", [{}])[0].get("score", 0) if has_knowledge else 0
            }
            
        except Exception as e:
            logger.error(f"检查知识库可用性失败: {e}")
            return {
                "has_knowledge": False,
                "confidence": 0,
                "error": str(e)
            }


# 全局RAG服务实例
rag_service = RAGService()


async def generate_rag_answer(query: str) -> Dict[str, Any]:
    """生成RAG回答的便捷函数"""
    return await rag_service.generate_rag_response(query)


async def check_knowledge_base(query: str) -> bool:
    """检查知识库是否包含相关信息"""
    result = await rag_service.check_knowledge_availability(query)
    return result.get("has_knowledge", False)