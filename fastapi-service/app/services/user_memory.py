"""
长期用户记忆服务（Redis + BM25 + 时间衰减）

使用 Redis 存储用户的长期记忆，通过 BM25 关键词检索 + 时间衰减算法
计算记忆相关性得分，无需调用外部 embeddings API。

Redis Key 格式：
    memory:{user_id}:{memory_id}   →  Hash，存储单条记忆字段
    memories:{user_id}             →  Sorted Set，score = 综合得分（用于排序/检索）

时间衰减公式：
    score = importance × exp(-decay_rate × elapsed_days) + bm25_bonus
    decay_rate = 0.1  → 约 7 天后衰减至 50%
"""

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 每用户最大记忆条数，超出时删除得分最低的记忆
MAX_MEMORIES_PER_USER = 50
# 时间衰减系数（per day）
DECAY_RATE = 0.1


# ─── 数据模型 ─────────────────────────────────────────────────────────────────

class UserMemory(BaseModel):
    """单条长期记忆"""
    memory_id: str
    user_id: str
    content: str          # 自然语言描述，如 "用户的 user_id 是 emp001"
    importance: float     # 0.0 ~ 1.0
    access_count: int = 0
    created_at: str       # ISO 8601
    last_accessed: str    # ISO 8601


# ─── UserMemoryService ────────────────────────────────────────────────────────

