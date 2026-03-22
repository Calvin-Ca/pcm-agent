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
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    
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

    # 任务规划 LLM配置（可选，默认复用主对话配置）
    PLANNER_LLM_API_KEY: str = ""
    PLANNER_LLM_API_BASE: str = ""
    PLANNER_LLM_MODEL: str = ""
    
    # MySQL配置
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "workhour"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "19990512"
    
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
    
    class Config:
        env_file = "../.env"
        case_sensitive = True


settings = Settings()
