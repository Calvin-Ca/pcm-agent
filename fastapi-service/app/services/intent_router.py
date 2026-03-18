"""
Intent Router - 意图路由器

负责识别用户意图并路由到相应的处理模块。
采用混合架构：规则匹配（快速、高置信度）+ LLM兜底（模糊情况）
"""

import logging
import os
from typing import Dict, Any, List, Optional, Union, Callable, Awaitable
from enum import Enum
from pydantic import BaseModel, Field
import json
import re
from datetime import datetime

from ..models.task_plan import PlannerAgent, TaskPlan
from .task_executor import TaskExecutor
from .permission_validator import PermissionContext

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """意图类型枚举"""
    KNOWLEDGE_QA = "knowledge_qa"  # 知识问答
    TOOL_EXECUTION = "tool_execution"  # 工具执行
    COMPLEX_REQUEST = "complex_request"  # 复杂请求（需要规划）
    GENERAL_CHAT = "general_chat"  # 通用对话


class RouteTarget(str, Enum):
    """路由目标枚举"""
    RAG_ENGINE = "rag_engine"  # RAG引擎
    TOOL_EXECUTOR = "tool_executor"  # 工具执行器
    PLANNER_AGENT = "planner_agent"  # 规划代理
    LLM_SERVICE = "llm_service"  # LLM服务


class IntentResult(BaseModel):
    """意图识别结果"""
    intent_type: IntentType = Field(..., description="意图类型")
    confidence: float = Field(..., description="置信度 (0-1)")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="提取的参数")
    reasoning: str = Field(..., description="识别推理过程")
    suggested_action: str = Field(..., description="建议的处理方式")


class RouteDecision(BaseModel):
    """路由决策结果"""
    target: RouteTarget = Field(..., description="路由目标")
    intent_result: IntentResult = Field(..., description="意图识别结果")
    route_parameters: Dict[str, Any] = Field(default_factory=dict, description="路由参数")
    fallback_target: Optional[RouteTarget] = Field(None, description="备用路由目标")


