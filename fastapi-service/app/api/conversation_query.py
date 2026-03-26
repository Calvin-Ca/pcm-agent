"""
审计日志 & 会话记录查询接口
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_, func

from app.models.conversation import ConversationLog
from app.models.ai_session import AiSession
from app.services.database import get_db_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Audit Log"])

# 允许查看审计日志的角色
_ADMIN_ROLES = {"superAdmin", "regionAdmin", "deptAdmin", "companyAdmin"}


def _require_admin(request: Request) -> str:
    """从请求头校验管理员权限，返回 user_id；非管理员抛 403。"""
    entity_type = request.headers.get("X-Entity-Type", "")
    user_id = request.headers.get("X-User-ID", "anonymous")
    if entity_type not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="仅管理员可访问审计日志")
    return user_id


def _log_to_dict(log: ConversationLog, detail: bool = False) -> dict:
    """将 ORM 对象序列化为字典。"""
    d = {
        "id": log.id,
        "session_id": log.session_id,
        "user_id": log.user_id,
        "user_message": log.user_message,
        "intent": log.intent,
        "route_type": log.route_type,
        "tool_count": log.tool_count,
        "duration_ms": log.duration_ms,
        "model_name": log.model_name,
        "status": log.status,
        "error_message": log.error_message,
        "request_time": log.request_time.isoformat() if log.request_time else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }
    if detail:
        d.update({
            "ai_response": log.ai_response,
            "tools_called": log.tools_called,
            "history_turns_count": log.history_turns_count,
            "memory_count": log.memory_count,
            "context_snapshot": log.context_snapshot,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
        })
    return d


# ── 审计日志查询（管理员） ────────────────────────────────────────────────────

@router.get("/ai/audit")
async def query_audit_logs(
    request: Request,
    user_id: Optional[str] = Query(None, description="按用户ID过滤"),
    session_id: Optional[str] = Query(None, description="按会话ID过滤"),
    intent: Optional[str] = Query(None, description="按意图过滤，如 tool_execution / knowledge_qa"),
    status: Optional[str] = Query(None, description="按状态过滤：success / error / rejected"),
    start_time: Optional[str] = Query(None, description="开始时间，格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS"),
    end_time: Optional[str] = Query(None, description="结束时间，同上"),
    page: int = Query(1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    detail: bool = Query(False, description="是否返回详细字段（ai_response、tools_called 等）"),
):
    """
    审计日志查询接口（管理员权限）

    支持过滤条件：用户ID、会话ID、意图类型、状态、时间范围
    支持分页：page + page_size
    """
    _require_admin(request)

    try:
        db = get_db_service()
        with db.get_session() as sess:
            q = sess.query(ConversationLog)

            # 过滤条件
            conditions = []
            if user_id:
                conditions.append(ConversationLog.user_id == user_id)
            if session_id:
                conditions.append(ConversationLog.session_id == session_id)
            if intent:
                conditions.append(ConversationLog.intent == intent)
            if status:
                conditions.append(ConversationLog.status == status)
            if start_time:
                try:
                    dt = datetime.fromisoformat(start_time)
                    conditions.append(ConversationLog.request_time >= dt)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"start_time 格式错误: {start_time}")
            if end_time:
                try:
                    dt = datetime.fromisoformat(end_time)
                    conditions.append(ConversationLog.request_time <= dt)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"end_time 格式错误: {end_time}")

            if conditions:
                q = q.filter(and_(*conditions))

            total = q.count()
            offset = (page - 1) * page_size
            logs = q.order_by(ConversationLog.request_time.desc()).offset(offset).limit(page_size).all()

            return {
                "status": "success",
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size,
                },
                "data": [_log_to_dict(log, detail=detail) for log in logs],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"审计日志查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


@router.get("/ai/audit/stats")
async def audit_stats(
    request: Request,
    start_time: Optional[str] = Query(None, description="统计起始时间"),
    end_time: Optional[str] = Query(None, description="统计结束时间"),
):
    """
    审计日志统计摘要（管理员权限）

    返回：总请求数、各状态分布、各意图分布、平均响应时长
    """
    _require_admin(request)

    try:
        db = get_db_service()
        with db.get_session() as sess:
            q = sess.query(ConversationLog)
            conditions = []
            if start_time:
                conditions.append(ConversationLog.request_time >= datetime.fromisoformat(start_time))
            if end_time:
                conditions.append(ConversationLog.request_time <= datetime.fromisoformat(end_time))
            if conditions:
                q = q.filter(and_(*conditions))

            total = q.count()
            avg_duration = sess.query(func.avg(ConversationLog.duration_ms)).filter(
                and_(*conditions) if conditions else True
            ).scalar()

            # 按状态分组
            status_rows = sess.query(
                ConversationLog.status, func.count(ConversationLog.id)
            ).filter(and_(*conditions) if conditions else True).group_by(ConversationLog.status).all()

            # 按意图分组
            intent_rows = sess.query(
                ConversationLog.intent, func.count(ConversationLog.id)
            ).filter(and_(*conditions) if conditions else True).group_by(ConversationLog.intent).all()

            return {
                "status": "success",
                "data": {
                    "total_requests": total,
                    "avg_duration_ms": round(avg_duration or 0, 1),
                    "by_status": {row[0] or "unknown": row[1] for row in status_rows},
                    "by_intent": {row[0] or "unknown": row[1] for row in intent_rows},
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"审计统计查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


# ── 原有会话查询（保持兼容）────────────────────────────────────────────────────

@router.get("/conversations")
async def get_conversations(
    user_id: Optional[str] = Query(None, description="用户ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    limit: int = Query(10, description="返回数量", ge=1, le=100),
):
    """查询会话记录（兼容旧接口，无需管理员权限）"""
    try:
        db = get_db_service()
        with db.get_session() as sess:
            q = sess.query(ConversationLog)
            if user_id:
                q = q.filter(ConversationLog.user_id == user_id)
            if session_id:
                q = q.filter(ConversationLog.session_id == session_id)
            logs = q.order_by(ConversationLog.created_at.desc()).limit(limit).all()

        return {
            "status": "success",
            "count": len(logs),
            "data": [_log_to_dict(log) for log in logs],
        }
    except Exception as e:
        logger.error(f"会话查询失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "data": []}
