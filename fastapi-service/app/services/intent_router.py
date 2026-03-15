"""
Intent Router - 意图路由器

负责识别用户意图并路由到相应的处理模块。
"""

import logging
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
            "说明", "介绍", "解释", "定义", "FAQ", "常见问题", "帮助"
        ]
        
        self.tool_keywords = {
            "query_timesheet": [
                "工时", "工作时间", "加班", "考勤", "打卡", "上班", "下班",
                "查询工时", "工时记录", "工时统计", "本周工时", "本月工时"
            ],
            "query_project": [
                "项目", "项目信息", "项目详情", "项目成员", "项目进度",
                "查询项目", "项目状态", "项目管理"
            ],
            "compute_statistics": [
                "统计", "汇总", "报表", "分析", "总计", "平均", "对比",
                "统计数据", "数据分析", "工时统计", "项目统计", "部门统计"
            ]
        }
        
        self.complex_indicators = [
            "并且", "然后", "接着", "同时", "另外", "还要", "以及",
            "生成报告", "制作图表", "发送邮件", "导出数据", "批量处理"
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
            
            # 1. 检查是否为工具执行意图
            tool_result = self._check_tool_intent(message)
            if tool_result:
                return tool_result
            
            # 2. 检查是否为知识问答意图
            knowledge_result = self._check_knowledge_intent(message)
            if knowledge_result:
                return knowledge_result
            
            # 3. 检查是否为复杂请求意图
            complex_result = self._check_complex_intent(message)
            if complex_result:
                return complex_result
            
            # 4. 默认为通用对话意图
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
                return self._route_to_llm_service(intent_result, context)
        
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
            return self._route_to_llm_service(fallback_intent, context)
    
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
        context: Optional[Dict[str, Any]]
    ) -> RouteDecision:
        """路由到LLM服务"""
        route_params = {
            "message": intent_result.parameters.get("message", ""),
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
        """检查工具执行意图"""
        best_match = None
        best_score = 0.0
        best_tool = None
        
        for tool_name, keywords in self.tool_keywords.items():
            score = 0.0
            matched_keywords = []
            
            for keyword in keywords:
                if keyword in message:
                    score += 1.0
                    matched_keywords.append(keyword)
            
            # 计算相对得分
            if len(keywords) > 0:
                relative_score = score / len(keywords)
                if relative_score > best_score:
                    best_score = relative_score
                    best_tool = tool_name
                    best_match = matched_keywords
        
        # 如果得分足够高，认为是工具执行意图
        if best_score > 0.1:  # 至少匹配10%的关键词
            parameters = self._extract_tool_parameters(message, best_tool)
            
            return IntentResult(
                intent_type=IntentType.TOOL_EXECUTION,
                confidence=min(0.9, 0.5 + best_score),
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
        
        # 检查问句模式
        question_patterns = [
            r'.*\?$',  # 以问号结尾
            r'^(什么|如何|怎么|为什么|哪里|哪个|谁|何时)',  # 疑问词开头
            r'(是什么|怎么办|如何做|为什么要)',  # 常见问句模式
        ]
        
        for pattern in question_patterns:
            if re.search(pattern, message):
                score += 0.5
                break
        
        if score > 0.5:
            return IntentResult(
                intent_type=IntentType.KNOWLEDGE_QA,
                confidence=min(0.9, 0.4 + score * 0.1),
                parameters={
                    "matched_keywords": matched_keywords,
                    "query": message
                },
                reasoning=f"匹配到知识问答关键词: {', '.join(matched_keywords)}",
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
        if len(message) > 50:  # 较长的消息可能更复杂
            score += 0.3
        
        if score > 1.0:
            return IntentResult(
                intent_type=IntentType.COMPLEX_REQUEST,
                confidence=min(0.9, 0.3 + score * 0.1),
                parameters={
                    "matched_indicators": matched_indicators,
                    "action_count": action_count,
                    "message_length": len(message)
                },
                reasoning=f"检测到复杂请求指标: {', '.join(matched_indicators)}",
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