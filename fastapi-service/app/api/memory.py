"""
Memory Management API（Task 41）

提供用户长期记忆的查询和清除接口。
用户可以通过这两个接口查看或重置 AI 对自己的"印象"。

路由：
    GET    /api/ai/memory   查询当前用户的长期记忆列表
    DELETE /api/ai/memory   清除当前用户的全部长期记忆
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.services.user_memory import get_user_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/memory", tags=["Memory Management"])


@router.get("")
async def list_user_memories(user_id: str = Query(..., description="用户 ID")):
    """
    查询指定用户的长期记忆列表。

    返回记忆内容、重要度、访问次数、创建时间等信息。
    """
    svc = get_user_memory()
    if not svc:
        return {"success": False, "error": "记忆服务未初始化", "memories": []}

    try:
        memories = await svc.list_memories(user_id)
        return {
            "success": True,
            "user_id": user_id,
            "total": len(memories),
            "memories": [
                {
                    "memory_id": m.memory_id,
                    "content": m.content,
                    "importance": round(m.importance, 2),
                    "access_count": m.access_count,
                    "created_at": m.created_at,
                    "last_accessed": m.last_accessed,
                }
                for m in memories
            ],
        }
    except Exception as e:
        logger.error(f"查询用户记忆失败 user_id={user_id}: {e}")
        return {"success": False, "error": str(e), "memories": []}


@router.delete("")
async def clear_user_memories(user_id: str = Query(..., description="用户 ID")):
    """
    清除指定用户的全部长期记忆。

    清除后，AI 将不再记得该用户之前表达过的偏好和信息。
    """
    svc = get_user_memory()
    if not svc:
        return {"success": False, "error": "记忆服务未初始化", "deleted": 0}

    try:
        deleted = await svc.clear_memories(user_id)
        return {
            "success": True,
            "user_id": user_id,
            "deleted": deleted,
            "message": f"已清除 {deleted} 条长期记忆",
        }
    except Exception as e:
        logger.error(f"清除用户记忆失败 user_id={user_id}: {e}")
        return {"success": False, "error": str(e), "deleted": 0}
