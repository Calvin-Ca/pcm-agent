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
from app.api.chat import router as chat_router, initialize_chat_components
from app.services.tool_registry import ToolRegistry
from app.services.permission_validator import PermissionValidator
from app.services.llm_client import LLMClient
from app.tools import query_timesheet, query_project, compute_statistics

# 配置日志
setup_logging()
logger = logging.getLogger(__name__)

# 全局组件
tool_registry = None
permission_validator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global tool_registry, permission_validator

    logger.info("🚀 AI Service starting...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Chat LLM Model: {settings.CHAT_LLM_MODEL}")

    # 启动时初始化
    try:
        # 初始化工具注册中心
        tool_registry = ToolRegistry()
        logger.info("✅ Tool Registry initialized")

        # 注册工具（工具会自动注册）
        # 导入工具模块会触发自动注册
        logger.info("✅ Tools registered")

        # 初始化权限验证器
        permission_validator = PermissionValidator()
        logger.info("✅ Permission Validator initialized")

        # 初始化 LLM 客户端
        llm_client = LLMClient(env_prefix="CHAT_LLM")
        logger.info(f"✅ LLM Client initialized (model: {llm_client.model})")

        initialize_chat_components(
            tool_reg=tool_registry,
            perm_validator=permission_validator,
            llm_client=llm_client
        )
        logger.info("✅ Chat components initialized")

        logger.info("🎉 AI Service startup completed successfully")

    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Service: {e}", exc_info=True)
        raise

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
app.include_router(chat_router, prefix="/api", tags=["AI Chat"])

# 导入并注册数据库测试路由
from app.api import db_test, init_db, conversation_query
app.include_router(db_test.router, prefix="/api", tags=["Database Test"])
app.include_router(init_db.router, prefix="/api", tags=["Database Init"])
app.include_router(conversation_query.router, prefix="/api", tags=["Conversation Query"])


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
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
