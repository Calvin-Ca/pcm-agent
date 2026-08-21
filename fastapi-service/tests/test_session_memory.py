"""
短期会话记忆单元测试（Task 37.3）

使用 fakeredis 模拟 Redis，不依赖真实 Redis 服务。
测试 SessionMemoryService 的核心行为：
- 会话创建与 TTL 续期
- 对话历史保存（最多 max_history 条）
- 历史检索
- 会话清除
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.session_memory import (
    SessionMemoryService,
    Session,
    Message,
    generate_session_id,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_fake_redis():
    """创建一个基于字典的内存 Redis mock，支持 get/setex/expire/delete"""
    store = {}
    ttls = {}

    redis = AsyncMock()

    async def fake_get(key):
        return store.get(key)

    async def fake_setex(key, ttl, value):
        store[key] = value
        ttls[key] = ttl

    async def fake_expire(key, ttl):
        if key in store:
            ttls[key] = ttl

    async def fake_delete(*keys):
        for key in keys:
            store.pop(key, None)
            ttls.pop(key, None)

    redis.get = fake_get
    redis.setex = fake_setex
    redis.expire = fake_expire
    redis.delete = fake_delete
    redis._store = store   # 供测试直接检查
    redis._ttls = ttls

    return redis


@pytest.fixture
def redis_mock():
    return _make_fake_redis()


@pytest.fixture
def service(redis_mock):
    return SessionMemoryService(redis_client=redis_mock, ttl=1800, max_history=6)


# ─── generate_session_id ──────────────────────────────────────────────────────

def test_generate_session_id_is_unique():
    """每次生成的 session_id 不同"""
    ids = {generate_session_id() for _ in range(100)}
    assert len(ids) == 100


def test_generate_session_id_format():
    """session_id 是 UUID 格式"""
    import uuid
    sid = generate_session_id()
    uuid.UUID(sid)  # 不抛出则格式正确


# ─── get_or_create_session ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_new_session(service):
    """不存在的 session 应被自动创建"""
    session = await service.get_or_create_session("sess_001", "user_001")
    assert session.session_id == "sess_001"
    assert session.user_id == "user_001"
    assert session.messages == []


@pytest.mark.asyncio
async def test_get_existing_session(service):
    """再次获取同一 session_id 应返回同一会话"""
    await service.get_or_create_session("sess_002", "user_002")
    session = await service.get_or_create_session("sess_002", "user_002")
    assert session.session_id == "sess_002"
    assert session.user_id == "user_002"


@pytest.mark.asyncio
async def test_session_persisted_in_redis(service, redis_mock):
    """创建后 Redis 中应存有对应 key"""
    await service.get_or_create_session("sess_003", "user_003")
    assert "session:sess_003" in redis_mock._store


@pytest.mark.asyncio
async def test_session_ttl_set(service, redis_mock):
    """创建会话时应设置 TTL"""
    await service.get_or_create_session("sess_ttl", "user_ttl")
    assert redis_mock._ttls.get("session:sess_ttl") == 1800


# ─── add_messages / get_conversation_history ─────────────────────────────────

@pytest.mark.asyncio
async def test_add_messages_saves_both_roles(service):
    """add_messages 应保存 user 和 assistant 各一条"""
    await service.add_messages(
        session_id="sess_a",
        user_id="u1",
        user_content="你好",
        assistant_content="您好！有什么可以帮您？",
    )
    history = await service.get_conversation_history("sess_a")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "你好"
    assert history[1].role == "assistant"
    assert history[1].content == "您好！有什么可以帮您？"


@pytest.mark.asyncio
async def test_multiple_rounds_accumulate(service):
    """多轮对话后历史长度应累加"""
    for i in range(3):
        await service.add_messages(
            session_id="sess_b",
            user_id="u2",
            user_content=f"第{i}轮提问",
            assistant_content=f"第{i}轮回答",
        )
    history = await service.get_conversation_history("sess_b")
    assert len(history) == 6  # 3 轮 × 2 条


@pytest.mark.asyncio
async def test_history_capped_at_max_history(service):
    """超出 max_history 时应只保留最新的消息"""
    # max_history=6，写 4 轮（8条），应只保留最新 6 条
    for i in range(4):
        await service.add_messages(
            session_id="sess_c",
            user_id="u3",
            user_content=f"问{i}",
            assistant_content=f"答{i}",
        )
    history = await service.get_conversation_history("sess_c")
    assert len(history) == 6
    # 最新的是第 3 轮（index 3）的 user/assistant
    assert "问3" in history[-2].content
    assert "答3" in history[-1].content


@pytest.mark.asyncio
async def test_history_empty_for_nonexistent_session(service):
    """不存在的 session 返回空列表"""
    history = await service.get_conversation_history("no_such_session")
    assert history == []


@pytest.mark.asyncio
async def test_message_intent_stored(service):
    """intent 字段应被正确存储"""
    await service.add_messages(
        session_id="sess_intent",
        user_id="u4",
        user_content="查询工时",
        assistant_content="查询结果...",
        intent="tool_execution",
    )
    history = await service.get_conversation_history("sess_intent")
    assert history[0].intent == "tool_execution"
    assert history[1].intent is None  # assistant 消息没有 intent


@pytest.mark.asyncio
async def test_message_timestamp_set(service):
    """消息应包含时间戳"""
    await service.add_messages(
        session_id="sess_ts",
        user_id="u5",
        user_content="测试",
        assistant_content="回复",
    )
    history = await service.get_conversation_history("sess_ts")
    for msg in history:
        assert msg.timestamp  # 非空
        datetime.fromisoformat(msg.timestamp)  # 格式有效


@pytest.mark.asyncio
async def test_business_state_persists_without_credentials(service):
    state = {
        "last_tool": {
            "name": "query_timesheet",
            "params": {"member_name": "张三", "start_date": "2026-07-27", "end_date": "2026-08-02"},
        }
    }
    await service.update_business_state("sess_state", "u-state", state)

    loaded = await service.get_business_state("sess_state")

    assert loaded == state


# ─── clear_session ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_session_removes_data(service, redis_mock):
    """清除会话后 Redis key 应消失，历史返回空"""
    await service.add_messages("sess_del", "u6", "hi", "hello")
    assert "session:sess_del" in redis_mock._store

    await service.clear_session("sess_del")
    assert "session:sess_del" not in redis_mock._store

    history = await service.get_conversation_history("sess_del")
    assert history == []


@pytest.mark.asyncio
async def test_clear_nonexistent_session_no_error(service):
    """清除不存在的 session 不应抛出异常"""
    await service.clear_session("nonexistent_session")  # 不抛异常即通过
