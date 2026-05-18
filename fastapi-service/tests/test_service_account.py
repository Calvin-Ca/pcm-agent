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


async def test_call_ai_service_tool_headers_and_url(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT",
                MCP_TEST_USER_ID="u1", MCP_TEST_ENTITY_TYPE="employee",
                AI_SERVICE_URL="http://ai-svc:8000")
    captured = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return R()

    with patch("httpx.AsyncClient.post", new=fake_post):
        out = await m.call_ai_service_tool("query_timesheet", {"a": 1})

    assert out == {"ok": True}
    assert captured["url"] == "http://ai-svc:8000/api/internal/tools/query_timesheet"
    assert captured["headers"] == {
        "X-User-ID": "u1", "X-Entity-Type": "employee", "X-Auth-Token": "JWT"}
    assert captured["json"] == {"a": 1}


async def test_call_logs_no_token(monkeypatch, caplog):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="SECRET_JWT",
                MCP_TEST_USER_ID="u1")

    class R:
        def raise_for_status(self): pass
        def json(self): return {}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return R()

    import logging as _lg
    with caplog.at_level(_lg.INFO, logger="mcp-service-account"):
        with patch("httpx.AsyncClient.post", new=fake_post):
            await m.call_ai_service_tool("query_project", {})
    assert "SECRET_JWT" not in caplog.text
    assert "query_project" in caplog.text


async def test_fetch_service_account_token_parses(monkeypatch):
    m = _reload(monkeypatch)

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        assert url.endswith("/api/internal/auth/mcp-token")
        assert json == {"entity_id": "E9", "api_key": "K9"}
        return _FakeResp({"token": "T9", "userId": "uid9", "entityType": "deptAdmin"})

    with patch("httpx.AsyncClient.post", new=fake_post):
        tok, uid, et = await m.fetch_service_account_token(
            "E9", "K9", ai_service_url="http://ai-svc:8000")
    assert (tok, uid, et) == ("T9", "uid9", "deptAdmin")


async def test_fetch_service_account_token_role_fallback_and_empty(monkeypatch):
    m = _reload(monkeypatch)

    async def fake_no_role(self, url, json=None, headers=None, timeout=None):
        return _FakeResp({"token": "T", "userId": "u"})  # 无角色键

    with patch("httpx.AsyncClient.post", new=fake_no_role):
        tok, uid, et = await m.fetch_service_account_token(
            "E", "K", ai_service_url="http://x", fallback_entity_type="regionAdmin")
    assert et == "regionAdmin"  # 回退，绝不空

    async def fake_empty(self, url, json=None, headers=None, timeout=None):
        return _FakeResp({"token": "", "userId": "u"})

    with patch("httpx.AsyncClient.post", new=fake_empty):
        with pytest.raises(RuntimeError, match="返回空 token"):
            await m.fetch_service_account_token("E", "K", ai_service_url="http://x")