class UserMemoryService:
    """
    基于 Redis 的长期用户记忆服务。

    检索策略：
        1. 从 Redis Sorted Set 按得分取出候选记忆
        2. 用 BM25 计算查询与每条记忆的关键词匹配分
        3. 与时间衰减系数相乘得到最终 score
        4. 返回 top-k 条结果，并更新 last_accessed / access_count
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    # ── Key helpers ────────────────────────────────────────────────────────────

    def _memory_key(self, user_id: str, memory_id: str) -> str:
        return f"memory:{user_id}:{memory_id}"

    def _index_key(self, user_id: str) -> str:
        return f"memories:{user_id}"

    # ── CRUD ────────────────────────────────────────────────────────────────────

    async def store_memory(
        self,
        user_id: str,
        content: str,
        importance: float = 0.5,
    ) -> str:
        """
        存储一条长期记忆。

        如果用户记忆数量超过上限，自动删除得分最低的记忆。

        Returns:
            新记忆的 memory_id
        """
        memory_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        memory = UserMemory(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            importance=max(0.0, min(1.0, importance)),
            access_count=0,
            created_at=now,
            last_accessed=now,
        )

        # 持久化 hash
        await self._redis.hset(
            self._memory_key(user_id, memory_id),
            mapping=memory.model_dump(),
        )

        # 初始得分 = importance（时间为 0，衰减因子 = 1）
        await self._redis.zadd(
            self._index_key(user_id),
            {memory_id: importance},
        )

        # 超上限时裁剪
        count = await self._redis.zcard(self._index_key(user_id))
        if count > MAX_MEMORIES_PER_USER:
            await self._evict_lowest_scored(user_id)

        logger.debug(f"存储长期记忆: user_id={user_id}, memory_id={memory_id}, importance={importance:.2f}")
        return memory_id

    async def retrieve_relevant_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[UserMemory]:
        """
        检索与查询最相关的长期记忆。

        综合评分 = BM25 关键词相关度 × 时间衰减系数
        """
        all_memories = await self.list_memories(user_id)
        if not all_memories:
            return []

        scored = self._score_memories(all_memories, query)
        scored.sort(key=lambda x: x[1], reverse=True)
        top_memories = [m for m, _ in scored[:top_k]]

        # 更新访问记录（fire-and-forget，失败不影响主流程）
        for memory in top_memories:
            try:
                await self._update_access(user_id, memory)
            except Exception as e:
                logger.debug(f"更新记忆访问记录失败: {e}")

        return top_memories

    async def list_memories(self, user_id: str) -> List[UserMemory]:
        """列出用户的全部长期记忆"""
        try:
            memory_ids = await self._redis.zrevrange(self._index_key(user_id), 0, -1)
        except Exception as e:
            logger.warning(f"获取记忆索引失败 user_id={user_id}: {e}")
            return []

        memories: List[UserMemory] = []
        for mid in memory_ids:
            mid_str = mid if isinstance(mid, str) else mid.decode()
            try:
                data = await self._redis.hgetall(self._memory_key(user_id, mid_str))
                if data:
                    # Redis hgetall 返回 bytes，统一转为 str
                    decoded = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in data.items()
                    }
                    # 数值字段转换
                    decoded["importance"] = float(decoded.get("importance", 0.5))
                    decoded["access_count"] = int(decoded.get("access_count", 0))
                    memories.append(UserMemory.model_validate(decoded))
            except Exception as e:
                logger.debug(f"解析记忆失败 memory_id={mid_str}: {e}")
        return memories

    async def clear_memories(self, user_id: str) -> int:
        """
        清除用户的全部长期记忆。

        Returns:
            删除的记忆条数
        """
        memory_ids = await self._redis.zrange(self._index_key(user_id), 0, -1)
        count = len(memory_ids)

        pipe = self._redis.pipeline()
        for mid in memory_ids:
            mid_str = mid if isinstance(mid, str) else mid.decode()
            pipe.delete(self._memory_key(user_id, mid_str))
        pipe.delete(self._index_key(user_id))
        await pipe.execute()

        logger.info(f"清除长期记忆: user_id={user_id}, 共 {count} 条")
        return count

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _time_decay_factor(self, last_accessed_iso: str) -> float:
        """
        计算时间衰减因子。

        factor = exp(-DECAY_RATE × elapsed_days)
        约 7 天后衰减至 50%，30 天后约 5%。
        """
        try:
            last = datetime.fromisoformat(last_accessed_iso)
            # 如果 last 没有时区信息，统一按本地时间处理
            if last.tzinfo is None:
                now = datetime.now()
            else:
                now = datetime.now(timezone.utc)
            elapsed_days = (now - last).total_seconds() / 86400
            return math.exp(-DECAY_RATE * elapsed_days)
        except Exception:
            return 1.0

    def _bm25_score(self, query_tokens: List[str], doc_tokens: List[str]) -> float:
        """
        简化 BM25 评分（用于内存中快速计算）。

        只做 TF × IDF 的近似：统计 query 词在 doc 中出现的频率。
        """
        if not query_tokens or not doc_tokens:
            return 0.0

        doc_set = set(doc_tokens)
        matched = sum(1 for t in query_tokens if t in doc_set)
        return matched / len(query_tokens) if query_tokens else 0.0

    def _tokenize(self, text: str) -> List[str]:
        """
        简单中文分词（按字符/标点分割）。
        生产环境可替换为 jieba 等分词器。
        """
        import re
        # 去除标点，按空格/标点切分，保留中文字符（每个字为一个 token）
        tokens = []
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                tokens.append(char)
            elif char.isalnum():
                tokens.append(char.lower())
        return tokens

    def _score_memories(
        self,
        memories: List[UserMemory],
        query: str,
    ) -> List[tuple]:
        """
        计算每条记忆的综合得分。

        score = importance × time_decay × (0.3 + 0.7 × bm25_match)
        """
        query_tokens = self._tokenize(query)
        result = []
        for memory in memories:
            decay = self._time_decay_factor(memory.last_accessed)
            bm25 = self._bm25_score(query_tokens, self._tokenize(memory.content))
            # 即使 BM25 分数为 0，重要性和时间衰减仍有基础分（避免完全过滤掉高重要性记忆）
            score = memory.importance * decay * (0.3 + 0.7 * bm25)
            result.append((memory, score))
        return result

    async def _update_access(self, user_id: str, memory: UserMemory) -> None:
        """更新记忆的访问时间和访问次数"""
        now = datetime.now().isoformat()
        memory.last_accessed = now
        memory.access_count += 1

        await self._redis.hset(
            self._memory_key(user_id, memory.memory_id),
            mapping={
                "last_accessed": now,
                "access_count": str(memory.access_count),
            },
        )

        # 更新 sorted set score（使用最新时间衰减重算）
        new_score = memory.importance * self._time_decay_factor(now)
        await self._redis.zadd(
            self._index_key(user_id),
            {memory.memory_id: new_score},
        )

    async def _evict_lowest_scored(self, user_id: str) -> None:
        """删除 sorted set 中得分最低的记忆"""
        to_evict = await self._redis.zrange(self._index_key(user_id), 0, 0)
        if to_evict:
            mid = to_evict[0]
            mid_str = mid if isinstance(mid, str) else mid.decode()
            await self._redis.delete(self._memory_key(user_id, mid_str))
            await self._redis.zrem(self._index_key(user_id), mid_str)
            logger.debug(f"淘汰低分记忆: user_id={user_id}, memory_id={mid_str}")


# ─── 全局实例 ─────────────────────────────────────────────────────────────────

_user_memory_service: Optional[UserMemoryService] = None


def initialize_user_memory(redis_client) -> None:
    """初始化全局用户记忆服务（在应用启动时调用）"""
    global _user_memory_service
    _user_memory_service = UserMemoryService(redis_client=redis_client)
    logger.info("✅ UserMemoryService 初始化完成")


def get_user_memory() -> Optional[UserMemoryService]:
    """获取全局用户记忆服务实例"""
    return _user_memory_service
