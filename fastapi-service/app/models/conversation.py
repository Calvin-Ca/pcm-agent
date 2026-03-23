"""
会话记录数据模型
"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, Float, Boolean, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


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
