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
            response = self._generate_contextual_response(query, context, retrieved_docs)
            
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
    
    def _generate_contextual_response(
        self, 
        query: str, 
        context: str, 
        sources: List[Dict[str, Any]]
    ) -> str:
        """生成基于上下文的回答"""
        # 这里是一个简化的实现，实际应该调用LLM
        # 在完整实现中，这里会构建prompt并调用LLM服务
        
        # 简单的模板响应
        response_parts = []
        
        # 添加基于上下文的回答
        response_parts.append("根据企业知识库的信息：")
        response_parts.append("")
        
        # 提取关键信息
        if "工时" in query:
            if "填报" in context or "提交" in context:
                response_parts.append("关于工时填报，主要要求如下：")
            elif "查询" in context or "统计" in context:
                response_parts.append("关于工时查询，相关规定如下：")
        elif "项目" in query:
            response_parts.append("关于项目管理，相关信息如下：")
        else:
            response_parts.append("相关信息如下：")
        
        response_parts.append("")
        
        # 添加上下文内容（简化处理）
        context_lines = context.split('\n')
        for line in context_lines[:10]:  # 最多显示10行
            if line.strip():
                response_parts.append(f"• {line.strip()}")
        
        # 添加来源信息
        if sources:
            response_parts.append("")
            response_parts.append("**参考来源：**")
            for i, source in enumerate(sources[:3], 1):  # 最多显示3个来源
                source_name = source.get("source", "未知来源")
                if source.get("page"):
                    source_name += f" (第{source['page']}页)"
                response_parts.append(f"{i}. {source_name}")
        
        return "\n".join(response_parts)
    
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