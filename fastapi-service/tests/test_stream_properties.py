"""
流式响应属性测试

使用Hypothesis进行基于属性的测试，验证SSE流式响应的核心属性：
- 属性1: 流式响应完整性
- 验证需求: 1.4, 12.1, 12.2

属性1: 流式响应完整性
验证所有响应片段组合后与预期内容一致
"""

import pytest
from hypothesis import given, strategies as st, settings, assume
import json
import re

from app.services.stream_response import SSEEventType, StreamResponseGenerator


# 生成有效的SSE事件数据
@st.composite
def sse_event_data(draw):
    """生成随机的SSE事件数据"""
    event_types = st.sampled_from(list(SSEEventType))
    event_type = draw(event_types)

    # 生成随机但合理的payload
    if event_type == SSEEventType.START:
        payload = {
            "message": draw(st.text(min_size=1, max_size=100)),
            "session_id": draw(st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('L', 'N', 'P')))),
            "timestamp": "2024-01-01T00:00:00"
        }
    elif event_type == SSEEventType.RESPONSE:
        payload = {
            "message": draw(st.text(min_size=1, max_size=500)),
            "chunk": draw(st.text(min_size=1, max_size=200)),
            "is_partial": draw(st.booleans())
        }
    elif event_type == SSEEventType.ERROR:
        payload = {
            "message": draw(st.text(min_size=1, max_size=200)),
            "error_code": draw(st.sampled_from(["E001", "E002", "E003"]))
        }
    else:
        payload = {
            "message": draw(st.text(min_size=1, max_size=200)),
            "data": draw(st.dictionaries(st.text(min_size=1, max_size=20), st.text(min_size=1, max_size=50)))
        }

    return {"event_type": event_type, "payload": payload}


# 生成SSE事件字符串
@st.composite
def sse_event_strings(draw):
    """生成有效的SSE事件字符串"""
    event_type = draw(st.sampled_from(list(SSEEventType)))
    data = draw(st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.text(min_size=1, max_size=100)
    ))

    # 构建SSE格式的事件字符串
    event_str = f"event: {event_type.value}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return event_str


class TestStreamResponseCompleteness:
    """
    属性1: 流式响应完整性
    验证所有响应片段组合后与预期内容一致
    验证需求: 1.4, 12.1, 12.2
    """

    @given(sse_event_data())
    @settings(max_examples=100, deadline=None)
    def test_single_event_format(self, event_data):
        """测试单个事件的格式正确性"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        event_str = generator.format_sse_event(
            event_data["event_type"],
            event_data["payload"]
        )

        # 验证事件格式
        assert event_str.startswith(f"event: {event_data['event_type'].value}")
        assert "data:" in event_str
        assert event_str.endswith("\n\n")

        # 验证JSON数据可解析
        lines = event_str.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data:")][0]
        data_json = data_line.replace("data: ", "")
        parsed = json.loads(data_json)

        # 验证关键字段存在
        assert "message" in parsed or "chunk" in parsed or "error_code" in parsed

    @given(st.lists(sse_event_strings(), min_size=1, max_size=10))
    @settings(max_examples=50, deadline=None)
    def test_multiple_events_concatenation(self, events):
        """测试多个事件连接后的完整性"""
        # 将所有事件连接成一个字符串
        combined = "".join(events)

        # 验证可以通过双换行符分割出完整事件
        split_events = [e for e in combined.split("\n\n") if e.strip()]

        assert len(split_events) == len(events)

        # 验证每个分割后的事件都是有效的
        for event in split_events:
            assert "event:" in event
            assert "data:" in event

            # 提取event类型
            event_match = re.search(r'event:\s*(\w+)', event)
            assert event_match is not None
            event_type = event_match.group(1)
            assert event_type in [e.value for e in SSEEventType]

    @given(st.text(min_size=1, max_size=1000))
    @settings(max_examples=50, deadline=None)
    def test_payload_json_encoding(self, text_content):
        """测试payload JSON编码的完整性"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        # 包含特殊字符的payload
        payload = {
            "message": text_content,
            "timestamp": "2024-01-01T00:00:00"
        }

        event_str = generator.format_sse_event(SSEEventType.RESPONSE, payload)

        # 提取data部分
        lines = event_str.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data:")]
        assert len(data_lines) == 1

        data_json = data_lines[0].replace("data: ", "")
        parsed = json.loads(data_json)

        # 验证内容完整性
        assert parsed["message"] == text_content


