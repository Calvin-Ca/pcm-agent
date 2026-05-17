"""
会话记录数据模型
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, Float, Boolean, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class ConversationLogEntry(BaseModel):
    """会话日志写入参数封装（技术债 #7）

    替代 ``log_conversation()`` 的 15+ 个位置/关键字参数。字段名、默认值
    与原函数签名严格一一对应，保证等价重构（不改变写库内容）。
    ``tool_count`` / ``total_tokens`` 仍由 logger 在写库时派生，不在此模型中。
    """

    model_config = ConfigDict(extra="forbid")

    # 必填
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    user_message: str = Field(..., description="用户消息")
    route_type: str = Field(..., description="路由类型: LLM_SERVICE/TOOL_CALL/TASK_PLANNING")

    # 可选（默认值须与原 log_conversation 签名一致）
    ai_response: Optional[str] = Field(None, description="AI响应内容")
    intent: Optional[str] = Field(None, description="识别的意图")
    history_turns_count: int = Field(0, description="注入的历史对话轮次数")
    memory_count: int = Field(0, description="注入的长期记忆条数")
    context_snapshot: Optional[Dict[str, Any]] = Field(None, description="上下文快照")
    tools_called: Optional[List[Dict[str, Any]]] = Field(None, description="调用的工具列表")
    has_task_plan: bool = Field(False, description="是否进行了任务规划")
    task_plan: Optional[Dict[str, Any]] = Field(None, description="任务规划详情")
    duration_ms: Optional[int] = Field(None, description="处理耗时(毫秒)")
    prompt_tokens: int = Field(0, description="输入token数")
    completion_tokens: int = Field(0, description="输出token数")
    model_name: Optional[str] = Field(None, description="使用的LLM模型名称")
    status: str = Field("success", description="状态: success/error")
    error_message: Optional[str] = Field(None, description="错误信息")
    extra_data: Optional[Dict[str, Any]] = Field(None, description="其他元数据")


class ConversationLog(Base):
    """会话日志表"""
    __tablename__ = "conversation_logs"
    __table_args__ = (
        Index("idx_user_time", "user_id", "request_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    session_id = Column(String(64), nullable=False, index=True, comment="会话ID")
    user_id = Column(String(64), nullable=False, index=True, comment="用户ID")
    
    # 请求信息
    user_message = Column(Text, nullable=False, comment="用户消息")
    request_time = Column(DateTime, nullable=False, default=datetime.now, index=True, comment="请求时间")
    
    # 路由信息
    route_type = Column(String(32), nullable=False, comment="路由类型: LLM_SERVICE/TOOL_CALL/TASK_PLANNING")
    intent = Column(String(64), comment="识别的意图")

    # 上下文信息
    history_turns_count = Column(Integer, default=0, comment="注入的历史对话轮次数")
    memory_count = Column(Integer, default=0, comment="注入的长期记忆条数")
    context_snapshot = Column(JSON, comment="上下文快照：最近2轮历史+使用的记忆")

    # 工具调用
    tools_called = Column(JSON, comment="调用的工具列表 [{name, params, result}]")
    tool_count = Column(Integer, default=0, comment="工具调用次数")
    
    # 任务规划
    has_task_plan = Column(Boolean, default=False, comment="是否进行了任务规划")
    task_plan = Column(JSON, comment="任务规划详情")
    
    # 响应信息
    ai_response = Column(Text, comment="AI响应内容")
    response_time = Column(DateTime, comment="响应时间")
    duration_ms = Column(Integer, comment="处理耗时(毫秒)")
    
    # Token统计
    prompt_tokens = Column(Integer, default=0, comment="输入token数")
    completion_tokens = Column(Integer, default=0, comment="输出token数")
    total_tokens = Column(Integer, default=0, comment="总token数")
    model_name = Column(String(64), comment="使用的LLM模型名称")

    # 状态
    status = Column(String(16), nullable=False, default="success", comment="状态: success/error")
    error_message = Column(Text, comment="错误信息")
    
    # 元数据
    extra_data = Column(JSON, comment="其他元数据")
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="创建时间")
    
    def __repr__(self):
        return f"<ConversationLog(id={self.id}, user_id={self.user_id}, session_id={self.session_id})>"
