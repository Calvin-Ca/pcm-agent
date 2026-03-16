"""
Intent Router单元测试

测试意图路由器的各项功能，包括：
- 意图识别准确性（知识问答、工具执行、复杂请求、通用对话）
- 路由决策正确性
- 参数提取
- 异常处理
"""

import pytest
from unittest.mock import Mock, AsyncMock

from app.services.intent_router import (
    IntentRouter,
    IntentType,
    RouteTarget,
    IntentResult,
    RouteDecision
)


class TestIntentRouterInitialization:
    """意图路由器初始化测试"""

    def test_intent_router_initialization(self):
        """测试意图路由器初始化"""
        router = IntentRouter()

        assert router.knowledge_keywords is not None
        assert len(router.knowledge_keywords) > 0
        assert router.tool_keywords is not None
        assert len(router.tool_keywords) > 0
        assert router.complex_indicators is not None
        assert len(router.complex_indicators) > 0
        assert router.route_handlers == {}

    def test_intent_router_component_setters(self):
        """测试组件设置器"""
        router = IntentRouter()

        mock_planner = Mock()
        mock_executor = Mock()
        mock_registry = Mock()
        mock_llm = Mock()

        router.set_planner_agent(mock_planner)
        router.set_task_executor(mock_executor)
        router.set_tool_registry(mock_registry)
        router.set_llm_client(mock_llm)

        assert router.planner_agent is mock_planner
        assert router.task_executor is mock_executor
        assert router.tool_registry is mock_registry
        assert router.llm_client is mock_llm


class TestToolIntentRecognition:
    """工具执行意图识别测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_recognize_query_timesheet_intent(self, router):
        """测试识别工时查询意图"""
        messages = [
            "查询我本周的工时",
            "查看我的工时记录",
            "显示本月工时统计",
            "我的加班时间",
            "查询工作时间"
        ]

        for message in messages:
            result = await router.route_intent(message)
            # 这些消息应该被识别为工具执行意图
            assert result.intent_type == IntentType.TOOL_EXECUTION, f"消息'{message}'应该被识别为工具执行意图"
            assert result.parameters.get("tool_name") == "query_timesheet"
            assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_recognize_query_project_intent(self, router):
        """测试识别项目查询意图"""
        messages = [
            "查询项目信息",
            "查看项目成员",
            "项目进度如何",
            "显示项目详情",
            "项目管理"
        ]

        for message in messages:
            result = await router.route_intent(message)
            assert result.intent_type == IntentType.TOOL_EXECUTION, f"消息'{message}'应该被识别为工具执行意图"
            assert result.parameters.get("tool_name") == "query_project"

    @pytest.mark.asyncio
    async def test_recognize_compute_statistics_intent(self, router):
        """测试识别统计意图"""
        messages = [
            "统计部门工时",
            "汇总本周数据",
            "计算平均工时",
        ]

        for message in messages:
            result = await router.route_intent(message)
            assert result.intent_type == IntentType.TOOL_EXECUTION, f"消息'{message}'应该被识别为工具执行意图"
            assert result.parameters.get("tool_name") == "compute_statistics"

    @pytest.mark.asyncio
    async def test_tool_intent_with_time_extraction(self, router):
        """测试工具意图的时间参数提取"""
        test_cases = [
            ("查询今天的工时", "today"),
            ("查看本周工时记录", "this_week_start"),
            ("显示本月统计", "this_month_start"),
            ("查询上周工时", "last_week_start"),
        ]

        for message, expected_date in test_cases:
            result = await router.route_intent(message)
            if result.intent_type == IntentType.TOOL_EXECUTION:
                params = result.parameters
                # 验证至少提取到了一些参数
                assert len(params) > 0


class TestKnowledgeIntentRecognition:
    """知识问答意图识别测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_recognize_knowledge_qa_intent(self, router):
        """测试识别知识问答意图"""
        messages = [
            "什么是工时管理制度？",
            "如何填写工时？",
            "加班规则是什么",
            "为什么需要审批？",
            "请假流程说明",
            "企业制度介绍",
            "帮助文档"
        ]

        for message in messages:
            result = await router.route_intent(message)
            assert result.intent_type == IntentType.KNOWLEDGE_QA, f"消息'{message}'应该被识别为知识问答意图"
            assert result.confidence > 0.4

    @pytest.mark.asyncio
    async def test_question_pattern_recognition(self, router):
        """测试问句模式识别"""
        # 知识类问句应该识别为知识问答
        knowledge_questions = [
            "什么是项目管理？",
            "如何提高效率？",
            "为什么要打卡？",
        ]

        for question in knowledge_questions:
            result = await router.route_intent(question)
            assert result.intent_type == IntentType.KNOWLEDGE_QA, f"问题'{question}'应该被识别为知识问答"
            assert result.confidence > 0.4

        # 工具相关问句应该识别为工具执行
        tool_questions = [
            "怎么申请加班？",
            "哪里查看记录？"
        ]

        for question in tool_questions:
            result = await router.route_intent(question)
            assert result.intent_type == IntentType.TOOL_EXECUTION, f"问题'{question}'应该被识别为工具执行"
            assert result.confidence > 0.5


