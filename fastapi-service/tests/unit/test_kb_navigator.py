"""
单元测试: app/services/kb_navigator.py

覆盖:
- get_outline: 目录不存在 / frontmatter 解析 / category 过滤
- read_section: 越权拒绝 / 章节存在与否 / 邻接章节
"""

import os
from pathlib import Path

import pytest

from app.services import kb_navigator
from app.services.kb_navigator import (
    get_outline,
    read_section,
    keyword_search,
    semantic_search,
    clear_outline_cache,
    _resolve_kb_root,
    _split_into_h2_sections,
    _parse_frontmatter,
)


@pytest.fixture
def isolated_kb(tmp_path, monkeypatch):
    """
    把 KB_PATH 指向 tmp_path, 隔离真实 knowledge-base 目录。
    每个测试结束后恢复 + 清空 outline 缓存。
    """
    monkeypatch.setenv("KB_PATH", str(tmp_path))
    clear_outline_cache()
    yield tmp_path
    clear_outline_cache()


# ─── _parse_frontmatter ───────────────────────────────────────────────────────


def test_parse_frontmatter_basic():
    text = """---
title: 测试文档
category: 工时管理
tags: [填报, 审核]
---

# 测试文档

正文内容。
"""
    fm, body = _parse_frontmatter(text)
    assert fm["title"] == "测试文档"
    assert fm["category"] == "工时管理"
    assert fm["tags"] == ["填报", "审核"]
    assert body.lstrip().startswith("# 测试文档")


def test_parse_frontmatter_absent():
    text = "# 无 frontmatter 文档\n\n正文。"
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert body == text


# ─── get_outline ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_outline_no_kb_dir(monkeypatch, tmp_path):
    """KB 目录不存在时返回 documents=[], 不抛异常"""
    nonexistent = tmp_path / "no-such-dir"
    monkeypatch.setenv("KB_PATH", str(nonexistent))
    clear_outline_cache()
    res = await get_outline()
    assert res == {"documents": []}


@pytest.mark.asyncio
async def test_get_outline_parses_frontmatter(isolated_kb):
    """能正确解析 frontmatter + h1/h2"""
    sub = isolated_kb / "01-工时管理" / "policy"
    sub.mkdir(parents=True)
    (sub / "test.md").write_text(
        """---
title: 工时填报制度
category: 工时管理
genre: policy
audience: all
acl: public
tags: [填报, 审核]
---

# 工时填报制度

## 适用范围

适用于全体员工。

## 核心规则

每天填报一次。
""",
        encoding="utf-8",
    )

    res = await get_outline()
    assert "documents" in res
    assert len(res["documents"]) == 1
    doc = res["documents"][0]
    assert doc["title"] == "工时填报制度"
    assert doc["category"] == "工时管理"
    assert doc["genre"] == "policy"
    assert doc["audience"] == "all"
    assert doc["acl"] == "public"
    assert doc["tags"] == ["填报", "审核"]
    assert "适用范围" in doc["h2"]
    assert "核心规则" in doc["h2"]


@pytest.mark.asyncio
async def test_get_outline_category_filter(isolated_kb):
    """category 过滤只保留匹配的文档"""
    (isolated_kb / "01-工时管理").mkdir()
    (isolated_kb / "01-工时管理" / "a.md").write_text(
        "---\ntitle: A\ncategory: 工时管理\n---\n\n# A\n\n## h2\ncontent",
        encoding="utf-8",
    )
    (isolated_kb / "02-假期与加班").mkdir()
    (isolated_kb / "02-假期与加班" / "b.md").write_text(
        "---\ntitle: B\ncategory: 假期与加班\n---\n\n# B\n\n## h2\ncontent",
        encoding="utf-8",
    )

    res = await get_outline(category="工时管理")
    titles = [d["title"] for d in res["documents"]]
    assert titles == ["A"]

    res_all = await get_outline(category="ALL")
    assert len(res_all["documents"]) == 2

    res_none = await get_outline()
    assert len(res_none["documents"]) == 2


