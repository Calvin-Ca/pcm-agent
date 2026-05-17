"""技术验证：tool_registry 支持 is_write 写分类（MCP Phase 3 G5）。"""
from app.services.tool_registry import ToolRegistry


def _schema():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def test_default_is_write_false():
    reg = ToolRegistry()
    reg.register_tool("rt", "read tool", _schema(), lambda **k: None)
    assert reg.is_write_tool("rt") is False


def test_register_is_write_true():
    reg = ToolRegistry()
    reg.register_tool("wt", "write tool", _schema(), lambda **k: None, is_write=True)
    assert reg.is_write_tool("wt") is True


def test_is_write_unknown_tool_false():
    reg = ToolRegistry()
    assert reg.is_write_tool("nope") is False
