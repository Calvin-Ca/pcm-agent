"""
向量数据库服务

支持Milvus和简单的内存向量存储
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from abc import ABC, abstractmethod
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    """向量存储抽象基类"""
    
    @abstractmethod
    async def create_collection(self, collection_name: str, dimension: int) -> bool:
        """创建集合"""
        pass
    
    @abstractmethod
    async def insert_vectors(
        self, 
        collection_name: str, 
        vectors: List[List[float]], 
        metadata: List[Dict[str, Any]]
    ) -> List[str]:
        """插入向量"""
        pass
    
    @abstractmethod
    async def search_vectors(
        self, 
        collection_name: str, 
        query_vector: List[float], 
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        pass
    
    @abstractmethod
    async def delete_collection(self, collection_name: str) -> bool:
        """删除集合"""
        pass


class MemoryVectorStore(VectorStore):
    """内存向量存储（用于开发和测试）"""
    
    def __init__(self):
        self.collections: Dict[str, Dict[str, Any]] = {}
        self.executor = ThreadPoolExecutor(max_workers=4)
    
    async def create_collection(self, collection_name: str, dimension: int) -> bool:
        """创建集合"""
        try:
            self.collections[collection_name] = {
                "dimension": dimension,
                "vectors": [],
                "metadata": [],
                "ids": []
            }
            logger.info(f"创建内存向量集合: {collection_name}, 维度: {dimension}")
            return True
        except Exception as e:
            logger.error(f"创建集合失败: {e}")
            return False
    
    async def insert_vectors(
        self, 
        collection_name: str, 
        vectors: List[List[float]], 
        metadata: List[Dict[str, Any]]
    ) -> List[str]:
        """插入向量"""
        if collection_name not in self.collections:
            raise ValueError(f"集合不存在: {collection_name}")
        
        if len(vectors) != len(metadata):
            raise ValueError("向量数量与元数据数量不匹配")
        
        collection = self.collections[collection_name]
        ids = []
        
        for i, (vector, meta) in enumerate(zip(vectors, metadata)):
            if len(vector) != collection["dimension"]:
                raise ValueError(f"向量维度不匹配: 期望 {collection['dimension']}, 实际 {len(vector)}")
            
            vector_id = f"{collection_name}_{len(collection['vectors'])}"
            collection["vectors"].append(vector)
            collection["metadata"].append(meta)
            collection["ids"].append(vector_id)
            ids.append(vector_id)
        
        logger.info(f"插入 {len(vectors)} 个向量到集合 {collection_name}")
        return ids
    
    async def search_vectors(
        self, 
        collection_name: str, 
        query_vector: List[float], 
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        if collection_name not in self.collections:
            raise ValueError(f"集合不存在: {collection_name}")
        
        collection = self.collections[collection_name]
        
        if not collection["vectors"]:
            return []
        
        def _compute_similarities():
            query_np = np.array(query_vector)
            vectors_np = np.array(collection["vectors"])
            
            # 计算余弦相似度
            similarities = np.dot(vectors_np, query_np) / (
                np.linalg.norm(vectors_np, axis=1) * np.linalg.norm(query_np)
            )
            
            # 获取top_k结果
            top_indices = np.argsort(similarities)[::-1][:top_k]
            
            results = []
            for idx in top_indices:
                results.append({
                    "id": collection["ids"][idx],
                    "score": float(similarities[idx]),
                    "metadata": collection["metadata"][idx]
                })
            
            return results
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, _compute_similarities)
    
    async def delete_collection(self, collection_name: str) -> bool:
        """删除集合"""
        try:
            if collection_name in self.collections:
                del self.collections[collection_name]
                logger.info(f"删除集合: {collection_name}")
            return True
        except Exception as e:
            logger.error(f"删除集合失败: {e}")
            return False
    
    def get_collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """获取集合信息"""
        if collection_name not in self.collections:
            return None
        
        collection = self.collections[collection_name]
        return {
            "name": collection_name,
            "dimension": collection["dimension"],
            "count": len(collection["vectors"])
        }


try:
    from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False


class MilvusVectorStore(VectorStore):
    """Milvus向量存储"""
    
    def __init__(self, host: str = "localhost", port: int = 19530):
        if not MILVUS_AVAILABLE:
            raise ImportError("需要安装pymilvus: pip install pymilvus")
        
        self.host = host
        self.port = port
        self.connected = False
    
    async def connect(self):
        """连接到Milvus"""
        try:
            connections.connect("default", host=self.host, port=self.port)
            self.connected = True
            logger.info(f"连接到Milvus: {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"连接Milvus失败: {e}")
            raise
    
    async def create_collection(self, collection_name: str, dimension: int) -> bool:
        """创建集合"""
        if not self.connected:
            await self.connect()
        
        try:
            # 检查集合是否已存在
            if utility.has_collection(collection_name):
                logger.info(f"集合已存在: {collection_name}")
                return True
            
            # 定义字段
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
                FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=65535)
            ]
            
            # 创建集合
            schema = CollectionSchema(fields, f"Knowledge base collection: {collection_name}")
            collection = Collection(collection_name, schema)
            
            # 创建索引
            index_params = {
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            collection.create_index("vector", index_params)
            
            logger.info(f"创建Milvus集合: {collection_name}, 维度: {dimension}")
            return True
            
        except Exception as e:
            logger.error(f"创建Milvus集合失败: {e}")
            return False
    
    async def insert_vectors(
        self, 
        collection_name: str, 
        vectors: List[List[float]], 
        metadata: List[Dict[str, Any]]
    ) -> List[str]:
        """插入向量"""
        if not self.connected:
            await self.connect()
        
        try:
            collection = Collection(collection_name)
            
            # 生成ID
            ids = [f"{collection_name}_{i}_{hash(str(meta))}" for i, meta in enumerate(metadata)]
            
            # 序列化元数据
            metadata_json = [json.dumps(meta, ensure_ascii=False) for meta in metadata]
            
            # 插入数据
            entities = [ids, vectors, metadata_json]
            collection.insert(entities)
            collection.flush()
            
            logger.info(f"插入 {len(vectors)} 个向量到Milvus集合 {collection_name}")
            return ids
            
        except Exception as e:
            logger.error(f"插入向量到Milvus失败: {e}")
            raise
    
    async def search_vectors(
        self, 
        collection_name: str, 
        query_vector: List[float], 
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """搜索向量"""
        if not self.connected:
            await self.connect()
        
        try:
            collection = Collection(collection_name)
            collection.load()
            
            # 搜索参数
            search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}
            
            # 执行搜索
            results = collection.search(
                data=[query_vector],
                anns_field="vector",
                param=search_params,
                limit=top_k,
                output_fields=["metadata"]
            )
            
            # 处理结果
            search_results = []
            for hits in results:
                for hit in hits:
                    metadata = json.loads(hit.entity.get("metadata"))
                    search_results.append({
                        "id": hit.id,
                        "score": float(hit.score),
                        "metadata": metadata
                    })
            
            return search_results
            
        except Exception as e:
            logger.error(f"Milvus搜索失败: {e}")
            raise
    
    async def delete_collection(self, collection_name: str) -> bool:
        """删除集合"""
        if not self.connected:
            await self.connect()
        
        try:
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)
                logger.info(f"删除Milvus集合: {collection_name}")
            return True
        except Exception as e:
            logger.error(f"删除Milvus集合失败: {e}")
            return False


class VectorStoreManager:
    """向量存储管理器"""
    
    def __init__(self, store_type: str = "memory", **kwargs):
        self.store_type = store_type
        
        if store_type == "memory":
            self.store = MemoryVectorStore()
        elif store_type == "milvus":
            self.store = MilvusVectorStore(**kwargs)
        else:
            raise ValueError(f"不支持的存储类型: {store_type}")
    
    async def initialize(self):
        """初始化存储"""
        if hasattr(self.store, 'connect'):
            await self.store.connect()
    
    async def create_knowledge_collection(self, dimension: int = 768) -> bool:
        """创建知识库集合"""
        return await self.store.create_collection("knowledge_base", dimension)
    
    async def add_documents(
        self, 
        vectors: List[List[float]], 
        metadata: List[Dict[str, Any]]
    ) -> List[str]:
        """添加文档向量"""
        return await self.store.insert_vectors("knowledge_base", vectors, metadata)
    
    async def search_knowledge(
        self, 
        query_vector: List[float], 
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """搜索知识库"""
        return await self.store.search_vectors("knowledge_base", query_vector, top_k)
    
    async def clear_knowledge_base(self) -> bool:
        """清空知识库"""
        return await self.store.delete_collection("knowledge_base")