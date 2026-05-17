"""网关 server：FastMCP 装配 + save_workhour 转发壳（confirm→dry_run）。"""
import importlib

import pytest

pytestmark = pytest.mark.asyncio


def _mod():
    import mcp_servers.http_gateway_server as m
    importlib.reload(m)
    return m


def test_app_factory_returns_asgi_with_middleware():
    m = _mod()
    app = m.build_app()
    assert callable(app)
    # 中间件已挂载（user_middleware 含 GatewayAuthMiddleware）
    from mcp_servers._gateway_core import GatewayAuthMiddleware
    classes = [mw.cls for mw in app.user_middleware]
    assert GatewayAuthMiddleware in classes


async def test_save_workhour_confirm_false_forwards_dry_run_true(monkeypatch):
    m = _mod()
    captured = {}

    async def fake_forward(tool_name, params):
        captured["tool"] = tool_name
        captured["params"] = params
        return {"success": True, "result": {"dry_run": True}}

    monkeypatch.setattr(m, "forward_to_ai_service", fake_forward)
    out = await m._save_workhour_impl(
        project_id="AI平台", date="2026-05-10", duration=8,
        description="开发", confirm=False,
    )
    assert captured["tool"] == "save_workhour"
    assert captured["params"]["dry_run"] is True
    assert "user_id" not in captured["params"] and "memberId" not in captured["params"]
    assert "success" in out


async def test_save_workhour_confirm_true_forwards_dry_run_false(monkeypatch):
    m = _mod()
    captured = {}

    async def fake_forward(tool_name, params):
        captured["params"] = params
        return {"success": True}

    monkeypatch.setattr(m, "forward_to_ai_service", fake_forward)
    await m._save_workhour_impl(
        project_id="AI平台", date="2026-05-10", duration=8, confirm=True,
    )
    assert captured["params"]["dry_run"] is False