class TestStreamResponseEventTypes:
    """SSE事件类型完整性测试"""

    @pytest.mark.parametrize("event_type", list(SSEEventType))
    def test_all_event_types_format_correctly(self, event_type):
        """测试所有事件类型都能正确格式化"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        payload = {"message": "test", "data": "test_data"}
        event_str = generator.format_sse_event(event_type, payload)

        # 验证包含正确的事件类型声明
        assert f"event: {event_type.value}" in event_str

    def test_event_type_consistency(self):
        """测试事件类型的一致性"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        # 多次格式化相同类型的事件，结果应该一致
        payload = {"message": "test"}

        event1 = generator.format_sse_event(SSEEventType.START, payload)
        event2 = generator.format_sse_event(SSEEventType.START, payload)

        assert event1 == event2


class TestStreamResponseOrdering:
    """流式响应顺序测试"""

    def test_standard_event_sequence(self):
        """测试标准事件序列顺序"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        # 标准序列：START -> THINKING -> ... -> DONE
        standard_sequence = [
            (SSEEventType.START, {"message": "开始"}),
            (SSEEventType.THINKING, {"message": "思考中"}),
            (SSEEventType.RESPONSE, {"message": "响应"}),
            (SSEEventType.DONE, {"message": "完成"})
        ]

        events = []
        for event_type, payload in standard_sequence:
            event_str = generator.format_sse_event(event_type, payload)
            events.append(event_str)

        # 验证所有事件都能正确生成
        assert len(events) == len(standard_sequence)

        # 验证每个事件包含正确的类型
        for i, (event_type, _) in enumerate(standard_sequence):
            assert f"event: {event_type.value}" in events[i]


class TestStreamResponseEdgeCases:
    """流式响应边界情况测试"""

    def test_empty_payload(self):
        """测试空payload处理"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        event_str = generator.format_sse_event(SSEEventType.START, {})

        # 应该生成有效的事件格式
        assert "event: start" in event_str
        # 空payload会添加默认timestamp，所以检查data字段存在即可
        assert "data:" in event_str

    def test_special_characters_in_payload(self):
        """测试payload中特殊字符的处理"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        special_payloads = [
            {"message": "包含中文的内容"},
            {"message": "Contains \"quotes\""},
            {"message": "Line1\nLine2"},
            {"message": "Tab\there"},
            {"message": "Special: <>&"}
        ]

        for payload in special_payloads:
            event_str = generator.format_sse_event(SSEEventType.RESPONSE, payload)

            # 验证可以正确解析
            data_match = re.search(r'data: (.+?)(?:\n\n|$)', event_str, re.DOTALL)
            assert data_match is not None

            data_json = data_match.group(1)
            parsed = json.loads(data_json)

            # 验证内容完整保留
            assert parsed["message"] == payload["message"]

    def test_nested_payload(self):
        """测试嵌套payload的处理"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        nested_payload = {
            "message": "测试",
            "nested": {
                "level1": {
                    "level2": "deep_value"
                }
            },
            "array": [1, 2, 3],
            "mixed": [{"key": "value"}, {"key": "value2"}]
        }

        event_str = generator.format_sse_event(SSEEventType.RESPONSE, nested_payload)

        # 提取并解析data
        data_match = re.search(r'data: (.+?)(?:\n\n|$)', event_str, re.DOTALL)
        assert data_match is not None

        data_json = data_match.group(1)
        parsed = json.loads(data_json)

        # 验证嵌套结构完整
        assert parsed["nested"]["level1"]["level2"] == "deep_value"
        assert parsed["array"] == [1, 2, 3]
        assert len(parsed["mixed"]) == 2


class TestStreamResponseGeneratorProperties:
    """StreamResponseGenerator属性测试"""

    def test_generator_initialization(self):
        """测试生成器初始化"""
        mock_intent_router = Mock()
        mock_task_executor = Mock()
        mock_llm_client = Mock()

        generator = StreamResponseGenerator(
            intent_router=mock_intent_router,
            task_executor=mock_task_executor,
            llm_client=mock_llm_client
        )

        assert generator.intent_router is mock_intent_router
        assert generator.task_executor is mock_task_executor
        assert generator.llm_client is mock_llm_client

    def test_generator_optional_dependencies(self):
        """测试可选依赖的处理"""
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        # 应该能正常工作（虽然某些功能可能受限）
        assert generator.task_executor is None
        assert generator.llm_client is None


# 导入Mock
from unittest.mock import Mock
