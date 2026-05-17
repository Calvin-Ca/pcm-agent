"""
单元测试: 技术债 #7 —— log_conversation() 参数过多，用 Pydantic 模型封装

覆盖:
- ConversationLogEntry 字段齐全、默认值与原签名一致
- log_conversation(entry) 经模型路径写入 DB 的字段集与重构前等价
- 写库异常时仍返回 -1 不抛出
"""

import pytest
from unittest.mock import MagicMock, patch


# ─── 模型字段与默认值 ─────────────────────────────────────────────────────────


def test_conversation_log_entry_fields_and_defaults():
    from app.models.conversation import ConversationLogEntry

    entry = ConversationLogEntry(
        session_id="s1",
        user_id="u1",
        user_message="hello",
        route_type="LLM_SERVICE",
    )
    # 必填字段
    assert entry.session_id == "s1"
    assert entry.user_id == "u1"
    assert entry.user_message == "hello"
    assert entry.route_type == "LLM_SERVICE"
    # 默认值须与原 log_conversation 签名一致
    assert entry.ai_response is None
    assert entry.intent is None
    assert entry.history_turns_count == 0
    assert entry.memory_count == 0
    assert entry.context_snapshot is None
    assert entry.tools_called is None
    assert entry.has_task_plan is False
    assert entry.task_plan is None
    assert entry.duration_ms is None
    assert entry.prompt_tokens == 0
    assert entry.completion_tokens == 0
    assert entry.model_name is None
    assert entry.status == "success"
    assert entry.error_message is None
    assert entry.extra_data is None


def test_conversation_log_entry_full_payload():
    from app.models.conversation import ConversationLogEntry

    entry = ConversationLogEntry(
        session_id="s2",
        user_id="u2",
        user_message="msg",
        route_type="TOOL_CALL",
        ai_response="resp",
        intent="query_timesheet",
        history_turns_count=3,
        memory_count=2,
        context_snapshot={"history": []},
        tools_called=[{"name": "query_timesheet"}],
        has_task_plan=True,
        task_plan={"steps": 1},
        duration_ms=120,
        prompt_tokens=10,
        completion_tokens=20,
        model_name="qwen-plus",
        status="error",
        error_message="boom",
        extra_data={"k": "v"},
    )
    assert entry.tools_called == [{"name": "query_timesheet"}]
    assert entry.prompt_tokens == 10
    assert entry.completion_tokens == 20


# ─── log_conversation 经模型路径写库字段等价 ──────────────────────────────────


@patch("app.services.conversation_logger.ConversationLog")
@patch("app.services.conversation_logger.get_db_service")
def test_log_conversation_writes_equivalent_fields(mock_get_db, mock_log_model):
    from app.services.conversation_logger import ConversationLogger
    from app.models.conversation import ConversationLogEntry

    # mock DB session 上下文
    fake_session = MagicMock()
    fake_log = MagicMock()
    fake_log.id = 42
    mock_log_model.return_value = fake_log
    cm = MagicMock()
    cm.__enter__.return_value = fake_session
    cm.__exit__.return_value = False
    mock_db = MagicMock()
    mock_db.get_session.return_value = cm
    mock_get_db.return_value = mock_db

    cl = ConversationLogger()
    entry = ConversationLogEntry(
        session_id="s3",
        user_id="u3",
        user_message="hi",
        route_type="LLM_SERVICE",
        intent="general_chat",
        history_turns_count=1,
        memory_count=0,
        tools_called=[{"name": "x"}, {"name": "y"}],
        ai_response="ok",
        duration_ms=55,
        prompt_tokens=4,
        completion_tokens=6,
        model_name="qwen-plus",
        status="success",
    )
    log_id = cl.log_conversation(entry)

    assert log_id == 42
    kwargs = mock_log_model.call_args.kwargs
    # 字段集等价校验（与重构前 ConversationLog(...) 构造保持一致）
    assert kwargs["session_id"] == "s3"
    assert kwargs["user_id"] == "u3"
    assert kwargs["user_message"] == "hi"
    assert kwargs["route_type"] == "LLM_SERVICE"
    assert kwargs["intent"] == "general_chat"
    assert kwargs["history_turns_count"] == 1
    assert kwargs["memory_count"] == 0
    assert kwargs["tools_called"] == [{"name": "x"}, {"name": "y"}]
    # tool_count 由 tools_called 长度派生
    assert kwargs["tool_count"] == 2
    assert kwargs["ai_response"] == "ok"
    assert kwargs["duration_ms"] == 55
    assert kwargs["prompt_tokens"] == 4
    assert kwargs["completion_tokens"] == 6
    # total_tokens = prompt + completion
    assert kwargs["total_tokens"] == 10
    assert kwargs["model_name"] == "qwen-plus"
    assert kwargs["status"] == "success"
    fake_session.add.assert_called_once_with(fake_log)


@patch("app.services.conversation_logger.get_db_service")
def test_log_conversation_returns_minus_one_on_failure(mock_get_db):
    from app.services.conversation_logger import ConversationLogger
    from app.models.conversation import ConversationLogEntry

    mock_db = MagicMock()
    mock_db.get_session.side_effect = RuntimeError("db down")
    mock_get_db.return_value = mock_db

    cl = ConversationLogger()
    entry = ConversationLogEntry(
        session_id="s",
        user_id="u",
        user_message="m",
        route_type="LLM_SERVICE",
    )
    assert cl.log_conversation(entry) == -1
