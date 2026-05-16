"""
短期会话记忆服务（Redis）

使用 Redis 存储用户会话历史，支持：
- 会话创建与过期管理（30分钟 TTL）
- 对话历史保存（最近 MAX_CONVERSATION_HISTORY 条消息）
- 会话清除

Redis Key 格式:
    session:{session_id}  →  JSON 序列化的 Session 对象
"""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ─── 数据模型 ─────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """单条对话消息"""
    role: str                    # "user" | "assistant"
    content: str
    timestamp: str               # ISO 8601
    intent: Optional[str] = None # 识别出的意图类型


class Session(BaseModel):
    """会话对象"""
    session_id: str
    user_id: str
    messages: List[Message] = []
    created_at: str
    last_active: str


# ─── SessionMemoryService ─────────────────────────────────────────────────────

class SessionMemoryService:
    """
    基于 Redis 的短期会话记忆服务。

    每个会话以 JSON 格式存储在 Redis 中，每次访问自动续期 TTL。
    消息列表保留最近 max_history 条（超出时从头部裁剪）。
    """

    def __init__(self, redis_client, ttl: int = 1800, max_history: int = 10):
        """
        Args:
            redis_client: redis.asyncio.Redis 实例
            ttl: 会话过期时间（秒），默认 30 分钟
            max_history: 保留的最大消息条数（1 轮 = user + assistant 共 2 条）
        """
        self._redis = redis_client
        self._ttl = ttl
        self._max_history = max_history

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def _load_session(self, session_id: str) -> Optional[Session]:
        """从 Redis 加载会话，不存在则返回 None"""
        try:
            data = await self._redis.get(self._key(session_id))
            if data is None:
                return None
            return Session.model_validate_json(data)
        except Exception as e:
            logger.warning(f"加载会话失败 session_id={session_id}: {e}")
            return None

    async def _save_session(self, session: Session) -> None:
        """将会话持久化到 Redis 并续期 TTL"""
        try:
            session.last_active = datetime.now().isoformat()
            await self._redis.setex(
                self._key(session.session_id),
                self._ttl,
                session.model_dump_json(),
            )
        except Exception as e:
            logger.error(f"保存会话失败 session_id={session.session_id}: {e}")

    async def get_or_create_session(self, session_id: str, user_id: str) -> Session:
        """
        获取已有会话，如不存在则创建新会话。

        每次调用都会续期 TTL。
        """
        session = await self._load_session(session_id)
        if session is None:
            now = datetime.now().isoformat()
            session = Session(
                session_id=session_id,
                user_id=user_id,
                messages=[],
                created_at=now,
                last_active=now,
            )
            logger.debug(f"创建新会话: session_id={session_id}, user_id={user_id}")
        await self._save_session(session)
        return session

    async def get_conversation_history(self, session_id: str) -> List[Message]:
        """
        获取会话的对话历史消息列表。

        如果会话不存在或 Redis 不可用，返回空列表（不抛异常）。
        """
        session = await self._load_session(session_id)
        if session is None:
            return []
        # 续期 TTL（失败不影响读取，但不静默吞掉——记录以便排查 Redis 抖动）
        try:
            await self._redis.expire(self._key(session_id), self._ttl)
        except Exception as e:
            logger.warning(f"会话 TTL 续期失败 session_id={session_id}: {e}")
        return session.messages

    async def add_messages(
        self,
        session_id: str,
        user_id: str,
        user_content: str,
        assistant_content: str,
        intent: Optional[str] = None,
    ) -> None:
        """
        将一轮对话（user + assistant）追加到会话历史。

        超出 max_history 时从最早的消息开始裁剪。
        """
        session = await self._load_session(session_id)
        if session is None:
            now = datetime.now().isoformat()
            session = Session(
                session_id=session_id,
                user_id=user_id,
                messages=[],
                created_at=now,
                last_active=now,
            )

        now = datetime.now().isoformat()
        session.messages.append(Message(
            role="user",
            content=user_content,
            timestamp=now,
            intent=intent,
        ))
        session.messages.append(Message(
            role="assistant",
            content=assistant_content,
            timestamp=now,
        ))

        # 裁剪：保留最近 max_history 条
        if len(session.messages) > self._max_history:
            session.messages = session.messages[-self._max_history:]

        await self._save_session(session)
        logger.debug(f"会话历史已更新: session_id={session_id}, 总消息数={len(session.messages)}")

    async def clear_session(self, session_id: str) -> None:
        """删除会话（清空历史）"""
        try:
            await self._redis.delete(self._key(session_id))
            logger.info(f"会话已清除: session_id={session_id}")
        except Exception as e:
            logger.error(f"清除会话失败 session_id={session_id}: {e}")


# ─── 全局实例 ─────────────────────────────────────────────────────────────────

_session_memory_service: Optional[SessionMemoryService] = None


def initialize_session_memory(redis_client, ttl: int = 1800, max_history: int = 10) -> None:
    """初始化全局会话记忆服务（在应用启动时调用）"""
    global _session_memory_service
    _session_memory_service = SessionMemoryService(
        redis_client=redis_client,
        ttl=ttl,
        max_history=max_history,
    )
    logger.info("✅ SessionMemoryService 初始化完成")


def get_session_memory() -> Optional[SessionMemoryService]:
    """获取全局会话记忆服务实例"""
    return _session_memory_service


def generate_session_id() -> str:
    """生成新的会话 ID"""
    return str(uuid.uuid4())
