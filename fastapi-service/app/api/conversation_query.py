"""
会话记录查询接口
"""
import logging
from fastapi import APIRouter, Query
from typing import Optional
from app.services.conversation_logger import get_conversation_logger

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Conversation Query"])


@router.get("/conversations")
async def get_conversations(
    user_id: Optional[str] = Query(None, description="用户ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    limit: int = Query(10, description="返回数量", ge=1, le=100)
):
    """
    查询会话记录
    
    Args:
        user_id: 用户ID（可选）
        session_id: 会话ID（可选）
        limit: 返回数量
        
    Returns:
        List: 会话记录列表
    """
    try:
        conv_logger = get_conversation_logger()
        
        if user_id:
            logs = conv_logger.get_user_history(
                user_id=user_id,
                limit=limit,
                session_id=session_id
            )
        else:
            # 如果没有指定用户，返回最近的记录
            from app.services.database import get_db_service
            from app.models.conversation import ConversationLog
            
            db_service = get_db_service()
            with db_service.get_session() as session:
                query = session.query(ConversationLog)
                
                if session_id:
                    query = query.filter(ConversationLog.session_id == session_id)
                
                logs = query.order_by(
                    ConversationLog.created_at.desc()
                ).limit(limit).all()
        
        # 转换为字典
        result = []
        for log in logs:
            result.append({
                "id": log.id,
                "session_id": log.session_id,
                "user_id": log.user_id,
                "user_message": log.user_message,
                "route_type": log.route_type,
                "intent": log.intent,
                "tool_count": log.tool_count,
                "has_task_plan": log.has_task_plan,
                "duration_ms": log.duration_ms,
                "status": log.status,
                "created_at": log.created_at.isoformat() if log.created_at else None
            })
        
        return {
            "status": "success",
            "count": len(result),
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Failed to query conversations: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
            "data": []
        }
