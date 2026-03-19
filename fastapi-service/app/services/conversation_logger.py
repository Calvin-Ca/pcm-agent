"""
会话日志记录服务
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.models.conversation import ConversationLog
from app.services.database import get_db_service

logger = logging.getLogger(__name__)


class ConversationLogger:
    """会话日志记录器"""
    
    def __init__(self):
        self.db_service = get_db_service()
    
    def log_conversation(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        route_type: str,
        ai_response: Optional[str] = None,
        intent: Optional[str] = None,
        tools_called: Optional[List[Dict]] = None,
        has_task_plan: bool = False,
        task_plan: Optional[Dict] = None,
        duration_ms: Optional[int] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        status: str = "success",
        error_message: Optional[str] = None,
        extra_data: Optional[Dict] = None
    ) -> int:
        """
        记录会话日志
        
        Args:
            session_id: 会话ID
            user_id: 用户ID
            user_message: 用户消息
            route_type: 路由类型
            ai_response: AI响应
            intent: 识别的意图
            tools_called: 调用的工具列表
            has_task_plan: 是否有任务规划
            task_plan: 任务规划详情
            duration_ms: 处理耗时
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
            status: 状态
            error_message: 错误信息
            extra_data: 额外数据
            
        Returns:
            int: 日志记录ID
        """
        try:
            with self.db_service.get_session() as session:
                log = ConversationLog(
                    session_id=session_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_time=datetime.now(),
                    route_type=route_type,
                    intent=intent,
                    tools_called=tools_called,
                    tool_count=len(tools_called) if tools_called else 0,
                    has_task_plan=has_task_plan,
                    task_plan=task_plan,
                    ai_response=ai_response,
                    response_time=datetime.now(),
                    duration_ms=duration_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    status=status,
                    error_message=error_message,
                    extra_data=extra_data
                )
                
                session.add(log)
                session.flush()
                
                log_id = log.id
                logger.info(f"Conversation logged: id={log_id}, user={user_id}, route={route_type}")
                
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
