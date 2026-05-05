"""
单元测试: 4 个层次化检索 Tool 包装

覆盖:
- ToolRegistry 中已注册了 kb_outline / kb_keyword_search / kb_semantic_search / kb_read_section
- 每个 Tool 的 json_schema 含 name/description/parameters
- 每个 Tool 的 handler 能调用 (mock kb_navigator) 并返回合理结构
- 参数缺失时返回 error 字段而不是抛异常
"""

import pytest

from app.services.tool_registry import tool_registry


# ─── 注册检查 ─────────────────────────────────────────────────────────────────


def test_kb_tools_are_registered():
    for name in ("kb_outline", "kb_keyword_search", "kb_semantic_search", "kb_read_section"):
        tool = tool_registry.get_tool(name)
        assert tool is not None, f"工具 {name} 未注册"
        assert tool.description, f"工具 {name} 缺少 description"
        assert isinstance(tool.json_schema, dict), f"{name}.json_schema 不是 dict"
        assert tool.json_schema.get("type") == "object"
        assert "properties" in tool.json_schema


def test_knowledge_qa_still_registered():
    """旧 knowledge_qa 应保留作为 fallback"""
    assert tool_registry.get_tool("knowledge_qa") is not None


# ─── kb_outline ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_outline_handler_calls_navigator(monkeypatch):
    from app.tools import kb_outline as kb_outline_tool

    captured = {}

    async def fake_get_outline(category=None):
        captured["category"] = category
        return {"documents": [{"file": "x.md", "title": "X", "h2": ["a"], "tags": [], "category": "工时管理"}]}

    monkeypatch.setattr(kb_outline_tool, "get_outline", fake_get_outline)

    res = await kb_outline_tool.kb_outline_handler(category="工时管理")
    assert captured["category"] == "工时管理"
    assert res["documents"][0]["title"] == "X"


@pytest.mark.asyncio
async def test_kb_outline_handler_no_args(monkeypatch):
    from app.tools import kb_outline as kb_outline_tool

    async def fake_get_outline(category=None):
        return {"documents": []}

    monkeypatch.setattr(kb_outline_tool, "get_outline", fake_get_outline)
    res = await kb_outline_tool.kb_outline_handler()
    assert res == {"documents": []}


# ─── kb_keyword_search ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_keyword_search_handler(monkeypatch):
    from app.tools import kb_keyword_search as kw

    async def fake(query, category=None, top_k=5):
        return [{"file": "a.md", "section": "h2", "score": None, "snippet": query}]

    monkeypatch.setattr(kw, "keyword_search", fake)

    res = await kw.kb_keyword_search_handler(query="加班", top_k=3)
    assert res["count"] == 1
    assert res["results"][0]["snippet"] == "加班"


@pytest.mark.asyncio
async def test_kb_keyword_search_missing_query():
    from app.tools.kb_keyword_search import kb_keyword_search_handler

    res = await kb_keyword_search_handler()
    assert res["results"] == []
    assert "error" in res


@pytest.mark.asyncio
async def test_kb_keyword_search_invalid_top_k(monkeypatch):
    """top_k 不是数字时回退到默认值, 不应崩溃"""
    from app.tools import kb_keyword_search as kw

    async def fake(query, category=None, top_k=5):
        return []

    monkeypatch.setattr(kw, "keyword_search", fake)
    res = await kw.kb_keyword_search_handler(query="x", top_k="abc")
    assert res == {"results": [], "count": 0}


# ─── kb_semantic_search ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_semantic_search_handler(monkeypatch):
    from app.tools import kb_semantic_search as sem

    async def fake(query, category=None, top_k=5):
        return [{"file": "b.md", "section": "h2", "score": 0.9, "snippet": "snip"}]

    monkeypatch.setattr(sem, "semantic_search", fake)
    res = await sem.kb_semantic_search_handler(query="请假")
    assert res["count"] == 1


@pytest.mark.asyncio
async def test_kb_semantic_search_missing_query():
    from app.tools.kb_semantic_search import kb_semantic_search_handler

    res = await kb_semantic_search_handler()
    assert res["results"] == []
    assert "error" in res


# ─── kb_read_section ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_read_section_handler(monkeypatch):
    from app.tools import kb_read_section as rs

    async def fake(file, section, include_neighbors=True):
        return {
            "file": file,
            "section": section,
            "content": "## X\n正文",
            "neighbors": [],
        }

    monkeypatch.setattr(rs, "read_section", fake)
    res = await rs.kb_read_section_handler(file="a.md", section="X")
    assert res["section"] == "X"
    assert res["neighbors"] == []


@pytest.mark.asyncio
async def test_kb_read_section_missing_file():
    from app.tools.kb_read_section import kb_read_section_handler

    res = await kb_read_section_handler(section="x")
    assert "error" in res
    assert "file" in res["error"]


@pytest.mark.asyncio
async def test_kb_read_section_missing_section():
    from app.tools.kb_read_section import kb_read_section_handler

    res = await kb_read_section_handler(file="a.md")
    assert "error" in res
    assert "section" in res["error"]


# ─── ToolRegistry validate_params 校验 ───────────────────────────────────────


def test_kb_outline_schema_no_required():
    """kb_outline 不应有 required 参数, 这样 LLM 可以无参调用"""
    tool = tool_registry.get_tool("kb_outline")
    assert tool.json_schema.get("required", []) == []


def test_kb_keyword_search_required_query():
    tool = tool_registry.get_tool("kb_keyword_search")
    assert "query" in tool.json_schema.get("required", [])


def test_kb_read_section_required_file_section():
    tool = tool_registry.get_tool("kb_read_section")
    required = tool.json_schema.get("required", [])
    assert "file" in required
    assert "section" in required
