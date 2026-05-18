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


pytestmark = pytest.mark.asyncio


async def test_ensure_auth_preconfigured_priority(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT",
                MCP_TEST_USER_ID="u9", MCP_TEST_ENTITY_TYPE="deptAdmin",
                MCP_ENTITY_ID="E1", MCP_API_KEY="K1")  # SA 也在，但预配优先
    uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("u9", "deptAdmin", "JWT")


async def test_ensure_auth_cache_hit(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    m._cached_token = "CT"
    m._cached_user_id = "cu"
    m._cached_entity_type = "regionAdmin"
    uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("cu", "regionAdmin", "CT")


from unittest.mock import patch


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


async def test_ensure_auth_sa_fetch_parses_role(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1",
                MCP_TEST_ENTITY_TYPE="employee")
    payload = {"token": "SAJWT", "userId": "sa-uid", "entityType": "deptAdmin"}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("sa-uid", "deptAdmin", "SAJWT")
    # 缓存已写入
    assert m._cached_token == "SAJWT" and m._cached_entity_type == "deptAdmin"


async def test_ensure_auth_sa_role_missing_falls_back_to_env(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1",
                MCP_TEST_ENTITY_TYPE="regionAdmin")
    payload = {"token": "SAJWT", "userId": "sa-uid"}  # 无角色字段

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        uid, etype, tok = await m.ensure_auth()
    assert etype == "regionAdmin"  # 回退 env 默认，绝不空


async def test_ensure_auth_sa_empty_token_raises(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    payload = {"token": "", "userId": "x"}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(RuntimeError, match="返回空 token"):
            await m.ensure_auth()


async def test_ensure_auth_sa_called_once_then_cached(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResp({"token": "T", "userId": "u", "entityType": "employee"})

    with patch("httpx.AsyncClient.post", new=fake_post):
        await m.ensure_auth()
        await m.ensure_auth()
    assert calls["n"] == 1  # 第二次走缓存
