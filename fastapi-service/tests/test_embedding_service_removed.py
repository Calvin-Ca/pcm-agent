"""
技术债 #9：未引用的 EmbeddingService 已删除

verify：
- app.services.embedding_service 模块不再可导入（已删除）
- 删除后核心模块 import 冒烟无 ImportError
"""

import importlib

import pytest


def test_embedding_service_module_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.embedding_service")


def test_core_modules_import_smoke():
    # 删除 embedding_service 后，核心链路 import 仍应正常
    import app.services.tool_registry  # noqa: F401
    import app.services.task_executor  # noqa: F401
    import app.services.langchain_rag  # noqa: F401
    import app.services.langgraph_agent  # noqa: F401
    import app.services.session_memory  # noqa: F401
    import app.tools  # noqa: F401
