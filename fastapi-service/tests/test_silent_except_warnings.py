"""
技术债 #12：空 pass 吞异常 → logger.warning

覆盖两处确认"静默吞掉"的 except...pass：
1. session_memory.get_conversation_history 中 _redis.expire 失败
   （TTL 续期失败被静默吞掉）
2. llm_client 文本格式 <tool_call> JSON 解析失败
   （解析失败导致 tool_call 丢失，无任何日志）

跳过项（intentional control flow，保留 pass）：
- kb_navigator._normalize_source 的 except ValueError: pass
  → Path.relative_to 在路径不在 kb_root 下时抛 ValueError，
    紧接的 return Path(source).name 即正常 fallback，非吞异常
- langchain_rag.load CSV 的 except ImportError: pass  # 注释说明
  → 可选依赖缺失的有意防御，已有注释说明，非吞异常
"""

import logging

import pytest

from app.services.session_memory import SessionMemoryService, Session

pytestmark = pytest.mark.asyncio


class _FakeRedisExpireFails:
    """get 正常返回一个已存在会话，expire 抛异常（模拟 Redis TTL 续期失败）"""

    def __init__(self, session_json: bytes):
        self._session_json = session_json

    async def get(self, key):
        return self._session_json

    async def expire(self, key, ttl):
        raise RuntimeError("redis connection reset during EXPIRE")


async def test_session_memory_expire_failure_logs_warning(caplog):
    session = Session(
        session_id="s1",
        user_id="u1",
        messages=[],
        created_at="2026-05-17T00:00:00",
        last_active="2026-05-17T00:00:00",
    )
    fake = _FakeRedisExpireFails(session.model_dump_json().encode())
    svc = SessionMemoryService(redis_client=fake, ttl=1800)

    with caplog.at_level(logging.WARNING):
        history = await svc.get_conversation_history("s1")

    # 降级默认值：仍返回会话消息（空列表），不抛异常
    assert history == []
    # 不再静默吞掉：必须有 warning 日志
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("续期" in m or "expire" in m.lower() or "TTL" in m for m in warnings), warnings


def test_llm_client_toolcall_parse_failure_logs_warning(caplog):
    """
    构造一段含畸形 <tool_call> JSON 的 content，触发 llm_client 内
    json.loads 失败分支，断言不再静默 pass 而是 logger.warning。
    """
    from app.services import llm_client as llm_mod

    # 反射出解析逻辑所在：直接走 _parse_text_tool_call 辅助（实现侧需提供），
    # 若实现仍内联，则该测试通过对内联块的等价封装函数调用验证。
    parse_fn = getattr(llm_mod, "_parse_text_tool_call", None)
    assert parse_fn is not None, (
        "期望 llm_client 暴露 _parse_text_tool_call 以便测试畸形 JSON 分支"
    )

    bad_content = '前导文本 <tool_call> {不是合法json} </tool_call>'
    with caplog.at_level(logging.WARNING):
        result = parse_fn(bad_content)

    # 解析失败时返回 None（调用方据此 fallback 到 stop）
    assert result is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("tool_call" in m.lower() or "解析" in m for m in warnings), warnings
