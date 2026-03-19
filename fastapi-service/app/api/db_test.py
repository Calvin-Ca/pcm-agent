"""
数据库测试接口
"""
import logging
from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Database Test"])


@router.get("/db/test")
async def test_database_connection():
    """
    测试数据库连接
    
    Returns:
        Dict: 连接状态和数据库信息
    """
    try:
        # 构建数据库连接URL
        db_url = f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
        
        # 创建引擎
        engine = create_engine(db_url, pool_pre_ping=True)
        
        # 测试连接
        with engine.connect() as conn:
            result = conn.execute(text("SELECT VERSION()"))
            version = result.scalar()
            
            # 获取数据库列表
            result = conn.execute(text("SHOW DATABASES"))
            databases = [row[0] for row in result]
            
            # 获取当前数据库的表
            result = conn.execute(text("SHOW TABLES"))
            tables = [row[0] for row in result]
        
        logger.info("Database connection test successful")
        
        return {
            "status": "success",
            "message": "数据库连接成功",
            "database": {
                "host": settings.MYSQL_HOST,
                "port": settings.MYSQL_PORT,
                "database": settings.MYSQL_DATABASE,
                "version": version,
                "databases": databases,
                "tables": tables
            }
        }
        
    except Exception as e:
        logger.error(f"Database connection failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "message": "数据库连接失败",
                "error": str(e)
            }
        )
