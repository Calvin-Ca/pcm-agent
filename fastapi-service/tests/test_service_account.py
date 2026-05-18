"""共享 Service Account：auth_configured / ensure_auth / call_ai_service_tool。"""
import importlib

import pytest

from mcp_servers import _service_account as sa


def _reload(monkeypatch, **env):
    for k in ("MCP_TEST_AUTH_TOKEN", "MCP_TEST_USER_ID", "MCP_TEST_ENTITY_TYPE",
              "MCP_ENTITY_ID", "MCP_API_KEY", "AI_SERVICE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(sa)
    return sa


def test_auth_configured_none(monkeypatch):
    m = _reload(monkeypatch)
    assert m.auth_configured() is False


def test_auth_configured_preconfigured_token(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT")
    assert m.auth_configured() is True


def test_auth_configured_service_account(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    assert m.auth_configured() is True


def test_auth_configured_partial_sa_is_false(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1")  # 缺 API_KEY
    assert m.auth_configured() is False
