"""
FastAPI AI Service - 主应用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api import health

# 配置日志
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🚀 AI Service starting...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"LLM Model: {settings.LLM_MODEL}")
    
    # 启动时初始化
    # TODO: 初始化数据库连接池
    # TODO: 初始化Redis连接
    # TODO: 初始化Milvus连接
    
    yield
    
    # 关闭时清理
    logger.info("🛑 AI Service shutting down...")
    # TODO: 关闭数据库连接
    # TODO: 关闭Redis连接
    # TODO: 关闭Milvus连接


# 创建FastAPI应用
app = FastAPI(
    title="AI Assistant Service",
    description="AI智能助手服务API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理器"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "服务器内部错误，请稍后重试",
            "detail": str(exc) if settings.DEBUG else None
        }
    )


# 注册路由
app.include_router(health.router, prefix="/health", tags=["Health"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "AI Assistant Service",
        "version": "1.0.0",
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
