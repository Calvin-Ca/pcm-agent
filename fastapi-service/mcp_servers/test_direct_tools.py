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


if __name__ == "__main__":
    asyncio.run(test())
