"""
FastAPI AI Service - 主应用入口
"""
import os
from pathlib import Path

# 必须在所有业务 import 之前加载 .env，否则 os.getenv() 拿不到值
_base_dir = Path(__file__).parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_base_dir / ".env")
    load_dotenv(_base_dir / ".env.local", override=True)  # 本地开发覆盖
except ImportError:
    pass  # 生产环境由 Docker/系统环境变量注入，不需要 dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api import health
from app.api.chat import router as chat_router, initialize_chat_components
from app.api.memory import router as memory_router
from app.api.init_db import router as init_db_router
from app.api.db_test import router as db_test_router
from app.api.conversation_query import router as conversation_query_router
from app.api.internal_tools import router as internal_tools_router
from app.api.internal_auth import router as internal_auth_router
from app.services.tool_registry import ToolRegistry, verify_expected_tools
from app.services.permission_validator import PermissionValidator
from app.services.llm_client import LLMClient
from app.tools import query_timesheet, query_project, compute_statistics, sql_query
from app.services.langchain_rag import initialize_langchain_rag
from app.services.sql_engine import sql_engine
from app.services.session_memory import initialize_session_memory
from app.services.user_memory import initialize_user_memory
from app.services.prompt_builder import initialize_prompt_builder, get_prompt_builder
from prometheus_client import make_asgi_app as make_metrics_app

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
    
    logger.info("[ AI Service starting...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Chat LLM Model: {settings.CHAT_LLM_MODEL}")

    redis_client = None

    # 启动时初始化
    try:
        # 初始化数据库表（幂等，表已存在时不报错）
        # 数据库不可用时降级，不影响核心 AI 功能
        try:
            from app.services.database import get_db_service
            get_db_service().create_tables()
            logger.info("[OK] Database tables ready")
        except Exception as db_err:
            logger.warning(f"[WARN]  数据库不可用，审计日志功能将降级: {db_err}")

        # 初始化工具注册中心
        tool_registry = ToolRegistry()
        logger.info("[OK] Tool Registry initialized")

        # 注册工具（导入工具模块会触发自动注册）
        import app.tools  # noqa: F401 — 触发 app/tools/__init__.py 的工具自动注册
        logger.info("[OK] Tools registered")

        # 技术债 #2：校验预期工具全部注册成功，缺失则明确报出（fail-soft，不中断启动）
        missing_tools = verify_expected_tools(tool_registry)
        if missing_tools:
            logger.error(
                "[ERROR] 工具注册不完整，缺失 %d 个: %s（服务继续启动，相关能力降级）",
                len(missing_tools),
                ", ".join(missing_tools),
            )

        # 初始化权限验证器
        permission_validator = PermissionValidator()
        logger.info("[OK] Permission Validator initialized")

        # 初始化 LLM 客户端
        llm_client = LLMClient(env_prefix="CHAT_LLM")
        logger.info(f"[OK] LLM Client initialized (model: {llm_client.model})")

        # ── Task 37/38: 初始化 Redis 连接和记忆服务 ──────────────────────────
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=False,  # 保持 bytes，service 层自行解码
            )
            await redis_client.ping()
            logger.info(f"[OK] Redis 连接成功 ({settings.REDIS_HOST}:{settings.REDIS_PORT})")

            initialize_session_memory(
                redis_client=redis_client,
                ttl=settings.SESSION_EXPIRE_SECONDS,
                max_history=settings.MAX_CONVERSATION_HISTORY,
            )
            initialize_user_memory(redis_client=redis_client)

            from app.services.session_memory import get_session_memory
            from app.services.user_memory import get_user_memory
            initialize_prompt_builder(
                session_memory_service=get_session_memory(),
                user_memory_service=get_user_memory(),
            )
        except Exception as redis_err:
            logger.warning(f"[WARN]  Redis 不可用，记忆功能将降级: {redis_err}")

        initialize_chat_components(
            tool_reg=tool_registry,
            perm_validator=permission_validator,
            llm_client=llm_client,
            prompt_builder=get_prompt_builder(),
        )
        logger.info("[OK] Chat components initialized")

        # 初始化 SQL Engine（SQL Agent）
        if settings.SQL_AGENT_ENABLED:
            try:
                await sql_engine.initialize()
                logger.info("[OK] SQL Engine initialized")
            except Exception as sql_err:
                logger.warning(f"[WARN]  SQL Engine 初始化失败，SQL Agent 功能将不可用: {sql_err}")

        # 初始化 LangChain RAG 服务（混合检索：Milvus + BM25）
        # knowledge-base 在本文件所在目录的子目录（ai-service/fastapi-service/knowledge-base）
        _kb_path = os.path.join(os.path.dirname(__file__), "knowledge-base")
        kb_result = await initialize_langchain_rag(kb_path=_kb_path)
        if kb_result.get("success"):
            logger.info(
                f"[OK] LangChain RAG initialized: {kb_result.get('loaded_files', 0)} files, "
                f"{kb_result.get('total_chunks', 0)} chunks"
            )
        else:
            logger.warning(f"[WARN]  LangChain RAG init failed: {kb_result.get('error', 'unknown')}")

        logger.info("🎉 AI Service startup completed successfully")

        # 注册服务信息指标
        from app.core.metrics import SERVICE_INFO
        SERVICE_INFO.info({
            "version": "1.2",
            "phase": "3",
            "llm_model": settings.CHAT_LLM_MODEL,
        })

    except Exception as e:
        logger.error(f"[ERROR] Failed to initialize AI Service: {e}", exc_info=True)
        raise

    yield

    # 关闭时清理
    logger.info("[ AI Service shutting down...")
    if redis_client:
        await redis_client.aclose()
        logger.info("[OK] Redis 连接已关闭")
    if settings.SQL_AGENT_ENABLED:
        await sql_engine.close()
        logger.info("[OK] SQL Engine closed")


# 创建FastAPI应用
app = FastAPI(
    title="AI 智能助手服务",
    description="""
## 工时管理系统 AI 智能助手 API

基于 LangGraph + LangChain 构建的 AI 对话服务，提供工时查询、周报生成、知识问答等功能。

### 认证方式

所有接口通过 SpringBoot 网关代理，由网关完成 JWT 验证后，将以下信息注入请求头：

| 请求头 | 说明 |
|--------|------|
| `X-User-ID` | 当前登录用户 ID |
| `X-Entity-Type` | 用户角色（employee / deptAdmin / superAdmin 等） |
| `X-Department-ID` | 用户所属部门 ID |
| `Authorization` | 原始 Bearer Token（工具调用透传给 SpringBoot） |

### 主要接口分组

- **AI Chat**：核心对话接口，SSE 流式输出
- **Audit Log**：审计日志查询（管理员）
- **Memory Management**：用户记忆管理
- **Health**：服务健康检查
""",
    version="1.0.0",
    lifespan=lifespan,
    contact={"name": "技术支持", "email": "support@thsware.com"},
)

# Prometheus metrics 端点
metrics_app = make_metrics_app()
app.mount("/metrics", metrics_app)

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
app.include_router(memory_router, prefix="/api", tags=["Memory Management"])
app.include_router(init_db_router, prefix="/api", tags=["Database Init"])
app.include_router(db_test_router, prefix="/api", tags=["Database Test"])
app.include_router(conversation_query_router, prefix="/api", tags=["Audit Log"])
app.include_router(internal_tools_router, tags=["Internal Tools"])
app.include_router(internal_auth_router, tags=["Internal Auth"])


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
        reload=False,
        workers=1,
    )
