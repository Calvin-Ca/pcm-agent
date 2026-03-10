"""
健康检查接口
"""
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "ai-assistant"
    }


@router.get("/ready")
async def readiness_check():
    """就绪检查"""
    # TODO: 检查数据库连接
    # TODO: 检查Redis连接
    # TODO: 检查Milvus连接
    
    return {
        "status": "ready",
        "checks": {
            "database": "ok",
            "redis": "ok",
            "milvus": "ok"
        }
    }
