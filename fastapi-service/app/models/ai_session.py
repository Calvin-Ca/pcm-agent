"""
AI会话汇总表模型
"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Index
from app.models.conversation import Base


class AiSession(Base):
    """AI会话汇总表，每条对应一个会话，记录会话级别统计"""
    __tablename__ = "ai_sessions"
    __table_args__ = (
        Index("idx_ai_sessions_user_last", "user_id", "last_active"),
    )

    session_id  = Column(String(64), primary_key=True, comment="会话ID")
    user_id     = Column(String(64), nullable=False, comment="用户ID")
    turn_count  = Column(Integer, nullable=False, default=0, comment="对话轮次数")
    total_tokens = Column(Integer, nullable=False, default=0, comment="累计token消耗")
    created_at  = Column(DateTime, nullable=False, default=datetime.now, comment="会话创建时间")
    last_active = Column(DateTime, nullable=False, default=datetime.now, comment="最后活跃时间")

    def __repr__(self):
        return f"<AiSession(session_id={self.session_id}, user_id={self.user_id}, turns={self.turn_count})>"
