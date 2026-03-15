"""
Health Check API - 健康检查接口
"""

import logging
from fastapi import APIRouter
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    基础健康检查接口
    
    Returns:
        Dict: 健康状态
    """
    return {
        "status": "healthy",
        "service": "AI Assistant Service",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }


@router.get("/ping")
async def ping():
    """
    简单ping接口
    
    Returns:
        Dict: pong响应
    """
    return {"message": "pong"}