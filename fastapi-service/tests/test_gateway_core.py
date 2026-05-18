"""网关核心：鉴权中间件 + 身份 contextvar + 转发助手。"""
import pytest
from unittest.mock import AsyncMock, patch

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_servers import _gateway_core as gc

pytestmark = pytest.mark.asyncio


def _app(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW_SECRET")
    import importlib
    importlib.reload(gc)

    async def probe(request):
        ident = gc.get_identity()
        return PlainTextResponse(
            f"{ident.user_id}|{ident.entity_type}|{ident.auth_token}"
        )

    app = Starlette(routes=[Route("/probe", probe), Route("/health/health", lambda r: PlainTextResponse("ok"))])
    app.add_middleware(gc.GatewayAuthMiddleware)
    return app


def test_missing_gateway_token_401(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.get("/probe", headers={})
    assert r.status_code == 401
    assert "X-Gateway-Token" in r.text


def test_wrong_gateway_token_401(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.get("/probe", headers={"X-Gateway-Token": "nope"})
    assert r.status_code == 401


def test_health_bypasses_auth(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.get("/health/health", headers={})
    assert r.status_code == 200


def _app_resolved(monkeypatch, fetch_impl):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW_SECRET")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    import importlib
    importlib.reload(gc)
    monkeypatch.setattr(gc, "fetch_service_account_token", fetch_impl)

    async def probe(request):
        i = gc.get_identity()
        return PlainTextResponse(f"{i.user_id}|{i.entity_type}|{i.auth_token}|{i.entity_id}")

    app = Starlette(routes=[
        Route("/probe", probe),
        Route("/health/health", lambda r: PlainTextResponse("ok")),
    ])
    app.add_middleware(gc.GatewayAuthMiddleware)
    return app


def test_identity_resolved_via_service_account(monkeypatch):
    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        return ("JWTx", "uuid-99", "deptAdmin")

    client = TestClient(_app_resolved(monkeypatch, fake_fetch))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET",
                                      "X-Entity-ID": "ding-99"})
    assert r.status_code == 200
    assert r.text == "uuid-99|deptAdmin|JWTx|ding-99"


def test_missing_entity_id_401(monkeypatch):
    async def fake_fetch(*a, **k):
        raise AssertionError("不应被调用")

    client = TestClient(_app_resolved(monkeypatch, fake_fetch))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET"})
    assert r.status_code == 401
    assert "X-Entity-ID" in r.text


def test_sa_failure_502_no_secret(monkeypatch):
    async def boom(entity_id, api_key, *, ai_service_url,
                   role_key="entityType", fallback_entity_type="employee"):
        raise RuntimeError("Service Account 认证返回空 token")

    client = TestClient(_app_resolved(monkeypatch, boom))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET",
                                      "X-Entity-ID": "ding-1"})
    assert r.status_code == 502
    assert "SHARED" not in r.text and "JWT" not in r.text
    assert "identity resolution failed" in r.text


async def test_forward_passes_identity_headers(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW_SECRET")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-service:8000")
    import importlib
    importlib.reload(gc)
    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp()

    tok = gc._IDENTITY.set(gc.Identity(user_id="u1", entity_type="employee", auth_token="T1"))
    try:
        with patch("httpx.AsyncClient.post", new=fake_post):
            out = await gc.forward_to_ai_service("save_workhour", {"project_id": "P"})
    finally:
        gc._IDENTITY.reset(tok)

    assert out == {"ok": True}
    assert captured["url"] == "http://ai-service:8000/api/internal/tools/save_workhour"
    assert captured["headers"]["X-User-ID"] == "u1"
    assert captured["headers"]["X-Entity-Type"] == "employee"
    assert captured["headers"]["X-Auth-Token"] == "T1"
    assert captured["json"] == {"project_id": "P"}


async def test_resolve_identity_caches_per_entity(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-svc:8000")
    import importlib
    importlib.reload(gc)
    calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        calls["n"] += 1
        return ("TOK-" + entity_id, "uuid-" + entity_id, "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)

    i1 = await gc.resolve_identity("E1")
    i1b = await gc.resolve_identity("E1")          # 命中缓存
    i2 = await gc.resolve_identity("E2")           # 不同人，另起
    assert calls["n"] == 2
    assert i1.user_id == "uuid-E1" and i1.auth_token == "TOK-E1"
    assert i1.entity_id == "E1" and i1b is i1
    assert i2.user_id == "uuid-E2"


async def test_resolve_identity_ttl_expiry(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("MCP_GATEWAY_TOKEN_TTL", "0")  # 立即过期
    import importlib
    importlib.reload(gc)
    calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        calls["n"] += 1
        return ("T", "u", "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)
    await gc.resolve_identity("E1")
    await gc.resolve_identity("E1")   # TTL=0 → 再次 fetch
    assert calls["n"] == 2


async def test_forward_reauth_on_401(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-svc:8000")
    import importlib
    importlib.reload(gc)

    fetch_calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        fetch_calls["n"] += 1
        return (f"TOK{fetch_calls['n']}", "uid", "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)

    import httpx
    post_calls = {"n": 0, "tokens": []}

    class Resp401:
        status_code = 401
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "401", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(401))
        def json(self): return {}

    class RespOK:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    async def fake_post(self, url, json=None, headers=None):
        post_calls["n"] += 1
        post_calls["tokens"].append(headers["X-Auth-Token"])
        return Resp401() if post_calls["n"] == 1 else RespOK()

    ident = await gc.resolve_identity("E1")           # 预热缓存
    tok = gc._IDENTITY.set(ident)
    try:
        with patch("httpx.AsyncClient.post", new=fake_post):
            out = await gc.forward_to_ai_service("query_timesheet", {})
    finally:
        gc._IDENTITY.reset(tok)

    assert out == {"ok": True}
    assert post_calls["n"] == 2                        # 重发一次
    assert fetch_calls["n"] == 2                        # evict 后重换
    assert post_calls["tokens"][0] != post_calls["tokens"][1]
