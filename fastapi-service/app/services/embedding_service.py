"""
向量化服务

支持多种Embedding模型
"""

import logging
from typing import List, Dict, Any, Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class EmbeddingService(ABC):
    """Embedding服务抽象基类"""
    
    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """单文本向量化"""
        pass
    
    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        pass


class OpenAIEmbeddingService(EmbeddingService):
    """OpenAI Embedding服务"""
    
    def __init__(self, api_key: str, model: str = "text-embedding-ada-002"):
        self.api_key = api_key
        self.model = model
        self._dimension = 1536  # text-embedding-ada-002的维度
        
        try:
            import openai
            self.client = openai.OpenAI(api_key=api_key)
        except ImportError:
            raise ImportError("需要安装openai: pip install openai")
    
    async def embed_text(self, text: str) -> List[float]:
        """单文本向量化"""
        try:
            response = await asyncio.to_thread(
                self.client.embeddings.create,
                input=text,
                model=self.model
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI向量化失败: {e}")
            raise
    
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        try:
            response = await asyncio.to_thread(
                self.client.embeddings.create,
                input=texts,
                model=self.model
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"OpenAI批量向量化失败: {e}")
            raise
    
    @property
    def dimension(self) -> int:
        return self._dimension


class HuggingFaceEmbeddingService(EmbeddingService):
    """HuggingFace Embedding服务"""
    
    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self.model_name = model_name
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._model = None
        self._tokenizer = None
        self._dimension = None
        
    async def _load_model(self):
        """加载模型"""
        if self._model is not None:
            return
        
        try:
            from transformers import AutoTokenizer, AutoModel
            import torch
            
            def _load():
                tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                model = AutoModel.from_pretrained(self.model_name)
                model.eval()
                return tokenizer, model
            
            self._tokenizer, self._model = await asyncio.get_event_loop().run_in_executor(
                self.executor, _load
            )
            
            # 获取模型维度
            with torch.no_grad():
                test_input = self._tokenizer("test", return_tensors="pt", padding=True, truncation=True)
                test_output = self._model(**test_input)
                self._dimension = test_output.last_hidden_state.mean(dim=1).shape[1]
            
            logger.info(f"加载HuggingFace模型: {self.model_name}, 维度: {self._dimension}")
            
        except ImportError:
            raise ImportError("需要安装transformers和torch: pip install transformers torch")
        except Exception as e:
            logger.error(f"加载HuggingFace模型失败: {e}")
            raise
    
    async def embed_text(self, text: str) -> List[float]:
        """单文本向量化"""
        await self._load_model()
        
        def _embed():
            import torch
            
            with torch.no_grad():
                inputs = self._tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
                outputs = self._model(**inputs)
                # 使用mean pooling
                embeddings = outputs.last_hidden_state.mean(dim=1)
                # 归一化
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
                return embeddings.squeeze().numpy().tolist()
        
        return await asyncio.get_event_loop().run_in_executor(self.executor, _embed)
    
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        await self._load_model()
        
        def _embed_batch():
            import torch
            
            with torch.no_grad():
                inputs = self._tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=512)
                outputs = self._model(**inputs)
                # 使用mean pooling
                embeddings = outputs.last_hidden_state.mean(dim=1)
                # 归一化
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
                return embeddings.numpy().tolist()
        
        return await asyncio.get_event_loop().run_in_executor(self.executor, _embed_batch)
    
    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("模型未加载，无法获取维度")
        return self._dimension


class SimpleEmbeddingService(EmbeddingService):
    """简单的Embedding服务（用于测试）"""
    
    def __init__(self, dimension: int = 768):
        self._dimension = dimension
    
    async def embed_text(self, text: str) -> List[float]:
        """简单的文本向量化（基于hash）"""
        # 这是一个简单的实现，仅用于测试
        import hashlib
        
        # 使用文本的hash值生成向量
        hash_obj = hashlib.md5(text.encode())
        hash_bytes = hash_obj.digest()
        
        # 将hash转换为向量
        vector = []
        for i in range(self._dimension):
            byte_idx = i % len(hash_bytes)
            vector.append(float(hash_bytes[byte_idx]) / 255.0 - 0.5)
        
        # 归一化
        norm = sum(x * x for x in vector) ** 0.5
        if norm > 0:
            vector = [x / norm for x in vector]
        
        return vector
    
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        return [await self.embed_text(text) for text in texts]
    
    @property
    def dimension(self) -> int:
        return self._dimension


class EmbeddingManager:
    """Embedding管理器"""
    
    def __init__(self, service_type: str = "simple", **kwargs):
        self.service_type = service_type
        
        if service_type == "openai":
            self.service = OpenAIEmbeddingService(**kwargs)
        elif service_type == "huggingface":
            self.service = HuggingFaceEmbeddingService(**kwargs)
        elif service_type == "simple":
            self.service = SimpleEmbeddingService(**kwargs)
        else:
            raise ValueError(f"不支持的服务类型: {service_type}")
    
    async def embed_document_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """为文档块生成向量"""
        texts = [chunk["content"] for chunk in chunks]
        
        try:
            vectors = await self.service.embed_texts(texts)
            
            # 添加向量到块数据
            for chunk, vector in zip(chunks, vectors):
                chunk["vector"] = vector
            
            logger.info(f"为 {len(chunks)} 个文档块生成向量")
            return chunks
            
        except Exception as e:
            logger.error(f"生成文档向量失败: {e}")
            raise
    
    async def embed_query(self, query: str) -> List[float]:
        """为查询生成向量"""
        return await self.service.embed_text(query)
    
    @property
    def dimension(self) -> int:
        """获取向量维度"""
        return self.service.dimension