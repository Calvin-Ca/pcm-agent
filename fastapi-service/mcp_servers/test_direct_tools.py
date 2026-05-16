"""直接调用 kb_mcp_server 的工具函数（绕过 MCP 协议层）。"""
import asyncio
import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parent.parent
os.environ["KB_PATH"] = str(SERVICE_ROOT.parent / "knowledge-base")
os.environ["MILVUS_HOST"] = "172.19.3.136"
os.environ["MILVUS_PORT"] = "19530"
os.environ["CHAT_LLM_API_KEY"] = "EMPTY"
os.environ["CHAT_LLM_API_BASE"] = "http://172.19.3.136:8099/v1"
os.environ["PYTHONIOENCODING"] = "utf-8"

sys.path.insert(0, str(SERVICE_ROOT))

from mcp_servers.kb_mcp_server import (
    kb_keyword_search,
    kb_outline,
    kb_read_section,
    kb_semantic_search,
)


async def test():
    print("=" * 60)
    print("Step 1: kb_outline")
    print("=" * 60)
    result = await kb_outline()
    data = json.loads(result)
    docs = data.get("documents", [])
    print(f"[OK] Documents: {len(docs)}")

    print("\n" + "=" * 60)
    print("Step 2: kb_keyword_search (会触发 RAG 初始化，约 15-20 秒)")
    print("=" * 60)
    result = await kb_keyword_search(query="加班")
    data = json.loads(result)
    count = data.get("count", 0)
    status = data.get("status")
    print(f"[OK] Results: {count}, status={status}")
    for r in data.get("results", [])[:3]:
        score = r.get("score", 0) or 0
        print(f"  [{score:.3f}] {r.get('file', '?')[:50]}: {r.get('snippet', '')[:80]}...")

    print("\n" + "=" * 60)
    print("Step 3: kb_semantic_search (RAG 已初始化，应秒回)")
    print("=" * 60)
    result = await kb_semantic_search(query="病假和事假怎么区分")
    data = json.loads(result)
    count = data.get("count", 0)
    status = data.get("status")
    print(f"[OK] Results: {count}, status={status}")
    for r in data.get("results", [])[:3]:
        score = r.get("score", 0) or 0
        print(f"  [{score:.3f}] {r.get('file', '?')[:50]}: {r.get('snippet', '')[:80]}...")

    print("\n" + "=" * 60)
    print("Step 4: kb_read_section")
    print("=" * 60)
    result = await kb_read_section(
        file="01-工时管理/policy/工时填报管理制度.md",
        section="适用范围",
    )
    data = json.loads(result)
    content = data.get("content", "")
    print(f"[OK] Content length: {len(content)}")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


def test_phase2_shells_smoke():
    """Phase 2 薄壳 server 冒烟：仅验证能 import + mcp 对象构造 + tool 注册成功。

    薄壳 server 把调用转发到 ai-service /api/internal/tools/{name}，
    真实端到端转发依赖 ai-service(8000) 运行，不在本冒烟范围；
    这里只确认 5 个新 server 模块本身完好（import / FastMCP / @mcp.tool 注册）。
    """
    import importlib

    print("\n" + "=" * 60)
    print("Phase 2 薄壳 server 冒烟（import + mcp 对象 + tool 注册）")
    print("=" * 60)

    expected = {
        "project_mcp_server": ("workhour-project", "query_project"),
        "statistics_mcp_server": ("workhour-statistics", "compute_statistics"),
        "weekly_report_mcp_server": ("workhour-weekly-report", "generate_weekly_report"),
        "sql_query_mcp_server": ("workhour-sql-query", "sql_query"),
        "knowledge_qa_mcp_server": ("workhour-knowledge-qa", "knowledge_qa"),
    }

    for mod_name, (expected_mcp_name, tool_attr) in expected.items():
        mod = importlib.import_module(f"mcp_servers.{mod_name}")
        assert mod.mcp.name == expected_mcp_name, (
            f"{mod_name}: mcp.name={mod.mcp.name!r} != {expected_mcp_name!r}"
        )
        # @mcp.tool() 装饰后函数仍保留在模块命名空间，作为薄壳函数存在性校验
        assert hasattr(mod, tool_attr), f"{mod_name}: 缺少 tool 函数 {tool_attr}"
        tools = asyncio.run(mod.mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert tool_attr in tool_names, (
            f"{mod_name}: tool {tool_attr!r} 未在 mcp 注册，实际={tool_names}"
        )
        print(f"[OK] {mod_name}: mcp={mod.mcp.name}, tools={sorted(tool_names)}")

    print("\n" + "=" * 60)
    print("PHASE 2 SHELLS SMOKE PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_phase2_shells_smoke()
    asyncio.run(test())
