"""6 个只读 server 迁移后：import 共享模块、无残留样板、guard 用 auth_configured。"""
import importlib
import inspect

import pytest

MODS = [
    "mcp_servers.timesheet_mcp_server",
    "mcp_servers.project_mcp_server",
    "mcp_servers.statistics_mcp_server",
    "mcp_servers.weekly_report_mcp_server",
    "mcp_servers.sql_query_mcp_server",
    "mcp_servers.knowledge_qa_mcp_server",
]


@pytest.mark.parametrize("name", MODS)
def test_imports_shared_module(name):
    src = inspect.getsource(importlib.import_module(name))
    assert "from mcp_servers._service_account import" in src
    assert "async def _call_ai_service_tool" not in src
    assert 'os.getenv("MCP_TEST_AUTH_TOKEN"' not in src
    assert "if not USER_ID or not AUTH_TOKEN" not in src
    assert "auth_configured()" in src
