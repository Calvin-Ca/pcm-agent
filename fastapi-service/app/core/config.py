"""
应用配置
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """应用配置"""
    
    # 基础配置
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    # CORS配置
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080", "http://localhost:8001", "http://localhost"]
    
    # 意图识别 LLM配置（轻量模型）
    INTENT_LLM_API_KEY: str = ""
    INTENT_LLM_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    INTENT_LLM_MODEL: str = "qwen-flash"

    # 主对话 LLM配置
    CHAT_LLM_API_KEY: str
    CHAT_LLM_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    CHAT_LLM_MODEL: str = "qwen-plus"
    CHAT_LLM_TEMPERATURE: float = 0.7
    CHAT_LLM_MAX_TOKENS: int = 2000
    LLM_NUM_CTX_SHORT: int = 4096   # 短对话（历史 ≤2000 字）
    LLM_NUM_CTX_LONG: int = 8192    # 长对话（历史 >2000 字）

    # 任务规划 LLM配置（可选，默认复用主对话配置）
    PLANNER_LLM_API_KEY: str = ""
    PLANNER_LLM_API_BASE: str = ""
    PLANNER_LLM_MODEL: str = ""
    
    # MySQL配置
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "workhour"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    
    # Redis配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    
    # Milvus配置
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    
    # 会话配置
    SESSION_EXPIRE_SECONDS: int = 1800  # 30分钟
    MAX_CONVERSATION_HISTORY: int = 10
    
    # 工具配置
    TOOL_TIMEOUT_SECONDS: int = 30

    # SpringBoot 后端地址（工具调用时使用）
    SPRINGBOOT_BASE_URL: str = "http://localhost:8080"

    class Config:
        # .env.local 优先级高于 .env，本地开发在 .env.local 中覆盖差异值
        env_file = ("../.env", "../.env.local")
        case_sensitive = True
        extra = "ignore"


settings = Settings()