class TestComplexIntentRecognition:
    """复杂请求意图识别测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_recognize_complex_request_intent(self, router):
        """测试识别复杂请求意图"""
        messages = [
            "查询本周工时并生成报表",
            "统计部门数据然后发送邮件",
            "分析项目进度同时查看成员工时",
            "导出数据并制作图表",
            "查询我的工时并且统计项目数据"
        ]

        for message in messages:
            result = await router.route_intent(message)
            # 复杂请求应该被识别为复杂请求或工具执行
            assert result.intent_type in [IntentType.COMPLEX_REQUEST, IntentType.TOOL_EXECUTION]

    @pytest.mark.asyncio
    async def test_complex_indicators_detection(self, router):
        """测试复杂指标检测"""
        messages_with_indicators = [
            "查询工时并且统计分析",
            "查看项目然后导出数据",
            "生成报表同时发送邮件"
        ]

        for message in messages_with_indicators:
            result = await router.route_intent(message)
            # 包含复杂性指标的消息应该是复杂请求或工具执行
            assert result.intent_type in [IntentType.COMPLEX_REQUEST, IntentType.TOOL_EXECUTION]


class TestGeneralChatRecognition:
    """通用对话意图识别测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_recognize_general_chat_intent(self, router):
        """测试识别通用对话意图"""
        messages = [
            "你好",
            "谢谢",
            "再见",
            "很高兴见到你",
            "随便聊聊",
            "今天天气不错"
        ]

        for message in messages:
            result = await router.route_intent(message)
            assert result.intent_type == IntentType.GENERAL_CHAT, f"消息'{message}'应该被识别为通用对话"

    @pytest.mark.asyncio
    async def test_empty_and_garbage_input(self, router):
        """测试空消息和垃圾输入"""
        messages = [
            "",
            "   ",
            "asdfgh",
            "123456",
            "!!!"
        ]

        for message in messages:
            result = await router.route_intent(message)
            # 这些输入应该被归类为通用对话或返回错误处理结果
            assert result.intent_type == IntentType.GENERAL_CHAT


class TestRouteDecision:
    """路由决策测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_route_to_rag_engine(self, router):
        """测试路由到RAG引擎"""
        result = await router.make_route_decision("什么是工时管理制度？")

        assert result.target == RouteTarget.RAG_ENGINE
        assert result.intent_result.intent_type == IntentType.KNOWLEDGE_QA
        assert result.fallback_target == RouteTarget.LLM_SERVICE
        assert "query" in result.route_parameters

    @pytest.mark.asyncio
    async def test_route_to_tool_executor(self, router):
        """测试路由到工具执行器"""
        result = await router.make_route_decision("查询我本周的工时")

        assert result.target == RouteTarget.TOOL_EXECUTOR
        assert result.intent_result.intent_type == IntentType.TOOL_EXECUTION
        assert result.fallback_target == RouteTarget.LLM_SERVICE

    @pytest.mark.asyncio
    async def test_route_to_planner_agent(self, router):
        """测试路由到规划代理"""
        result = await router.make_route_decision("查询我的工时并生成统计报表")

        assert result.target == RouteTarget.PLANNER_AGENT
        assert result.intent_result.intent_type == IntentType.COMPLEX_REQUEST
        assert result.fallback_target == RouteTarget.LLM_SERVICE

    @pytest.mark.asyncio
    async def test_route_to_llm_service(self, router):
        """测试路由到LLM服务"""
        result = await router.make_route_decision("你好，请介绍一下自己")

        assert result.target == RouteTarget.LLM_SERVICE
        assert result.intent_result.intent_type == IntentType.GENERAL_CHAT
        assert result.fallback_target is None


class TestRouteExecution:
    """路由执行测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_execute_registered_handler(self, router):
        """测试执行已注册的路由处理器"""
        async def mock_handler(params):
            return {"success": True, "data": "test"}

        router.register_route_handler(RouteTarget.LLM_SERVICE, mock_handler)

        route_decision = RouteDecision(
            target=RouteTarget.LLM_SERVICE,
            intent_result=IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.8,
                reasoning="test",
                suggested_action="test"
            ),
            route_parameters={"message": "hello"}
        )

        result = await router.execute_route(route_decision)

        assert result["success"] is True
        assert "route_info" in result

    @pytest.mark.asyncio
    async def test_execute_with_fallback(self, router):
        """测试执行失败时使用备用路由"""
        async def main_handler(params):
            raise Exception("Main handler failed")

        async def fallback_handler(params):
            return {"success": True, "data": "fallback"}

        router.register_route_handler(RouteTarget.RAG_ENGINE, main_handler)
        router.register_route_handler(RouteTarget.LLM_SERVICE, fallback_handler)

        route_decision = RouteDecision(
            target=RouteTarget.RAG_ENGINE,
            intent_result=IntentResult(
                intent_type=IntentType.KNOWLEDGE_QA,
                confidence=0.8,
                reasoning="test",
                suggested_action="test"
            ),
            route_parameters={"query": "test"},
            fallback_target=RouteTarget.LLM_SERVICE
        )

        result = await router.execute_route(route_decision)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_no_handler(self, router):
        """测试没有处理器的情况"""
        route_decision = RouteDecision(
            target=RouteTarget.TOOL_EXECUTOR,
            intent_result=IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=0.8,
                reasoning="test",
                suggested_action="test"
            ),
            route_parameters={}
        )

        result = await router.execute_route(route_decision)

        assert result["success"] is False
        assert "error" in result


