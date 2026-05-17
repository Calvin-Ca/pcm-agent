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


def test_identity_populated_from_headers(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.get("/probe", headers={
        "X-Gateway-Token": "GW_SECRET",
        "X-Auth-Token": "JWT123",
        "X-User-ID": "u42",
        "X-Entity-Type": "deptAdmin",
    })
    assert r.status_code == 200
    assert r.text == "u42|deptAdmin|JWT123"


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