@pytest.mark.asyncio
async def test_get_outline_no_frontmatter_falls_back(isolated_kb):
    """没有 frontmatter 时, title 取 h1, category 从目录推断"""
    sub = isolated_kb / "03-薪资福利"
    sub.mkdir()
    (sub / "salary.md").write_text(
        "# 薪资福利说明\n\n## 五险一金\n\n详细内容。\n",
        encoding="utf-8",
    )

    res = await get_outline()
    assert len(res["documents"]) == 1
    doc = res["documents"][0]
    assert doc["title"] == "薪资福利说明"
    assert doc["category"] == "薪资福利"
    assert doc["h2"] == ["五险一金"]


# ─── read_section ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_section_path_traversal_rejected(isolated_kb, tmp_path):
    """越权访问 (../etc/passwd) 必须被拒绝"""
    # 构造一个超出 kb_root 的目标文件
    outside = tmp_path.parent / "secret.md"
    outside.write_text("# secret\n\n## bad\nleak", encoding="utf-8")

    res = await read_section(file="../secret.md", section="bad")
    assert "error" in res
    assert "越权" in res["error"] or "不存在" in res["error"]


@pytest.mark.asyncio
async def test_read_section_file_not_found(isolated_kb):
    res = await read_section(file="missing.md", section="任意")
    assert "error" in res
    assert "不存在" in res["error"]


@pytest.mark.asyncio
async def test_read_section_section_not_found(isolated_kb):
    (isolated_kb / "doc.md").write_text(
        "# Doc\n\n## a\n内容a\n\n## b\n内容b\n",
        encoding="utf-8",
    )
    res = await read_section(file="doc.md", section="不存在的章节")
    assert "error" in res
    assert "available_sections" in res
    assert set(res["available_sections"]) == {"a", "b"}


@pytest.mark.asyncio
async def test_read_section_with_neighbors(isolated_kb):
    (isolated_kb / "doc.md").write_text(
        "# Doc\n\n## 一\n内容1\n\n## 二\n内容2\n\n## 三\n内容3\n",
        encoding="utf-8",
    )
    res = await read_section(file="doc.md", section="二", include_neighbors=True)
    assert res["section"] == "二"
    assert "内容2" in res["content"]
    sections = [n["section"] for n in res["neighbors"]]
    assert sections == ["一", "三"]


@pytest.mark.asyncio
async def test_read_section_without_neighbors(isolated_kb):
    (isolated_kb / "doc.md").write_text(
        "# Doc\n\n## 一\n内容1\n\n## 二\n内容2\n",
        encoding="utf-8",
    )
    res = await read_section(file="doc.md", section="一", include_neighbors=False)
    assert res["neighbors"] == []
    assert "内容1" in res["content"]


@pytest.mark.asyncio
async def test_read_section_missing_args(isolated_kb):
    res = await read_section(file="", section="x")
    assert "error" in res
    res2 = await read_section(file="x.md", section="")
    assert "error" in res2


# ─── keyword/semantic_search 在 RAG 未就绪时返回 [] ───────────────────────────


@pytest.mark.asyncio
async def test_keyword_search_returns_empty_when_rag_not_ready(monkeypatch):
    # 强制让 _rag_service 不存在或未初始化
    from app.services import langchain_rag as _lr
    monkeypatch.setattr(_lr, "_rag_service", None, raising=False)
    res = await keyword_search(query="加班")
    assert res == []


@pytest.mark.asyncio
async def test_semantic_search_returns_empty_when_rag_not_ready(monkeypatch):
    from app.services import langchain_rag as _lr
    monkeypatch.setattr(_lr, "_rag_service", None, raising=False)
    res = await semantic_search(query="加班")
    assert res == []


@pytest.mark.asyncio
async def test_keyword_search_empty_query():
    res = await keyword_search(query="")
    assert res == []


# ─── _split_into_h2_sections ──────────────────────────────────────────────────


def test_split_h2_sections_basic():
    body = "# title\n\n## A\n内容a行1\n内容a行2\n\n## B\n内容b\n"
    sections = _split_into_h2_sections(body)
    assert len(sections) == 2
    assert sections[0][0] == "A"
    assert "内容a行1" in sections[0][1]
    assert sections[1][0] == "B"


def test_split_h2_sections_empty():
    assert _split_into_h2_sections("# title\n\n正文没有 h2") == []
