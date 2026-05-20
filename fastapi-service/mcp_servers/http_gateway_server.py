"""
HTTP MCP 网关（方案 2）— 单一常驻 streamable-http MCP 服务，部署在 172。

开发者 .mcp.json 配 type:http + url + headers(X-Gateway-Token / X-Auth-Token /
X-User-ID / X-Entity-Type)，零本机环境。网关只做鉴权+header透传+转发同机
ai-service 内部端点；Phase 3 写白名单/dry_run强制/审计在 ai-service 端生效。

二段确认（save_workhour）：confirm=False(默认)→dry_run=true 预览不写库；
用户明确同意后 confirm=True→dry_run=false 真写。不接受任意 user_id（杜绝跨人写）。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "http_gateway_server.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-gateway")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_servers._gateway_core import (
    GatewayAuthMiddleware,
    forward_to_ai_service,
)

mcp = FastMCP(
    "workhour-gateway",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _save_workhour_impl(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """内部实现，便于单测；confirm→dry_run 映射，不含任意 user_id。"""
    params = {
        "project_id": project_id,
        "date": date,
        "duration": duration,
        "description": description,
        "dry_run": not confirm,
    }
    result = await forward_to_ai_service("save_workhour", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def save_workhour(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """填报单条工时（二段确认）。

    1. 首次 confirm=False（默认）→ 返回预览，不写库。把 preview 原样给用户。
    2. 用户明确同意后，相同参数 + confirm=True 再调一次才真写。
    只为本请求 X-Entity-ID 经 Service Account 换取的身份填报，不接受代填他人。

    Args:
        project_id: 项目名称或 ID（系统解析）
        date: 工时日期 YYYY-MM-DD
        duration: 工时（小时），0.5 的整数倍，0.5~10
        description: 工作内容（可选）
        confirm: False=预览（默认）；True=确认写入
    """
    logger.info(f"save_workhour confirm={confirm!r} project={project_id!r}")
    try:
        return await _save_workhour_impl(
            project_id, date, duration, description, confirm
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {e}"}, ensure_ascii=False)


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


async def _query_timesheet_impl(member_id=None, project_id=None,
                                start_date=None, end_date=None) -> str:
    params = _drop_none({
        "user_id": member_id, "project_id": project_id,
        "start_date": start_date, "end_date": end_date,
    })
    return json.dumps(await forward_to_ai_service("query_timesheet", params),
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def query_timesheet(member_id: str = "", project_id: str = "",
                          start_date: str = "", end_date: str = "") -> str:
    """查询工时填报记录（按人/项目/时间范围；不传则当前用户近30天）。

    Args:
        member_id: 成员 ID，空=当前用户
        project_id: 项目 ID，空=全部项目
        start_date: 开始日期 YYYY-MM-DD，空=自动近30天
        end_date: 结束日期 YYYY-MM-DD，空=今天
    """
    return await _query_timesheet_impl(
        member_id or None, project_id or None,
        start_date or None, end_date or None,
    )


async def _query_project_impl(keyword=None, project_id=None) -> str:
    params = _drop_none({"keyword": keyword, "project_id": project_id})
    return json.dumps(await forward_to_ai_service("query_project", params),
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def query_project(keyword: str = "", project_id: str = "") -> str:
    """查询项目信息。

    Args:
        keyword: 项目名关键词，空=不按名筛
        project_id: 项目 ID，空=不按 ID 筛
    """
    return await _query_project_impl(keyword or None, project_id or None)


async def _compute_statistics_impl(scope=None, start_date=None,
                                   end_date=None, group_by=None) -> str:
    params = _drop_none({"scope": scope, "start_date": start_date,
                         "end_date": end_date, "group_by": group_by})
    return json.dumps(await forward_to_ai_service("compute_statistics", params),
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def compute_statistics(scope: str = "", start_date: str = "",
                             end_date: str = "", group_by: str = "") -> str:
    """工时统计分析（汇总/分组）。

    Args:
        scope: 统计范围（如 self/department），空=默认
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        group_by: 分组维度（如 project/member），空=默认
    """
    return await _compute_statistics_impl(
        scope or None, start_date or None, end_date or None, group_by or None
    )


async def _generate_weekly_report_impl(start_date=None, end_date=None) -> str:
    params = _drop_none({"start_date": start_date, "end_date": end_date})
    return json.dumps(
        await forward_to_ai_service("generate_weekly_report", params),
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
async def generate_weekly_report(start_date: str = "", end_date: str = "") -> str:
    """生成周报（基于本人工时）。

    Args:
        start_date: 周起始 YYYY-MM-DD，空=本周一
        end_date: 周结束 YYYY-MM-DD，空=今天
    """
    return await _generate_weekly_report_impl(
        start_date or None, end_date or None
    )


async def _sql_query_impl(question: str) -> str:
    return json.dumps(
        await forward_to_ai_service("sql_query", {"question": question}),
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
async def sql_query(question: str) -> str:
    """自然语言查询数据库（复杂分析场景）。

    Args:
        question: 自然语言问题，由 ai-service 转 SQL 执行
    """
    return await _sql_query_impl(question)


async def _kb_forward(tool: str, params: dict) -> str:
    return json.dumps(await forward_to_ai_service(tool, params),
                      ensure_ascii=False, indent=2)


@mcp.tool()
async def kb_outline(category: str = "") -> str:
    """知识库目录大纲（标题+h2+metadata），问题模糊时先看全貌。

    Args:
        category: 限定主题域，空=全部
    """
    return await _kb_forward("kb_outline", _drop_none({"category": category or None}))


@mcp.tool()
async def kb_keyword_search(query: str, category: str = "",
                            top_k: int = 5) -> str:
    """BM25 关键词检索（精确术语/编号/数字）。

    Args:
        query: 关键词
        category: 限定主题域，空=全部
        top_k: 返回数 1~20
    """
    return await _kb_forward("kb_keyword_search", _drop_none(
        {"query": query, "category": category or None, "top_k": top_k}))


@mcp.tool()
async def kb_semantic_search(query: str, category: str = "",
                             top_k: int = 5) -> str:
    """向量语义检索（自然语言/近义概念）。

    Args:
        query: 自然语言查询
        category: 限定主题域，空=全部
        top_k: 返回数 1~20
    """
    return await _kb_forward("kb_semantic_search", _drop_none(
        {"query": query, "category": category or None, "top_k": top_k}))


@mcp.tool()
async def kb_read_section(file: str, section: str,
                          include_neighbors: bool = True) -> str:
    """精读指定文档某 h2 章节（含前后相邻章节）。

    Args:
        file: 文档相对路径
        section: h2 章节标题
        include_neighbors: 是否附带相邻章节
    """
    return await _kb_forward("kb_read_section", {
        "file": file, "section": section,
        "include_neighbors": include_neighbors,
    })


async def _list_tool_names_async() -> list[str]:
    tools = await mcp.list_tools()
    return [t.name for t in tools]


def list_tool_names() -> list[str]:
    """已注册 MCP 工具名（供自检/测试）。"""
    import asyncio
    return asyncio.run(_list_tool_names_async())


async def _health(request):
    return PlainTextResponse("ok")


def build_app():
    """组装 ASGI app：streamable_http_app + 健康路由 + 鉴权中间件。"""
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health/health", _health))
    app.add_middleware(GatewayAuthMiddleware)
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting HTTP MCP gateway on 0.0.0.0:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765)
