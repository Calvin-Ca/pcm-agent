"""
长期用户记忆单元测试（Task 38.4）

使用内存 mock 替代真实 Redis，不依赖外部服务。
测试 UserMemoryService 的核心行为：
- 记忆存储与检索
- 时间衰减算法
- 记忆上限（淘汰低分记忆）
- 清除记忆
"""

import asyncio
import math
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.services.user_memory import UserMemoryService, UserMemory, DECAY_RATE


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_fake_redis():
    """基于字典的内存 Redis mock，支持 hset/hgetall/zadd/zrange/zrevrange/zcard/zrem/delete/pipeline"""
    hashes = {}     # key → {field: value}
    zsets = {}      # key → {member: score}

    redis = AsyncMock()

    async def fake_hset(key, mapping=None, **kwargs):
        if key not in hashes:
            hashes[key] = {}
        if mapping:
            hashes[key].update({k: str(v) for k, v in mapping.items()})

    async def fake_hgetall(key):
        return {k.encode(): v.encode() for k, v in hashes.get(key, {}).items()}

    async def fake_zadd(key, mapping):
        if key not in zsets:
            zsets[key] = {}
        zsets[key].update(mapping)

    async def fake_zrange(key, start, stop, withscores=False):
        members = sorted(zsets.get(key, {}).items(), key=lambda x: x[1])
        if stop == -1:
            selected = members[start:]
        else:
            selected = members[start:stop + 1]
        if withscores:
            return [(m.encode(), s) for m, s in selected]
        return [m.encode() for m, _ in selected]

    async def fake_zrevrange(key, start, stop, withscores=False):
        members = sorted(zsets.get(key, {}).items(), key=lambda x: x[1], reverse=True)
        if stop == -1:
            selected = members[start:]
        else:
            selected = members[start:stop + 1]
        if withscores:
            return [(m.encode(), s) for m, s in selected]
        return [m.encode() for m, _ in selected]

    async def fake_zcard(key):
        return len(zsets.get(key, {}))

    async def fake_zrem(key, *members):
        z = zsets.get(key, {})
        for m in members:
            z.pop(m if isinstance(m, str) else m.decode(), None)

    async def fake_delete(*keys):
        for key in keys:
            hashes.pop(key, None)
            zsets.pop(key, None)

    # Pipeline mock（execute 立即执行积累的命令）
    class FakePipeline:
        def __init__(self):
            self._cmds = []

        def delete(self, key):
            self._cmds.append(("delete", key))
            return self

        async def execute(self):
            for cmd, key in self._cmds:
                if cmd == "delete":
                    hashes.pop(key, None)
                    zsets.pop(key, None)
            return [True] * len(self._cmds)

    redis.hset = fake_hset
    redis.hgetall = fake_hgetall
    redis.zadd = fake_zadd
    redis.zrange = fake_zrange
    redis.zrevrange = fake_zrevrange
    redis.zcard = fake_zcard
    redis.zrem = fake_zrem
    redis.delete = fake_delete
    redis.pipeline = lambda: FakePipeline()

    redis._hashes = hashes
    redis._zsets = zsets

    return redis


@pytest.fixture
def redis_mock():
    return _make_fake_redis()


@pytest.fixture
def service(redis_mock):
    return UserMemoryService(redis_client=redis_mock)


# ─── store_memory ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_memory_returns_id(service):
    """存储后应返回非空 memory_id"""
    mid = await service.store_memory("user1", "用户倾向查本周数据", importance=0.7)
    assert mid
    assert isinstance(mid, str)


@pytest.mark.asyncio
async def test_stored_memory_retrievable(service):
    """存储后应能列出该条记忆"""
    await service.store_memory("user2", "用户是开发部门员工", importance=0.8)
    memories = await service.list_memories("user2")
    assert len(memories) == 1
    assert memories[0].content == "用户是开发部门员工"
    assert memories[0].importance == 0.8
    assert memories[0].user_id == "user2"


@pytest.mark.asyncio
async def test_importance_clamped(service):
    """importance 超出 [0, 1] 范围时应被截断"""
    await service.store_memory("user3", "测试", importance=1.5)
    mems = await service.list_memories("user3")
    assert mems[0].importance <= 1.0

    await service.store_memory("user3", "测试2", importance=-0.5)
    mems = await service.list_memories("user3")
    negs = [m for m in mems if m.content == "测试2"]
    assert negs[0].importance >= 0.0


@pytest.mark.asyncio
async def test_multiple_memories_stored(service):
    """可以为同一用户存储多条记忆"""
    for i in range(5):
        await service.store_memory("user4", f"记忆{i}", importance=0.5)
    mems = await service.list_memories("user4")
    assert len(mems) == 5


# ─── list_memories ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_empty_for_unknown_user(service):
    """未知用户返回空列表"""
    mems = await service.list_memories("unknown_user")
    assert mems == []


