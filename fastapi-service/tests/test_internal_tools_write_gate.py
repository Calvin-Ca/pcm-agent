"""internal_tools 写闸门：白名单 / dry_run 强制 / 审计（G1*/G3/G5）。"""
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import internal_tools
from app.services.tool_registry import tool_registry


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(internal_tools.router)
    return TestClient(app)


def _register(monkeypatch, name, is_write, handler):
    """临时把工具塞进单例 registry，测试后还原。"""
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    if name in tool_registry._tools:
        del tool_registry._tools[name]
        tool_registry._handlers.pop(name, None)
    tool_registry.register_tool(name, "x", schema, handler, is_write=is_write)
    yield
    tool_registry._tools.pop(name, None)
    tool_registry._handlers.pop(name, None)


def test_write_tool_not_whitelisted_403(client, monkeypatch):
    gen = _register(monkeypatch, "evil_write", True, lambda **k: {"ok": 1})
    next(gen)
    monkeypatch.setattr(internal_tools, "INTERNAL_WRITE_TOOLS", set())
    r = client.post("/api/internal/tools/evil_write", json={},
                     headers={"X-User-ID": "u", "X-Entity-Type": "employee", "X-Auth-Token": "t"})
    assert r.status_code == 403


def test_write_tool_forces_dry_run_default(client, monkeypatch):
    seen = {}

    async def h(**kw):
        seen.update(kw)
        return {"success": True}

    gen = _register(monkeypatch, "save_x", True, h)
    next(gen)
    monkeypatch.setattr(internal_tools, "INTERNAL_WRITE_TOOLS", {"save_x"})
    client.post("/api/internal/tools/save_x", json={"duration": 8},
                headers={"X-User-ID": "u", "X-Entity-Type": "employee", "X-Auth-Token": "t"})
    assert seen.get("dry_run") is True  # 未显式传 → 强制 True


def test_write_tool_explicit_dry_run_false_respected(client, monkeypatch):
    seen = {}

    async def h(**kw):
        seen.update(kw)
        return {"success": True}

    gen = _register(monkeypatch, "save_y", True, h)
    next(gen)
    monkeypatch.setattr(internal_tools, "INTERNAL_WRITE_TOOLS", {"save_y"})
    client.post("/api/internal/tools/save_y", json={"dry_run": False},
                headers={"X-User-ID": "u", "X-Entity-Type": "employee", "X-Auth-Token": "t"})
    assert seen.get("dry_run") is False


def test_read_tool_no_dry_run_injection(client, monkeypatch):
    seen = {}

    async def h(**kw):
        seen.update(kw)
        return {"success": True}

    gen = _register(monkeypatch, "read_z", False, h)
    next(gen)
    client.post("/api/internal/tools/read_z", json={},
                headers={"X-User-ID": "u", "X-Entity-Type": "employee", "X-Auth-Token": "t"})
    assert "dry_run" not in seen  # 只读工具不注入


def test_audit_emitted_without_auth_token(client, monkeypatch, caplog):
    async def h(**kw):
        return {"success": True, "record_id": "R1"}

    gen = _register(monkeypatch, "save_a", True, h)
    next(gen)
    monkeypatch.setattr(internal_tools, "INTERNAL_WRITE_TOOLS", {"save_a"})
    with caplog.at_level(logging.INFO, logger="audit"):
        client.post("/api/internal/tools/save_a", json={"project_id": "P", "duration": 8},
                    headers={"X-User-ID": "u", "X-Entity-Type": "employee", "X-Auth-Token": "SECRET"})
    audit_lines = [r.getMessage() for r in caplog.records if r.name == "audit"]
    assert any('"tag": "AUDIT"' in m or '"tag":"AUDIT"' in m for m in audit_lines), audit_lines
    blob = "\n".join(audit_lines)
    assert "SECRET" not in blob  # 绝不记录 auth_token
    assert "save_a" in blob
