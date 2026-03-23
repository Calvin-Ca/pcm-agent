"""
数据库初始化接口
"""
import logging
from fastapi import APIRouter, HTTPException
from app.services.database import get_db_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Database Init"])


@router.post("/db/init")
async def initialize_database():
    """
    初始化数据库表结构
    
    Returns:
        Dict: 初始化结果
    """
    try:
        db_service = get_db_service()
        db_service.create_tables()
        
        logger.info("Database tables initialized successfully")
        
        return {
            "status": "success",
            "message": "数据库表初始化成功",
            "tables": ["conversation_logs", "ai_sessions"]
        }
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "message": "数据库初始化失败",
                "error": str(e)
            }
        )
