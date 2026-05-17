"""
会话日志记录服务
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.models.conversation import ConversationLog, ConversationLogEntry
from app.services.database import get_db_service

logger = logging.getLogger(__name__)


class ConversationLogger:
    """会话日志记录器"""
    
    def __init__(self):
        self.db_service = get_db_service()
    
    def log_conversation(self, entry: ConversationLogEntry) -> int:
        """
        记录会话日志（技术债 #7：参数收敛为 ConversationLogEntry 模型）

        Args:
            entry: 会话日志参数模型，字段与默认值同原签名一一对应

        Returns:
            int: 日志记录ID（失败返回 -1，不抛异常以免影响主流程）
        """
        try:
            with self.db_service.get_session() as session:
                log = ConversationLog(
                    session_id=entry.session_id,
                    user_id=entry.user_id,
                    user_message=entry.user_message,
                    request_time=datetime.now(),
                    route_type=entry.route_type,
                    intent=entry.intent,
                    history_turns_count=entry.history_turns_count,
                    memory_count=entry.memory_count,
                    context_snapshot=entry.context_snapshot,
                    tools_called=entry.tools_called,
                    tool_count=len(entry.tools_called) if entry.tools_called else 0,
                    has_task_plan=entry.has_task_plan,
                    task_plan=entry.task_plan,
                    ai_response=entry.ai_response,
                    response_time=datetime.now(),
                    duration_ms=entry.duration_ms,
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    total_tokens=entry.prompt_tokens + entry.completion_tokens,
                    model_name=entry.model_name,
                    status=entry.status,
                    error_message=entry.error_message,
                    extra_data=entry.extra_data
                )
                
                session.add(log)
                session.flush()
                
                log_id = log.id
                logger.info(f"Conversation logged: id={log_id}, user={entry.user_id}, route={entry.route_type}")
                
                return log_id
                
        except Exception as e:
            logger.error(f"Failed to log conversation: {e}", exc_info=True)
            # 不抛出异常，避免影响主流程
            return -1
    
    def get_user_history(
        self,
        user_id: str,
        limit: int = 10,
        session_id: Optional[str] = None
    ) -> List[ConversationLog]:
        """
        获取用户历史对话
        
        Args:
            user_id: 用户ID
            limit: 返回数量
            session_id: 会话ID（可选）
            
        Returns:
            List[ConversationLog]: 对话记录列表
        """
        try:
            with self.db_service.get_session() as session:
                query = session.query(ConversationLog).filter(
                    ConversationLog.user_id == user_id
                )
                
                if session_id:
                    query = query.filter(ConversationLog.session_id == session_id)
                
                logs = query.order_by(
                    ConversationLog.created_at.desc()
                ).limit(limit).all()
                
                return logs
                
        except Exception as e:
            logger.error(f"Failed to get user history: {e}", exc_info=True)
            return []


# 全局实例
conversation_logger = None


def get_conversation_logger() -> ConversationLogger:
    """获取会话日志记录器实例"""
    global conversation_logger
    if conversation_logger is None:
        conversation_logger = ConversationLogger()
    return conversation_logger
