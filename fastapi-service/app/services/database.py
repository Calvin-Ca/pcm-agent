"""
数据库服务
"""
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from app.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseService:
    """数据库服务类"""
    
    def __init__(self):
        """初始化数据库连接"""
        self.db_url = f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}?charset=utf8mb4"
        
        self.engine = create_engine(
            self.db_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20,
            echo=settings.DEBUG
        )
        
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )
        
        logger.info("Database service initialized")
    
    @contextmanager
    def get_session(self) -> Session:
        """获取数据库会话（上下文管理器）"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        finally:
            session.close()
    
    def create_tables(self):
        """创建所有表"""
        from app.models.conversation import Base
        import app.models.ai_session  # noqa: F401 — 注册 AiSession 到 Base.metadata
        Base.metadata.create_all(bind=self.engine)
        logger.info("Database tables created")


# 全局数据库服务实例
db_service = None


def get_db_service() -> DatabaseService:
    """获取数据库服务实例"""
    global db_service
    if db_service is None:
        db_service = DatabaseService()
    return db_service
