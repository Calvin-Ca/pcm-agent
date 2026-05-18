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


async def test_query_timesheet_forwards_filtered_params(monkeypatch):
    m = _mod()
    captured = {}

    async def fake_forward(tool_name, params):
        captured["tool"] = tool_name
        captured["params"] = params
        return {"success": True}

    monkeypatch.setattr(m, "forward_to_ai_service", fake_forward)
    await m._query_timesheet_impl(member_id=None, project_id="P1",
                                  start_date=None, end_date="2026-05-10")
    assert captured["tool"] == "query_timesheet"
    # None 不传，让 ai-service 用默认
    assert captured["params"] == {"project_id": "P1", "end_date": "2026-05-10"}


def test_all_expected_tools_registered():
    m = _mod()
    names = set(m.list_tool_names())
    assert names == {
        "save_workhour", "query_timesheet", "query_project",
        "compute_statistics", "generate_weekly_report", "sql_query",
        "kb_outline", "kb_keyword_search", "kb_semantic_search",
        "kb_read_section",
    }


def test_save_workhour_docstring_reflects_sa_identity():
    import importlib
    m = importlib.import_module("mcp_servers.http_gateway_server")
    doc = m.save_workhour.__doc__ or ""
    assert "X-Auth-Token" not in doc
    assert "X-Entity-ID" in doc and "Service Account" in doc


def test_save_workhour_impl_no_target_user_id():
    """G4 结构性保护：网关 save 参数不含目标 user_id（保持）。"""
    import importlib, inspect
    m = importlib.import_module("mcp_servers.http_gateway_server")
    src = inspect.getsource(m._save_workhour_impl)
    assert '"user_id"' not in src and "'user_id'" not in src
    assert "memberId" not in src