# ─── retrieve_relevant_memories ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_returns_top_k(service):
    """检索结果不超过 top_k"""
    for i in range(10):
        await service.store_memory("user5", f"工时记录 {i}", importance=0.5)
    results = await service.retrieve_relevant_memories("user5", "工时", top_k=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_retrieve_empty_for_no_memories(service):
    """无记忆时返回空列表"""
    results = await service.retrieve_relevant_memories("user_empty", "任意查询")
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_updates_access_count(service):
    """检索后 access_count 应增加"""
    await service.store_memory("user6", "工时填报偏好", importance=0.7)
    before = await service.list_memories("user6")
    assert before[0].access_count == 0

    await service.retrieve_relevant_memories("user6", "工时", top_k=5)
    after = await service.list_memories("user6")
    assert after[0].access_count >= 1


# ─── 时间衰减算法 ──────────────────────────────────────────────────────────────

def test_time_decay_factor_fresh(service):
    """刚创建的记忆（0天）衰减因子应接近 1.0"""
    now_iso = datetime.now().isoformat()
    factor = service._time_decay_factor(now_iso)
    assert abs(factor - 1.0) < 0.01


def test_time_decay_factor_7days(service):
    """7天前的记忆衰减因子应约为 0.5"""
    past = (datetime.now() - timedelta(days=7)).isoformat()
    factor = service._time_decay_factor(past)
    expected = math.exp(-DECAY_RATE * 7)
    assert abs(factor - expected) < 0.05


def test_time_decay_factor_30days(service):
    """30天前的记忆衰减因子约为 0.05"""
    past = (datetime.now() - timedelta(days=30)).isoformat()
    factor = service._time_decay_factor(past)
    expected = math.exp(-DECAY_RATE * 30)
    assert abs(factor - expected) < 0.05


def test_time_decay_always_positive(service):
    """时间衰减因子始终为正值"""
    for days in [0, 1, 7, 30, 365]:
        past = (datetime.now() - timedelta(days=days)).isoformat()
        factor = service._time_decay_factor(past)
        assert factor > 0


def test_time_decay_decreasing(service):
    """时间越长，衰减因子越小"""
    factors = []
    for days in [0, 1, 7, 14, 30]:
        past = (datetime.now() - timedelta(days=days)).isoformat()
        factors.append(service._time_decay_factor(past))
    assert all(factors[i] >= factors[i + 1] for i in range(len(factors) - 1))


# ─── BM25/tokenize 工具方法 ───────────────────────────────────────────────────

def test_tokenize_chinese(service):
    """中文文本分词应提取每个汉字"""
    tokens = service._tokenize("工时管理")
    assert "工" in tokens
    assert "时" in tokens
    assert "管" in tokens
    assert "理" in tokens


def test_tokenize_english(service):
    """英文应小写化"""
    tokens = service._tokenize("UserID")
    assert "u" in tokens or "userid" in tokens or "user" in tokens


def test_bm25_score_full_match(service):
    """完全匹配时分数应为 1.0"""
    score = service._bm25_score(["工", "时"], ["工", "时", "管", "理"])
    assert score == 1.0


def test_bm25_score_no_match(service):
    """无匹配时分数应为 0.0"""
    score = service._bm25_score(["xyz"], ["工", "时"])
    assert score == 0.0


def test_bm25_score_partial_match(service):
    """部分匹配时分数在 0 和 1 之间"""
    score = service._bm25_score(["工", "时", "xyz"], ["工", "时"])
    assert 0.0 < score < 1.0


# ─── clear_memories ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_memories_removes_all(service):
    """清除后 list_memories 应返回空列表"""
    for i in range(3):
        await service.store_memory("user7", f"记忆{i}")
    assert len(await service.list_memories("user7")) == 3

    deleted = await service.clear_memories("user7")
    assert deleted == 3
    assert await service.list_memories("user7") == []


@pytest.mark.asyncio
async def test_clear_empty_user_returns_zero(service):
    """无记忆用户清除时返回 0"""
    deleted = await service.clear_memories("no_memories_user")
    assert deleted == 0


# ─── score_memories（综合评分）────────────────────────────────────────────────

def test_score_memories_returns_pairs(service):
    """_score_memories 应返回 (memory, score) 元组列表"""
    memories = [
        UserMemory(
            memory_id="m1",
            user_id="u",
            content="工时查询偏好",
            importance=0.8,
            access_count=0,
            created_at=datetime.now().isoformat(),
            last_accessed=datetime.now().isoformat(),
        )
    ]
    result = service._score_memories(memories, "工时")
    assert len(result) == 1
    memory, score = result[0]
    assert memory.memory_id == "m1"
    assert score > 0


def test_score_higher_for_relevant_memory(service):
    """关键词匹配的记忆应比无关记忆得分更高（其他条件相同）"""
    now = datetime.now().isoformat()
    relevant = UserMemory(
        memory_id="r",
        user_id="u",
        content="工 时 填 报 规 则",
        importance=0.5,
        access_count=0,
        created_at=now,
        last_accessed=now,
    )
    irrelevant = UserMemory(
        memory_id="i",
        user_id="u",
        content="假 期 申 请 流 程",
        importance=0.5,
        access_count=0,
        created_at=now,
        last_accessed=now,
    )
    scored = service._score_memories([relevant, irrelevant], "工 时 填 报")
    scores = {m.memory_id: s for m, s in scored}
    assert scores["r"] > scores["i"]
