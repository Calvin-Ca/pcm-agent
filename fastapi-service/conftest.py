"""
Pytest configuration file
"""

# ── 必须在所有 app 模块 import 之前加载 .env ──────────────────────────────────
# LLMClient 直接读取 os.getenv，conftest 最先被 pytest 加载，此处 load_dotenv
# 可确保后续所有测试文件 import app.* 时环境变量已就绪。
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _base_dir = _Path(__file__).parent.parent  # ai-service/
    _load_dotenv(_base_dir / ".env")
    _load_dotenv(_base_dir / ".env.local", override=True)
except ImportError:
    pass
# ─────────────────────────────────────────────────────────────────────────────

import pytest

# Configure pytest-asyncio mode
pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    """Configure pytest"""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


@pytest.fixture(scope="session", autouse=True)
def initialize_llm_agent():
    """
    Session 级初始化：注入 LLMClient + ToolRegistry，使 node_llm_with_tools
    走 Function Calling 路径，而非规则匹配降级（_llm_client=None 时的行为）。

    Layer 1 / Layer 2 测试依赖此 fixture；其他测试不受影响。
    """
    from app.services.llm_client import LLMClient
    from app.services.tool_registry import ToolRegistry
    from app.services.permission_validator import PermissionValidator
    from app.services.task_executor import TaskExecutor
    from app.services.intent_router import IntentRouter
    from app.services.langgraph_agent import initialize_agent
    import app.tools  # noqa: F401 — 触发工具自动注册到 ToolRegistry 单例

    llm_client = LLMClient(env_prefix="CHAT_LLM")
    tool_reg = ToolRegistry()
    perm_validator = PermissionValidator()

    intent_router = IntentRouter()
    intent_router.set_tool_registry(tool_reg)
    intent_router.set_llm_client(llm_client)

    task_executor = TaskExecutor(
        tool_registry=tool_reg,
        permission_validator=perm_validator,
        llm_client=llm_client,
    )
    intent_router.set_task_executor(task_executor)

    initialize_agent(
        intent_router=intent_router,
        tool_registry=tool_reg,
        task_executor=task_executor,
        llm_client=llm_client,
    )
    yield
