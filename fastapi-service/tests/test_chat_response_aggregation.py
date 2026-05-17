"""回归守卫：非流式 /api/ai/chat 聚合 response 事件必须累积而非覆盖。

生产事故（2026-05-17）：流式 RAG（knowledge_qa）路径把答案逐 chunk 作为多个
独立 `response` SSE 事件发出，末尾再单独发一个来源 footer 事件。非流式端点
原用 `final_response = data.get("message")` 覆盖，于是只剩最后一个事件＝footer，
整段答案体丢失（用户只看到「📚 来源：...」无正文）。DB 落库正常（langgraph
侧 `_full_response += text` 累积无误），故 success=true 掩盖了数据丢失。

工具/LLM/澄清/计划路径只发单个全量 response 事件，累积后等价自身，不重复。
"""

from app.api.chat import _accumulate_response_text


def _run(events):
    """模拟非流式端点对一串 response 事件 data 的聚合。"""
    acc = None
    for data in events:
        acc = _accumulate_response_text(acc, data)
    return acc


def test_streaming_rag_chunks_plus_footer_accumulated():
    """流式 RAG：多 chunk + footer 事件 → 完整答案 + footer（不丢正文）。"""
    events = [
        {"message": "请假流程如下：\n"},
        {"message": "1. 员工提交申请\n"},
        {"message": "2. 主管审批\n"},
        {"message": "3. HR 备案\n"},
        {"message": "\n\n---\n📚 **来源：** 请假申请流程.md | 请假常见问题.md"},
    ]
    result = _run(events)
    assert result == (
        "请假流程如下：\n1. 员工提交申请\n2. 主管审批\n3. HR 备案\n"
        "\n\n---\n📚 **来源：** 请假申请流程.md | 请假常见问题.md"
    )
    # 关键回归断言：正文不能被 footer 覆盖
    assert "员工提交申请" in result
    assert result != events[-1]["message"]


def test_single_full_event_equivalent_to_itself():
    """工具/LLM 单事件全量路径：累积后等价自身，不重复。"""
    events = [{"message": "本月共填报 23.5 小时，分布在 3 个项目。"}]
    assert _run(events) == "本月共填报 23.5 小时，分布在 3 个项目。"


def test_empty_messages_ignored():
    """空 message 事件不应把已累积内容清空或注入 None。"""
    events = [
        {"message": "答案前半"},
        {"message": ""},
        {"result": {}},
        {"message": "答案后半"},
    ]
    assert _run(events) == "答案前半答案后半"


def test_response_fallback_field():
    """message 缺失时回退 result.response。"""
    assert _run([{"result": {"response": "兜底答案"}}]) == "兜底答案"


def test_no_response_events_returns_none():
    assert _run([]) is None
