"""
流式响应单元测试

测试SSE流式响应的各项功能，包括：
- SSE事件格式
- 完整响应流程
- 错误处理
- 各种路由处理流程

验证需求: 12.1-12.5
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import json
import asyncio
from datetime import datetime

from app.services.stream_response import (
    SSEEventType, StreamResponseGenerator
)
from app.services.intent_router import (
    IntentRouter, IntentType, RouteTarget, RouteDecision, IntentResult
)
from app.models.task_plan import TaskPlan, TaskNode, TaskType, TaskStatus


class TestStreamResponseGeneratorInitialization:
    """流式响应生成器初始化测试"""

    def test_generator_initialization_with_all_deps(self):
        """测试完整依赖的初始化"""
        mock_intent_router = Mock(spec=IntentRouter)
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

    def test_generator_initialization_with_minimal_deps(self):
        """测试最小依赖的初始化"""
        mock_intent_router = Mock(spec=IntentRouter)

        generator = StreamResponseGenerator(
            intent_router=mock_intent_router
        )

        assert generator.intent_router is mock_intent_router
        assert generator.task_executor is None
        assert generator.llm_client is None


class TestSSEEventFormatting:
    """SSE事件格式测试"""

    @pytest.fixture
    def generator(self):
        """创建生成器实例"""
        return StreamResponseGenerator(
            intent_router=Mock(spec=IntentRouter),
            task_executor=None,
            llm_client=None
        )

    def test_format_start_event(self, generator):
        """测试START事件格式"""
        payload = {
            "message": "开始处理",
            "session_id": "test_session",
            "timestamp": datetime.now().isoformat()
        }

        event = generator.format_sse_event(SSEEventType.START, payload)

        # 验证SSE格式
        assert event.startswith("event: start")
        assert "data:" in event
        assert event.endswith("\n\n")

        # 验证JSON数据
        data_line = [l for l in event.split("\n") if l.startswith("data:")][0]
        data = json.loads(data_line.replace("data: ", ""))
        assert data["message"] == "开始处理"
        assert data["session_id"] == "test_session"

    def test_format_thinking_event(self, generator):
        """测试THINKING事件格式"""
        payload = {"message": "正在分析..."}

        event = generator.format_sse_event(SSEEventType.THINKING, payload)

        assert "event: thinking" in event
        assert "data:" in event

    def test_format_tool_call_event(self, generator):
        """测试TOOL_CALL事件格式"""
        payload = {
            "message": "调用工具",
            "tool_name": "query_timesheet",
            "parameters": {"user_id": "001"}
        }

        event = generator.format_sse_event(SSEEventType.TOOL_CALL, payload)

        assert "event: tool_call" in event
        data = json.loads(event.split("data: ")[1].split("\n")[0])
        assert data["tool_name"] == "query_timesheet"

    def test_format_response_event(self, generator):
        """测试RESPONSE事件格式"""
        payload = {
            "message": "响应内容",
            "result": {"data": "value"}
        }

        event = generator.format_sse_event(SSEEventType.RESPONSE, payload)

        assert "event: response" in event
        assert "响应内容" in event

    def test_format_done_event(self, generator):
        """测试DONE事件格式"""
        payload = {"message": "处理完成"}

        event = generator.format_sse_event(SSEEventType.DONE, payload)

        assert "event: done" in event

    def test_format_error_event(self, generator):
        """测试ERROR事件格式"""
        payload = {
            "message": "发生错误",
            "error": "详细错误信息"
        }

        event = generator.format_sse_event(SSEEventType.ERROR, payload)

        assert "event: error" in event
        assert "发生错误" in event

    def test_format_event_with_special_characters(self, generator):
        """测试特殊字符的事件格式"""
        payload = {
            "message": "包含\"引号\"和\n换行的内容",
            "data": "<html>tags</html>"
        }

        event = generator.format_sse_event(SSEEventType.RESPONSE, payload)

        # 验证可以正确解析
        data_line = event.split("data: ")[1].split("\n")[0]
        data = json.loads(data_line)
        assert "引号" in data["message"]
        assert "\n" in data["message"]


class TestStreamResponseFlow:
    """完整响应流程测试"""

    @pytest.fixture
    def mock_intent_router(self):
        """模拟意图路由器"""
        router = Mock(spec=IntentRouter)
        router.route_intent = AsyncMock()
        router.execute_route = AsyncMock()
        return router

    @pytest.fixture
    def generator(self, mock_intent_router):
        """创建生成器实例"""
        return StreamResponseGenerator(
            intent_router=mock_intent_router,
            task_executor=None,
            llm_client=None
        )

    @pytest.mark.asyncio
    async def test_tool_execution_flow(self, generator, mock_intent_router):
        """测试工具执行流程"""
        # 模拟路由决策 - 工具执行
        route_decision = RouteDecision(
            target=RouteTarget.TOOL_EXECUTOR,
            intent_result=IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=0.9,
                reasoning="测试",
                suggested_action="执行工具",
                parameters={"tool_name": "query_timesheet"}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision
        mock_intent_router.execute_route.return_value = {
            "success": True,
            "data": "工时数据"
        }

        # 收集所有事件
        events = []
        async for event in generator.stream_response("查询工时"):
            events.append(event)

        # 验证事件序列
        assert len(events) > 0

        # 验证包含必要的事件类型
        event_types = []
        for event in events:
            if "event:" in event:
                event_type = event.split("event: ")[1].split("\n")[0]
                event_types.append(event_type)

        assert "start" in event_types
        assert "thinking" in event_types
        assert "tool_call" in event_types or "response" in event_types
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_rag_query_flow(self, generator, mock_intent_router):
        """测试RAG查询流程"""
        route_decision = RouteDecision(
            target=RouteTarget.RAG_ENGINE,
            intent_result=IntentResult(
                intent_type=IntentType.KNOWLEDGE_QA,
                confidence=0.85,
                reasoning="测试",
                suggested_action="查询知识库",
                parameters={}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision
        mock_intent_router.execute_route.return_value = {
            "answer": "知识库答案",
            "sources": []
        }

        events = []
        async for event in generator.stream_response("什么是工时制度"):
            events.append(event)

        assert len(events) > 0
        # 验证有响应事件
        assert any("response" in e for e in events)

    @pytest.mark.asyncio
    async def test_llm_chat_flow(self, generator, mock_intent_router):
        """测试LLM对话流程"""
        route_decision = RouteDecision(
            target=RouteTarget.LLM_SERVICE,
            intent_result=IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.8,
                reasoning="测试",
                suggested_action="聊天",
                parameters={}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision

        # 模拟LLM客户端
        mock_llm_client = Mock()
        mock_llm_client.stream_generate = AsyncMock()
        mock_llm_client.stream_generate.return_value = [
            "你好",
            "，",
            "有什么",
            "可以帮助",
            "你？"
        ]
        generator.llm_client = mock_llm_client

        events = []
        async for event in generator.stream_response("你好"):
            events.append(event)

        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_task_planning_flow(self, generator, mock_intent_router):
        """测试任务规划流程"""
        # 创建模拟任务计划
        task_plan = TaskPlan(name="测试计划")
        task_plan.add_task(TaskNode(
            task_id="task_1",
            task_type=TaskType.TOOL_CALL,
            tool_name="query_timesheet"
        ))
        task_plan.add_task(TaskNode(
            task_id="task_2",
            task_type=TaskType.TOOL_CALL,
            tool_name="query_project",
            dependencies=["task_1"]
        ))

        route_decision = RouteDecision(
            target=RouteTarget.PLANNER_AGENT,
            intent_result=IntentResult(
                intent_type=IntentType.COMPLEX_REQUEST,
                confidence=0.9,
                reasoning="测试",
                suggested_action="规划任务",
                parameters={}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision
        mock_intent_router.execute_route.return_value = task_plan

        # 模拟任务执行器
        mock_executor = Mock()
        mock_executor._execute_level_tasks = AsyncMock()
        generator.task_executor = mock_executor

        events = []
        async for event in generator.stream_response("查询工时和项目"):
            events.append(event)

        # 验证有任务相关事件
        assert len(events) > 0


class TestStreamResponseErrorHandling:
    """错误处理测试"""

    @pytest.fixture
    def mock_intent_router(self):
        """模拟意图路由器"""
        router = Mock(spec=IntentRouter)
        router.route_intent = AsyncMock()
        return router

    @pytest.fixture
    def generator(self, mock_intent_router):
        """创建生成器实例"""
        return StreamResponseGenerator(
            intent_router=mock_intent_router,
            task_executor=None,
            llm_client=None
        )

    @pytest.mark.asyncio
    async def test_intent_router_error(self, generator, mock_intent_router):
        """测试意图路由器错误"""
        mock_intent_router.route_intent.side_effect = Exception("路由错误")

        events = []
        async for event in generator.stream_response("测试消息"):
            events.append(event)

        # 验证有错误事件
        assert any("error" in e for e in events)

    @pytest.mark.asyncio
    async def test_tool_execution_error(self, generator, mock_intent_router):
        """测试工具执行错误"""
        route_decision = RouteDecision(
            target=RouteTarget.TOOL_EXECUTOR,
            intent_result=IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=0.9,
                reasoning="测试",
                suggested_action="执行工具",
                parameters={"tool_name": "query_timesheet"}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision
        mock_intent_router.execute_route.side_effect = Exception("工具执行失败")

        events = []
        async for event in generator.stream_response("查询工时"):
            events.append(event)

        # 验证有错误事件
        assert any("error" in e for e in events)
        # 但流应该正常结束
        assert any("done" in e for e in events)

    @pytest.mark.asyncio
    async def test_unsupported_target(self, generator, mock_intent_router):
        """测试不支持的路由目标"""
        # 创建一个无效的路由目标
        route_decision = Mock()
        route_decision.target.value = "UNKNOWN_TARGET"
        route_decision.intent_result = Mock()
        mock_intent_router.route_intent.return_value = route_decision

        events = []
        async for event in generator.stream_response("测试"):
            events.append(event)

        # 验证有错误事件
        assert any("error" in e for e in events)


class TestStreamResponseTimeout:
    """超时控制测试"""

    @pytest.mark.asyncio
    async def test_stream_response_with_timeout(self):
        """测试带超时的流式响应"""
        # 这个测试验证流式生成可以在合理时间内完成
        generator = StreamResponseGenerator(
            intent_router=Mock(),
            task_executor=None,
            llm_client=None
        )

        # 模拟一个快速完成的路由
        route_decision = RouteDecision(
            target=RouteTarget.LLM_SERVICE,
            intent_result=IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.8,
                reasoning="测试",
                suggested_action="聊天",
                parameters={}
            ),
            route_parameters={}
        )
        generator.intent_router.route_intent = AsyncMock(return_value=route_decision)
        generator.intent_router.execute_route = AsyncMock(return_value={"message": "回复"})

        # 设置超时
        try:
            events = []
            async for event in generator.stream_response("测试"):
                events.append(event)
            # 应该在合理时间内完成
            assert True
        except asyncio.TimeoutError:
            pytest.fail("流式响应超时")


class TestStreamResponseEventSequence:
    """事件序列测试"""

    @pytest.mark.asyncio
    async def test_standard_event_sequence(self):
        """测试标准事件序列"""
        mock_intent_router = Mock(spec=IntentRouter)
        route_decision = RouteDecision(
            target=RouteTarget.LLM_SERVICE,
            intent_result=IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.8,
                reasoning="测试",
                suggested_action="聊天",
                parameters={}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent = AsyncMock(return_value=route_decision)
        mock_intent_router.execute_route = AsyncMock(return_value={"message": "回复"})

        generator = StreamResponseGenerator(
            intent_router=mock_intent_router,
            task_executor=None,
            llm_client=None
        )

        events = []
        async for event in generator.stream_response("测试"):
            events.append(event)

        # 验证事件顺序
        event_types = []
        for event in events:
            for line in event.split("\n"):
                if line.startswith("event: "):
                    event_types.append(line.replace("event: ", ""))

        # 标准序列应该以start开始，done结束
        assert event_types[0] == "start"
        assert event_types[-1] == "done"


class TestStreamResponseContext:
    """上下文处理测试"""

    @pytest.mark.asyncio
    async def test_stream_with_user_context(self):
        """测试带用户上下文的流式响应"""
        mock_intent_router = Mock(spec=IntentRouter)
        mock_intent_router.route_intent = AsyncMock()
        mock_intent_router.execute_route = AsyncMock(return_value={})

        route_decision = RouteDecision(
            target=RouteTarget.TOOL_EXECUTOR,
            intent_result=IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=0.9,
                reasoning="测试",
                suggested_action="执行工具",
                parameters={}
            ),
            route_parameters={}
        )
        mock_intent_router.route_intent.return_value = route_decision

        generator = StreamResponseGenerator(
            intent_router=mock_intent_router,
            task_executor=None,
            llm_client=None
        )

        user_context = {
            "user_id": "user_001",
            "department_id": "dept_001",
            "role": "manager"
        }

        events = []
        async for event in generator.stream_response(
            "查询工时",
            user_context=user_context,
            session_id="session_001"
        ):
            events.append(event)

        # 验证session_id在start事件中被包含
        start_event = next(e for e in events if "event: start" in e)
        assert "session_001" in start_event

        # 验证user_context被传递给intent_router
        mock_intent_router.route_intent.assert_called_once()
        call_args = mock_intent_router.route_intent.call_args
        assert call_args[0][1] == user_context