class IntentRouter:
    """意图路由器"""
    
    def __init__(self):
        """初始化意图路由器"""
        self.knowledge_keywords = [
            "什么是", "如何", "怎么", "为什么", "规则", "制度", "政策", "流程",
            "说明", "介绍", "解释", "定义", "faq", "常见问题", "帮助",
            "填写", "申请", "方法", "步骤"
        ]

        self.tool_keywords = {
            "query_timesheet": [
                # 组合词（高分）
                "查询工时", "我的工时", "查看工时", "本人工时", "工时记录",
                # 基础词（中分）
                "工时", "工作时间", "加班", "考勤", "打卡", "上班", "下班",
                # 时间限定
                "本周工时", "本月工时"
            ],
            "query_project": [
                # 组合词（高分）
                "查询项目", "项目信息", "项目详情", "项目成员", "项目进度",
                "查看项目", "显示项目",
                # 基础词（中分）
                "项目"
            ],
            "compute_statistics": [
                # 统计词（高分）
                "统计", "汇总", "报表", "分析", "总计", "平均", "对比",
                "统计数据", "数据分析",
                # 组合词
                "统计部门", "统计工时", "统计项目", "部门统计", "项目统计"
            ]
        }

        self.complex_indicators = [
            "并且", "然后", "接着", "同时", "另外", "还要", "以及",
            "生成报告", "制作图表", "发送邮件", "导出数据", "批量处理",
            "并生成", "并统计", "然后", "同时"
        ]

        # 通用对话关键词（用于排除）
        self.chat_keywords = [
            "你好", "您好", "嗨", "hello", "hi",
            "谢谢", "感谢", "多谢",
            "再见", "拜拜", "bye",
            "很高兴", "随便聊聊", "天气"
        ]
        
        # 路由处理器映射
        self.route_handlers: Dict[RouteTarget, Callable] = {}
        
        # 集成组件
        self.planner_agent: Optional[PlannerAgent] = None
        self.task_executor: Optional[TaskExecutor] = None
        self.tool_registry = None
        self.llm_client = None
        
        logger.info("Intent Router initialized")
    
    def set_planner_agent(self, planner_agent: PlannerAgent):
        """设置任务规划代理"""
        self.planner_agent = planner_agent
    
    def set_task_executor(self, task_executor: TaskExecutor):
        """设置任务执行器"""
        self.task_executor = task_executor
    
    def set_tool_registry(self, tool_registry):
        """设置工具注册中心"""
        self.tool_registry = tool_registry
    
    def set_llm_client(self, llm_client):
        """设置LLM客户端"""
        self.llm_client = llm_client
    
    def register_route_handler(
        self,
        target: RouteTarget,
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
    ):
        """注册路由处理器"""
        self.route_handlers[target] = handler
        logger.info(f"Route handler registered for {target}")
    
    async def route_intent(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> IntentResult:
        """
        路由用户意图

        Args:
            user_message: 用户消息
            context: 上下文信息

        Returns:
            IntentResult: 意图识别结果
        """
        try:
            # 预处理消息
            message = user_message.strip().lower()

            # 0. 首先检查通用对话关键词（快速排除）
            chat_result = self._check_chat_intent(message)
            if chat_result:
                return chat_result

            # 0. 特殊模式：以"什么是"开头 → 强知识问答信号
            if re.search(r'^什么是', message):
                return IntentResult(
                    intent_type=IntentType.KNOWLEDGE_QA,
                    confidence=0.85,
                    parameters={"query": message},
                    reasoning="以'什么是'开头，强烈知识问答意图",
                    suggested_action="使用RAG引擎进行知识问答"
                )

            # 1. 检查是否为复杂请求意图（优先于高置信度工具意图）
            complex_result = self._check_complex_intent(message)
            if complex_result:
                return complex_result

            # 2. 检查是否为工具执行意图
            tool_result = self._check_tool_intent(message)

            # 3. 检查是否为知识问答意图
            knowledge_result = self._check_knowledge_intent(message)

            # 4. 决策逻辑
            # 高置信度工具意图优先
            if tool_result and tool_result.confidence >= 0.7:
                # 但如果知识意图置信度更高，选择知识
                if knowledge_result and knowledge_result.confidence > tool_result.confidence:
                    return knowledge_result
                return tool_result

            # 高置信度知识意图
            if knowledge_result and knowledge_result.confidence >= 0.6:
                return knowledge_result

            # 4. 检查是否为复杂请求意图
            complex_result = self._check_complex_intent(message)
            if complex_result:
                return complex_result

            # 5. 低置信度时，取较高者
            if tool_result and knowledge_result:
                if knowledge_result.confidence > tool_result.confidence:
                    return knowledge_result
                return tool_result
            elif tool_result:
                return tool_result
            elif knowledge_result:
                return knowledge_result

            # 4. 如果知识问答有较低置信度，也接受
            if knowledge_result and knowledge_result.confidence >= 0.5:
                return knowledge_result

            # 5. 收集规则匹配结果（用于后续LLM兜底决策）
            rule_results = []
            if knowledge_result:
                rule_results.append(("knowledge_qa", knowledge_result.confidence))
            if tool_result:
                rule_results.append(("tool_execution", tool_result.confidence))
            if complex_result:
                rule_results.append(("complex_request", complex_result.confidence))

            # 6. LLM兜底：如果规则置信度都较低，尝试调用LLM
            if self.llm_client and rule_results and max(r[1] for r in rule_results) < 0.6:
                llm_result = await self._classify_with_llm(message, rule_results)
                if llm_result:
                    return llm_result

            # 7. 使用最佳规则结果（如果有）
            if rule_results:
                best_rule = max(rule_results, key=lambda x: x[1])
                if best_rule[0] == "knowledge_qa" and knowledge_result:
                    return knowledge_result
                elif best_rule[0] == "tool_execution" and tool_result:
                    return tool_result
                elif best_rule[0] == "complex_request" and complex_result:
                    return complex_result

            # 8. 默认为通用对话意图
            return IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.6,
                parameters={},
                reasoning="未匹配到特定意图模式，归类为通用对话",
                suggested_action="使用LLM进行通用对话处理"
            )

        except Exception as e:
            logger.error(f"意图路由异常: {str(e)}")
            return IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.3,
                parameters={},
                reasoning=f"意图识别异常: {str(e)}",
                suggested_action="降级到通用对话处理"
            )

    async def _classify_with_llm(
        self,
        message: str,
        rule_results: List[tuple]
    ) -> Optional[IntentResult]:
        """
        使用LLM进行意图分类（兜底策略）

        适用于规则匹配置信度较低的情况，如：
        - "如何查询工时"（知识还是工具？）
        - "帮我看看项目进度"（较口语化）
        """
        try:
            # 构建Prompt
            rule_info = ", ".join([f"{name}({conf:.2f})" for name, conf in rule_results])

            prompt = f"""你是一个意图分类助手。请分析用户消息，判断其意图类型。

用户消息："{message}"

可选意图类型：
1. knowledge_qa - 用户询问知识、规则、流程（如"什么是工时制度""怎么申请加班"）
2. tool_execution - 用户想执行具体操作（如"查询我的工时""统计部门数据"）
3. complex_request - 复杂请求，包含多个步骤（如"查询工时并生成报表"）
4. general_chat - 通用对话、问候、闲聊

规则引擎初步结果：{rule_info}

请以JSON格式返回：
{{
    "intent_type": "意图类型",
    "confidence": 0.0-1.0,
    "reasoning": "判断理由"
}}

只返回JSON，不要其他内容。"""

            # 调用LLM
            response = await self._call_llm(prompt)

            # 解析JSON响应
            # 提取JSON部分（处理可能的markdown代码块）
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response.strip()

            result = json.loads(json_str)

            intent_type = result.get("intent_type", "general_chat")
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "LLM分类")

            # 验证意图类型
            if intent_type not in [t.value for t in IntentType]:
                intent_type = "general_chat"

            return IntentResult(
                intent_type=IntentType(intent_type),
                confidence=confidence,
                parameters={"llm_classified": True},
                reasoning=f"LLM兜底分类: {reasoning}",
                suggested_action=f"使用规则+LLM混合决策"
            )

        except Exception as e:
            logger.warning(f"LLM意图分类失败: {str(e)}")
            return None

    async def _call_llm(self, prompt: str) -> str:
        """
        调用意图识别专用LLM API（OpenAI兼容格式）

        通过环境变量 INTENT_LLM_* 配置：
        - INTENT_LLM_API_KEY: API密钥
        - INTENT_LLM_API_BASE: API端点（兼容OpenAI格式）
        - INTENT_LLM_MODEL: 模型名称
        """
        import aiohttp

        api_key = os.getenv("INTENT_LLM_API_KEY")
        api_base = os.getenv("INTENT_LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = os.getenv("INTENT_LLM_MODEL", "qwen-flash")

        if not api_key:
            raise ValueError("未配置 INTENT_LLM_API_KEY 环境变量")

        url = f"{api_base.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 200
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    error_text = await resp.text()
                    raise ValueError(f"Intent LLM API错误 ({model}): {resp.status} - {error_text}")

    def _check_chat_intent(self, message: str) -> Optional[IntentResult]:
        """检查通用对话意图（快速排除）"""
        # 检查纯问候语
        chat_patterns = [
            r'^(你好|您好|嗨|hello|hi|嘿|在吗|在？|有人吗)',
            r'^(谢谢|感谢|多谢|谢了)',
            r'^(再见|拜拜|bye|byebye)',
            r'^(很高兴|随便聊聊|今天天气|聊聊|说说话)',
        ]

        for pattern in chat_patterns:
            if re.search(pattern, message):
                return IntentResult(
                    intent_type=IntentType.GENERAL_CHAT,
                    confidence=0.7,
                    parameters={},
                    reasoning="匹配到通用对话模式",
                    suggested_action="使用LLM进行通用对话处理"
                )

        # 空消息或纯标点
        if not message or re.match(r'^[\s\W]+$', message):
            return IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.5,
                parameters={},
                reasoning="空消息或无效输入",
                suggested_action="使用LLM进行通用对话处理"
            )

        return None
    
    async def make_route_decision(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> RouteDecision:
        """
        做出路由决策
        
        Args:
            user_message: 用户消息
            context: 上下文信息
            
        Returns:
            RouteDecision: 路由决策结果
        """
        try:
            # 首先进行意图识别
            intent_result = await self.route_intent(user_message, context)
            
            # 根据意图类型决定路由目标
            if intent_result.intent_type == IntentType.KNOWLEDGE_QA:
                return self._route_to_rag_engine(intent_result, context)
            
            elif intent_result.intent_type == IntentType.TOOL_EXECUTION:
                return self._route_to_tool_executor(intent_result, context)
            
            elif intent_result.intent_type == IntentType.COMPLEX_REQUEST:
                return self._route_to_planner_agent(intent_result, context)
            
            else:  # GENERAL_CHAT
                return self._route_to_llm_service(intent_result, context, user_message)
        
        except Exception as e:
            logger.error(f"路由决策异常: {str(e)}")
            # 异常情况下降级到LLM服务
            fallback_intent = IntentResult(
                intent_type=IntentType.GENERAL_CHAT,
                confidence=0.3,
                parameters={},
                reasoning=f"路由决策异常，降级处理: {str(e)}",
                suggested_action="使用LLM服务处理"
            )
            return self._route_to_llm_service(fallback_intent, context, user_message)
    
    def _route_to_rag_engine(
        self,
        intent_result: IntentResult,
        context: Optional[Dict[str, Any]]
    ) -> RouteDecision:
        """路由到RAG引擎"""
        route_params = {
            "query": intent_result.parameters.get("query", ""),
            "matched_keywords": intent_result.parameters.get("matched_keywords", []),
            "search_type": "knowledge_base",
            "max_results": 5
        }
        
        return RouteDecision(
            target=RouteTarget.RAG_ENGINE,
            intent_result=intent_result,
            route_parameters=route_params,
            fallback_target=RouteTarget.LLM_SERVICE
        )
    
    def _route_to_tool_executor(
        self,
        intent_result: IntentResult,
        context: Optional[Dict[str, Any]]
    ) -> RouteDecision:
        """路由到工具执行器"""
        route_params = {
            "tool_name": intent_result.parameters.get("tool_name"),
            "tool_parameters": {
                k: v for k, v in intent_result.parameters.items()
                if k not in ["tool_name", "matched_keywords"]
            },
            "permission_context": context.get("permission_context") if context else None
        }
        
        return RouteDecision(
            target=RouteTarget.TOOL_EXECUTOR,
            intent_result=intent_result,
            route_parameters=route_params,
            fallback_target=RouteTarget.LLM_SERVICE
        )
    
    def _route_to_planner_agent(
        self,
        intent_result: IntentResult,
        context: Optional[Dict[str, Any]]
    ) -> RouteDecision:
        """路由到规划代理"""
        route_params = {
            "complex_request": intent_result.parameters.get("message", ""),
            "matched_indicators": intent_result.parameters.get("matched_indicators", []),
            "action_count": intent_result.parameters.get("action_count", 0),
            "planning_mode": "reactive",  # 使用ReAct框架
            "permission_context": context.get("permission_context") if context else None
        }
        
        return RouteDecision(
            target=RouteTarget.PLANNER_AGENT,
            intent_result=intent_result,
            route_parameters=route_params,
            fallback_target=RouteTarget.LLM_SERVICE
        )
    
    def _route_to_llm_service(
        self,
        intent_result: IntentResult,
        context: Optional[Dict[str, Any]],
        user_message: str = ""
    ) -> RouteDecision:
        """路由到LLM服务"""
        route_params = {
            "message": user_message or intent_result.parameters.get("message", ""),
            "conversation_type": "general",
            "context": context or {},
            "temperature": 0.7,
            "max_tokens": 1000
        }
        
        return RouteDecision(
            target=RouteTarget.LLM_SERVICE,
            intent_result=intent_result,
            route_parameters=route_params,
            fallback_target=None  # LLM服务是最后的备选
        )
    
    async def execute_route(
        self,
        route_decision: RouteDecision,
        user_context: Dict[str, Any] = None
    ) -> Any:
        """
        执行路由决策
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Returns:
            Any: 执行结果
        """
        try:
            # 特殊处理PLANNER_AGENT路由
            if route_decision.target == RouteTarget.PLANNER_AGENT:
                return await self._execute_planner_agent(route_decision, user_context)
            
            # 获取目标处理器
            handler = self.route_handlers.get(route_decision.target)
            
            if not handler:
                logger.warning(f"未找到路由处理器: {route_decision.target}")
                # 尝试备用路由
                if route_decision.fallback_target:
                    fallback_handler = self.route_handlers.get(route_decision.fallback_target)
                    if fallback_handler:
                        logger.info(f"使用备用路由: {route_decision.fallback_target}")
                        return await fallback_handler(route_decision.route_parameters)
                
                # 返回错误结果
                return {
                    "success": False,
                    "error": f"未找到可用的路由处理器: {route_decision.target}",
                    "route_target": route_decision.target
                }
            
            # 执行路由处理器
            result = await handler(route_decision.route_parameters)
            
            # 添加路由信息到结果中
            if isinstance(result, dict):
                result["route_info"] = {
                    "target": route_decision.target,
                    "intent_type": route_decision.intent_result.intent_type,
                    "confidence": route_decision.intent_result.confidence
                }
            
            return result
            
        except Exception as e:
            logger.error(f"路由执行异常: {str(e)}")
            
            # 尝试备用路由
            if route_decision.fallback_target:
                try:
                    fallback_handler = self.route_handlers.get(route_decision.fallback_target)
                    if fallback_handler:
                        logger.info(f"执行异常，使用备用路由: {route_decision.fallback_target}")
                        result = await fallback_handler(route_decision.route_parameters)
                        if isinstance(result, dict):
                            result["route_info"] = {
                                "target": route_decision.fallback_target,
                                "original_target": route_decision.target,
                                "fallback_reason": str(e)
                            }
                        return result
                except Exception as fallback_error:
                    logger.error(f"备用路由也执行失败: {str(fallback_error)}")
            
            return {
                "success": False,
                "error": f"路由执行失败: {str(e)}",
                "route_target": route_decision.target
            }
    
    async def _execute_planner_agent(
        self,
        route_decision: RouteDecision,
        user_context: Dict[str, Any] = None
    ) -> TaskPlan:
        """
        执行任务规划代理
        
        Args:
            route_decision: 路由决策
            user_context: 用户上下文
            
        Returns:
            TaskPlan: 生成的任务计划
        """
        if not self.planner_agent:
            raise ValueError("任务规划代理未配置")
        
        # 提取用户请求
        user_request = route_decision.route_parameters.get("complex_request", "")
        permission_context = route_decision.route_parameters.get("permission_context")
        
        # 获取可用工具列表
        available_tools = []
        if self.tool_registry:
            available_tools = self.tool_registry.list_tools()
        
        # 构建用户上下文
        planner_context = {}
        if user_context:
            planner_context.update(user_context)
        if permission_context:
            planner_context["permission_context"] = permission_context
        
        # 生成任务计划
        logger.info(f"开始任务规划: {user_request}")
        task_plan = await self.planner_agent.plan_tasks(
            user_request=user_request,
            user_context=planner_context,
            available_tools=available_tools
        )
        
        logger.info(f"任务规划完成: {task_plan.name}, 包含 {len(task_plan.tasks)} 个任务")
        return task_plan
    
    def _check_tool_intent(self, message: str) -> Optional[IntentResult]:
        """检查工具执行意图 - 按优先级顺序检查"""

        # 优先级1: query_timesheet (工时查询)
        ts_score = 0.0
        ts_matched = []
        for keyword in self.tool_keywords["query_timesheet"]:
            if keyword in message:
                ts_score += 1.0
                ts_matched.append(keyword)
        # 强信号：查询/查看/显示 + 工时/加班/记录
        if ("查询" in message or "查看" in message or "显示" in message) and ("工时" in message or "加班" in message or "记录" in message):
            ts_score += 3.5
        # 强信号：我的 + 工时
        if "我的" in message and "工时" in message:
            ts_score += 2.0
        # 中信号：单独的"工时"词
        if "工时" in message:
            ts_score += 1.5
        # 弱信号：查看记录
        if "查看" in message and "记录" in message:
            ts_score += 2.0

        # 优先级2: compute_statistics (统计相关)
        cs_score = 0.0
        cs_matched = []
        for keyword in self.tool_keywords["compute_statistics"]:
            if keyword in message:
                cs_score += 1.0
                cs_matched.append(keyword)
        # 强信号：统计部门/统计项目
        if "统计" in message and ("部门" in message or "项目" in message):
            cs_score += 4.0
        # 中信号：统计 + 工时
        if "统计" in message and "工时" in message:
            cs_score += 2.5
        # 强信号：计算平均/汇总
        if "计算" in message and ("平均" in message or "汇总" in message):
            cs_score += 3.0
        # 弱信号：单独的"统计"
        if "统计" in message:
            cs_score += 0.5

        # 优先级3: query_project
        qp_score = 0.0
        qp_matched = []
        for keyword in self.tool_keywords["query_project"]:
            if keyword in message:
                qp_score += 1.0
                qp_matched.append(keyword)
        if ("查询" in message or "查看" in message) and "项目" in message:
            qp_score += 2.5
        if "项目进度" in message or "项目成员" in message or "项目状态" in message:
            qp_score += 2.0

        # 选择最佳匹配
        scores = [
            ("query_timesheet", ts_score, ts_matched),
            ("compute_statistics", cs_score, cs_matched),
            ("query_project", qp_score, qp_matched),
        ]

        best_tool, best_score, best_match = max(scores, key=lambda x: x[1])

        # 如果得分足够高，认为是工具执行意图
        if best_score >= 0.5:
            parameters = self._extract_tool_parameters(message, best_tool)

            return IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=min(0.9, 0.5 + best_score * 0.1),
                parameters={
                    "tool_name": best_tool,
                    "matched_keywords": best_match,
                    **parameters
                },
                reasoning=f"匹配到工具关键词: {', '.join(best_match)}",
                suggested_action=f"执行工具: {best_tool}"
            )

        return None
    
    def _check_knowledge_intent(self, message: str) -> Optional[IntentResult]:
        """检查知识问答意图"""
        score = 0.0
        matched_keywords = []

        for keyword in self.knowledge_keywords:
            if keyword in message:
                score += 1.0
                matched_keywords.append(keyword)

        # 检查问句模式（加权更高）
        question_patterns = [
            (r'^什么是', 1.5),  # "什么是" 是强烈的知识问答信号
            (r'.*\?$', 0.8),  # 以问号结尾
            (r'^(什么|如何|怎么|为什么|哪里|哪个|谁|何时)', 0.8),  # 疑问词开头
            (r'(是什么|怎么办|如何做|为什么要)', 0.8),  # 常见问句模式
        ]

        for pattern, weight in question_patterns:
            if re.search(pattern, message):
                score += weight
                break

        # 检查是否同时包含强烈的工具关键词 - 如果包含，优先认为是工具操作而非知识问答
        strong_tool_indicators = ["工时", "项目", "加班", "统计"]
        has_tool_indicator = any(ind in message for ind in strong_tool_indicators)

        # 强知识问句模式判断
        is_what_question = re.search(r'^什么是', message) is not None
        is_why_question = re.search(r'(为什么|制度|规则|流程|介绍)', message) is not None
        is_strong_knowledge_question = is_what_question or is_why_question

        # "什么是XX" 是极强的知识问答信号（已在入口处处理，这里不需要重复）
        if is_what_question:
            score += 2.0  # 大幅提升分数

        # "怎么申请""如何查询"等操作类问句 → 应识别为工具意图而非知识问答
        is_how_to_action = re.search(r'(怎么|如何).*(申请|查询|查看|打卡|统计)', message) is not None
        # "如何填写""怎么填写" → 知识问答（填写方法）
        is_how_to_fill = re.search(r'(怎么|如何).*填写', message) is not None
        if is_how_to_action and not is_how_to_fill:
            score -= 1.5  # 大幅降低知识问答分数
        if is_how_to_fill:
            score += 1.0  # 提升知识问答分数

        # 如果包含工具关键词但不是强知识问句，降低知识问答置信度
        if has_tool_indicator and not is_strong_knowledge_question:
            score -= 0.5

        if is_strong_knowledge_question:
            score += 1.0

        # 调整阈值：强知识问句阈值低，普通问句阈值高，操作类问句阈值更高
        if is_how_to_action:
            threshold = 1.5  # 操作类问句需要更高分数才识别为知识问答
        elif is_strong_knowledge_question:
            threshold = 0.3
        else:
            threshold = 0.5
        if score >= threshold or (is_strong_knowledge_question and not is_how_to_action):
            return IntentResult(
                intent_type=IntentType.KNOWLEDGE_QA,
                confidence=min(0.9, 0.5 + score * 0.1),
                parameters={
                    "matched_keywords": matched_keywords,
                    "query": message
                },
                reasoning=f"匹配到知识问答关键词: {', '.join(matched_keywords) if matched_keywords else '疑问词模式'}",
                suggested_action="使用RAG引擎进行知识问答"
            )

        return None
    
    def _check_complex_intent(self, message: str) -> Optional[IntentResult]:
        """检查复杂请求意图"""
        score = 0.0
        matched_indicators = []

        # 检查复杂性指标
        for indicator in self.complex_indicators:
            if indicator in message:
                score += 1.0
                matched_indicators.append(indicator)

        # 检查多个动作词
        action_words = ["查询", "统计", "生成", "导出", "发送", "计算", "分析", "对比"]
        action_count = sum(1 for word in action_words if word in message)

        if action_count >= 2:
            score += action_count * 0.5

        # 检查长度和复杂度
        if len(message) > 15:  # 降低长度阈值，更容易识别复杂请求
            score += 0.3

        # 降低阈值，更容易识别复杂请求
        if score >= 0.5:
            return IntentResult(
                intent_type=IntentType.COMPLEX_REQUEST,
                confidence=min(0.9, 0.3 + score * 0.1),
                parameters={
                    "matched_indicators": matched_indicators,
                    "action_count": action_count,
                    "message_length": len(message)
                },
                reasoning=f"检测到复杂请求指标: {', '.join(matched_indicators) if matched_indicators else '多动作组合'}",
                suggested_action="使用Planner Agent进行任务规划"
            )

        return None
    
    def _extract_tool_parameters(self, message: str, tool_name: str) -> Dict[str, Any]:
        """提取工具参数"""
        parameters = {}
        
        try:
            if tool_name == "query_timesheet":
                # 提取时间范围
                time_patterns = {
                    "今天": {"start_date": "today", "end_date": "today"},
                    "昨天": {"start_date": "yesterday", "end_date": "yesterday"},
                    "本周": {"start_date": "this_week_start", "end_date": "this_week_end"},
                    "上周": {"start_date": "last_week_start", "end_date": "last_week_end"},
                    "本月": {"start_date": "this_month_start", "end_date": "this_month_end"},
                    "上月": {"start_date": "last_month_start", "end_date": "last_month_end"}
                }
                
                for pattern, dates in time_patterns.items():
                    if pattern in message:
                        parameters.update(dates)
                        break
                
                # 提取用户相关信息
                if "我的" in message or "自己的" in message:
                    parameters["target_self"] = True
            
            elif tool_name == "query_project":
                # 提取项目相关信息
                project_patterns = [
                    r'项目(\w+)',
                    r'(\w+)项目',
                ]
                
                for pattern in project_patterns:
                    match = re.search(pattern, message)
                    if match:
                        parameters["project_name"] = match.group(1)
                        break
            
            elif tool_name == "compute_statistics":
                # 提取统计类型
                stat_patterns = {
                    "用户工时": "user_hours",
                    "项目工时": "project_hours",
                    "部门工时": "department_hours",
                    "每日工时": "daily_hours",
                    "每周工时": "weekly_hours",
                    "每月工时": "monthly_hours"
                }
                
                for pattern, stat_type in stat_patterns.items():
                    if pattern in message:
                        parameters["statistics_type"] = stat_type
                        break
        
        except Exception as e:
            logger.warning(f"参数提取异常: {str(e)}")
        
        return parameters
    
    def get_intent_prompt_template(self, intent_type: IntentType) -> str:
        """获取意图对应的Prompt模板"""
        templates = {
            IntentType.KNOWLEDGE_QA: """
你是一个专业的企业助手，请基于提供的知识库内容回答用户问题。

用户问题: {user_message}

相关知识: {knowledge_context}

请提供准确、详细的回答，如果知识库中没有相关信息，请明确说明。
""",
            
            IntentType.TOOL_EXECUTION: """
用户请求执行工具操作。

用户消息: {user_message}
工具名称: {tool_name}
提取参数: {parameters}

请确认参数是否正确，如有缺失请询问用户补充。
""",
            
            IntentType.COMPLEX_REQUEST: """
用户提出了复杂请求，需要分解为多个步骤。

用户请求: {user_message}

请分析请求，制定执行计划，包括：
1. 需要执行的具体步骤
2. 每个步骤使用的工具
3. 步骤之间的依赖关系
""",
            
            IntentType.GENERAL_CHAT: """
用户进行一般对话。

用户消息: {user_message}

请提供友好、专业的回复，如果涉及工时管理相关问题，可以引导用户使用相关功能。
"""
        }
        
        return templates.get(intent_type, templates[IntentType.GENERAL_CHAT])


# 全局意图路由器实例
intent_router = IntentRouter()