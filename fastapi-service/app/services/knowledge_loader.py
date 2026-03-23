"""
知识库加载服务（已废弃，仅供学习参考）

负责加载初始知识库文档

⚠️  DEPRECATED: 此文件是早期知识库加载流程，依赖已废弃的 document_loader.py。
    当前生产代码入口：app/services/langchain_rag.py -> initialize_langchain_rag()
    保留此文件仅供学习参考，不再被 main.py 调用。
"""

import logging
import os
from typing import List, Dict, Any
from pathlib import Path
from app.services.document_loader import DocumentLoader, ChunkSplitter
from app.tools.search_knowledge import add_knowledge_documents

logger = logging.getLogger(__name__)


class KnowledgeLoader:
    """知识库加载器"""
    
    def __init__(self, knowledge_base_path: str = "knowledge-base"):
        self.knowledge_base_path = knowledge_base_path
        self.document_loader = DocumentLoader()
        self.chunk_splitter = ChunkSplitter(chunk_size=512, chunk_overlap=50)
        
    async def load_initial_knowledge_base(self) -> Dict[str, Any]:
        """加载初始知识库"""
        try:
            # 获取知识库文件路径
            kb_path = Path(self.knowledge_base_path)
            if not kb_path.exists():
                logger.warning(f"知识库目录不存在: {kb_path}")
                return {
                    "success": False,
                    "error": f"知识库目录不存在: {kb_path}",
                    "loaded_files": 0
                }
            
            # 查找所有支持的文档文件
            supported_extensions = ['.md', '.txt', '.pdf', '.docx', '.doc']
            document_files = []
            
            for ext in supported_extensions:
                document_files.extend(kb_path.glob(f"*{ext}"))
                document_files.extend(kb_path.glob(f"**/*{ext}"))  # 递归查找
            
            if not document_files:
                logger.warning("未找到任何文档文件")
                return {
                    "success": True,
                    "message": "未找到任何文档文件",
                    "loaded_files": 0
                }
            
            logger.info(f"找到 {len(document_files)} 个文档文件")
            
            # 加载所有文档
            all_chunks = []
            loaded_files = 0
            failed_files = []
            
            for file_path in document_files:
                try:
                    logger.info(f"加载文档: {file_path}")
                    
                    # 加载文档
                    chunks = await self.document_loader.load_document(str(file_path))
                    
                    if chunks:
                        # 分割文档块
                        split_chunks = self.chunk_splitter.split_chunks(chunks)
                        
                        # 转换为字典格式
                        for chunk in split_chunks:
                            chunk_dict = {
                                "content": chunk.content,
                                "metadata": chunk.metadata
                            }
                            all_chunks.append(chunk_dict)
                        
                        loaded_files += 1
                        logger.info(f"成功加载文档 {file_path}，生成 {len(split_chunks)} 个块")
                    
                except Exception as e:
                    logger.error(f"加载文档失败 {file_path}: {e}")
                    failed_files.append(str(file_path))
                    continue
            
            # 添加到向量数据库
            if all_chunks:
                result = await add_knowledge_documents(all_chunks)
                
                if result.get("success", False):
                    logger.info(f"成功加载知识库: {loaded_files} 个文件, {len(all_chunks)} 个文档块")
                    return {
                        "success": True,
                        "loaded_files": loaded_files,
                        "total_chunks": len(all_chunks),
                        "failed_files": failed_files,
                        "document_ids": result.get("document_ids", [])
                    }
                else:
                    return {
                        "success": False,
                        "error": f"向量化失败: {result.get('error', '未知错误')}",
                        "loaded_files": loaded_files,
                        "failed_files": failed_files
                    }
            else:
                return {
                    "success": False,
                    "error": "没有成功加载任何文档内容",
                    "loaded_files": 0,
                    "failed_files": failed_files
                }
                
        except Exception as e:
            logger.error(f"加载知识库失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "loaded_files": 0
            }
    
    async def load_single_document(self, file_path: str) -> Dict[str, Any]:
        """加载单个文档"""
        try:
            logger.info(f"加载单个文档: {file_path}")
            
            # 加载文档
            chunks = await self.document_loader.load_document(file_path)
            
            if not chunks:
                return {
                    "success": False,
                    "error": "文档内容为空",
                    "chunks": 0
                }
            
            # 分割文档块
            split_chunks = self.chunk_splitter.split_chunks(chunks)
            
            # 转换为字典格式
            chunk_dicts = []
            for chunk in split_chunks:
                chunk_dict = {
                    "content": chunk.content,
                    "metadata": chunk.metadata
                }
                chunk_dicts.append(chunk_dict)
            
            # 添加到向量数据库
            result = await add_knowledge_documents(chunk_dicts)
            
            if result.get("success", False):
                logger.info(f"成功加载文档 {file_path}，生成 {len(chunk_dicts)} 个块")
                return {
                    "success": True,
                    "file_path": file_path,
                    "chunks": len(chunk_dicts),
                    "document_ids": result.get("document_ids", [])
                }
            else:
                return {
                    "success": False,
                    "error": f"向量化失败: {result.get('error', '未知错误')}",
                    "file_path": file_path
                }
                
        except Exception as e:
            logger.error(f"加载单个文档失败 {file_path}: {e}")
            return {
                "success": False,
                "error": str(e),
                "file_path": file_path
            }
    
    def close(self):
        """关闭资源"""
        self.document_loader.close()


# 全局知识库加载器实例
knowledge_loader = KnowledgeLoader()


async def initialize_knowledge_base() -> Dict[str, Any]:
    """初始化知识库的便捷函数"""
    return await knowledge_loader.load_initial_knowledge_base()


async def add_document_to_knowledge_base(file_path: str) -> Dict[str, Any]:
    """添加文档到知识库的便捷函数"""
    return await knowledge_loader.load_single_document(file_path)