class TestParameterExtraction:
    """参数提取测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    def test_extract_timesheet_time_parameters(self, router):
        """测试提取工时查询时间参数"""
        test_cases = [
            ("查询今天的工时", "today"),
            ("查看昨天的工作时间", "yesterday"),
            ("显示本周工时", "this_week_start"),
            ("查询我的工时", "target_self"),
        ]

        for message, expected_param in test_cases:
            params = router._extract_tool_parameters(message, "query_timesheet")
            # 验证至少有一个参数被提取
            assert len(params) > 0, f"消息'{message}'应该提取到参数"

    def test_extract_statistics_parameters(self, router):
        """测试提取统计参数"""
        test_cases = [
            ("统计用户工时", "user_hours"),
            ("项目工时统计", "project_hours"),
            ("部门工时汇总", "department_hours"),
            ("每日工时统计", "daily_hours"),
        ]

        for message, expected_type in test_cases:
            params = router._extract_tool_parameters(message, "compute_statistics")
            if "statistics_type" in params:
                assert params["statistics_type"] == expected_type


class TestErrorHandling:
    """错误处理测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_route_intent_exception_handling(self, router):
        """测试意图路由异常处理"""
        # 测试各种输入不会导致异常
        inputs = ["", "   ", "正常消息"]

        for input_msg in inputs:
            try:
                result = await router.route_intent(input_msg)
                assert isinstance(result, IntentResult)
                assert result.intent_type is not None
            except Exception as e:
                pytest.fail(f"处理输入'{input_msg}'时不应抛出异常: {e}")

    @pytest.mark.asyncio
    async def test_make_route_decision_exception_handling(self, router):
        """测试路由决策异常处理"""
        result = await router.make_route_decision("")  # 空消息

        assert isinstance(result, RouteDecision)
        assert result.target is not None

    def test_get_intent_prompt_template(self, router):
        """测试获取Prompt模板"""
        templates_to_test = [
            IntentType.KNOWLEDGE_QA,
            IntentType.TOOL_EXECUTION,
            IntentType.COMPLEX_REQUEST,
            IntentType.GENERAL_CHAT
        ]

        for intent_type in templates_to_test:
            template = router.get_intent_prompt_template(intent_type)
            assert template is not None
            assert len(template) > 0


class TestIntentConfidence:
    """意图置信度测试"""

    @pytest.fixture
    def router(self):
        return IntentRouter()

    @pytest.mark.asyncio
    async def test_tool_intent_confidence_range(self, router):
        """测试工具意图置信度范围"""
        result = await router.route_intent("查询我本周的工时")

        if result.intent_type == IntentType.TOOL_EXECUTION:
            assert 0 <= result.confidence <= 1
            assert result.confidence > 0.5  # 工具意图应该有较高的置信度

    @pytest.mark.asyncio
    async def test_knowledge_intent_confidence_range(self, router):
        """测试知识问答意图置信度范围"""
        result = await router.route_intent("什么是工时管理制度？")

        if result.intent_type == IntentType.KNOWLEDGE_QA:
            assert 0 <= result.confidence <= 1
            assert result.confidence > 0.4

    @pytest.mark.asyncio
    async def test_general_chat_low_confidence(self, router):
        """测试通用对话的低置信度"""
        result = await router.route_intent("随便说点什么")

        assert result.intent_type == IntentType.GENERAL_CHAT
        # 通用对话的置信度应该较低（因为没有匹配到特定模式）
        assert result.confidence <= 0.7
