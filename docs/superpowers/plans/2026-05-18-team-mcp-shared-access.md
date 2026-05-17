# 团队开发者 MCP 共享接入（方案 2）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 172 新增一个常驻 HTTP MCP 网关：开发者 `.mcp.json` 配 `type:http` + 三个 header 即可零本机环境用全部 MCP 工具，网关只做鉴权+header透传+转发同机 ai-service 内部端点（Phase 3 闸门全保留）。

**Architecture:** FastMCP `streamable_http_app()` 返回 Starlette ASGI app，用一个 Starlette `BaseHTTPMiddleware` 包裹它：校验 `X-Gateway-Token`（失败 401），把 `X-Auth-Token/X-User-ID/X-Entity-Type` 存进 contextvar；每个 MCP 工具是一个转发壳，读 contextvar → httpx POST `http://ai-service:8000/api/internal/tools/{tool}`。

**Tech Stack:** mcp 1.27.0（FastMCP）、starlette 1.0.0、uvicorn 0.44.0、httpx、pytest。测试解释器固定 `./fastapi-service/.venv/Scripts/python.exe`，命令在 `fastapi-service/` 下执行。

**依据 spec:** `docs/superpowers/specs/2026-05-18-team-mcp-shared-access-design.md`。

**环境约定:** 后端改完不执行 mvn/编译。`git add` 只加本任务列出的文件，绝不 `git add -A`/`.`（仓库有既存噪声 .claude/scheduled_tasks.lock、CLAUDE.md、benchmark CSV，勿动）。docker-compose 加 service 属生产动作，列在末尾「部署章节」，**不是自动化 Task**，须用户在场。

---

### Task 1: 技术 spike — 验证 Starlette 中间件能拦 streamable_http_app 的请求头

**目的:** spec §5.2 标注的实施前必验技术前提。证明「FastMCP streamable_http_app() 外面包一层 Starlette 中间件，能读到每请求自定义 header 并能在鉴权失败时 401 短路」。这是整个网关的地基，先证伪/证实再建。

**Files:**
- Test: `fastapi-service/tests/test_gateway_spike_header_access.py`

- [ ] **Step 1: 写 spike 测试**

创建 `fastapi-service/tests/test_gateway_spike_header_access.py`：

```python
"""SPIKE：验证 FastMCP streamable_http_app() 可被 Starlette 中间件包裹并读取每请求 header。

这是方案 2 的地基技术前提（spec §5.2）。只验证机制，不依赖 MCP 协议握手：
断言 (1) streamable_http_app() 是个 ASGI app；(2) BaseHTTPMiddleware 能在
请求进入 MCP app 前读到自定义 header；(3) 能据此返回 401 短路。
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from mcp.server.fastmcp import FastMCP


def _build_app():
    mcp = FastMCP("spike")

    @mcp.tool()
    async def ping() -> str:
        return "pong"

    app = mcp.streamable_http_app()

    seen = {}

    class GateMW(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            seen["gw"] = request.headers.get("X-Gateway-Token")
            if seen["gw"] != "secret":
                return PlainTextResponse("forbidden", status_code=401)
            return await call_next(request)

    app.add_middleware(GateMW)
    return app, seen


def test_streamable_http_app_is_asgi():
    app, _ = _build_app()
    assert callable(app)  # ASGI app


def test_middleware_sees_header_and_blocks_without_token():
    app, seen = _build_app()
    client = TestClient(app)
    r = client.get("/", headers={})  # 无 token
    assert r.status_code == 401
    assert seen["gw"] is None


def test_middleware_passes_with_correct_token():
    app, seen = _build_app()
    client = TestClient(app)
    # 带正确网关 token：中间件放行（放行后 MCP app 自身可能因非法 MCP 请求返回
    # 4xx，但绝不是中间件的 401；我们只断言 token 被读到且未被中间件拦）
    client.get("/", headers={"X-Gateway-Token": "secret"})
    assert seen["gw"] == "secret"
```

- [ ] **Step 2: 运行 spike**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gateway_spike_header_access.py -q`
Expected: 3 passed。

- [ ] **Step 3: 判定与记录**

- 若 3 passed → 机制证实，主路径成立，继续 Task 2（中间件方案）。
- 若任何 fail（如 `streamable_http_app` 不是可被 `add_middleware` 的 Starlette app，或中间件读不到 header）→ **停止，报告 BLOCKED**，附完整报错。回退预案：改用 `mcp.streamable_http_app()` 返回对象的 `.add_middleware` 不可用时，用 `starlette.applications.Starlette(routes=[Mount("/", app=mcp.streamable_http_app())])` 外层包裹再 `add_middleware`；仍不行则升级模型重评估。不要带着坏地基往下建。

- [ ] **Step 4: 提交**

```bash
git add fastapi-service/tests/test_gateway_spike_header_access.py
git commit -m "test(gateway): spike 验证 streamable_http_app 可被中间件拦截读header (方案2地基)"
```

---

### Task 2: 网关身份核心模块（contextvar + 鉴权中间件 + 转发助手）

**Files:**
- Create: `fastapi-service/mcp_servers/_gateway_core.py`
- Test: `fastapi-service/tests/test_gateway_core.py`

职责单一：这个文件只管「鉴权 + 身份上下文 + 转发到 ai-service」，不含 FastMCP/工具定义（在 Task 3）。

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_gateway_core.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gateway_core.py -q`
Expected: FAIL（`ModuleNotFoundError: mcp_servers._gateway_core`）

- [ ] **Step 3: 实现 `_gateway_core.py`**

创建 `fastapi-service/mcp_servers/_gateway_core.py`：

```python
"""
网关核心：鉴权中间件 + 每请求身份 contextvar + 转发到同机 ai-service。

设计（spec §5.2/5.3/5.5）：
    - GatewayAuthMiddleware：校验 X-Gateway-Token（健康检查路径放行），
      把 X-Auth-Token/X-User-ID/X-Entity-Type 存进 contextvar。
    - forward_to_ai_service：读 contextvar 身份 → httpx POST ai-service
      内部端点（Phase 3 白名单/dry_run/审计在 ai-service 端生效，不绕过）。
铁律：绝不记录 X-Auth-Token / X-Gateway-Token。
"""

from __future__ import annotations

import contextvars
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp-gateway")

GATEWAY_TOKEN = os.getenv("MCP_GATEWAY_TOKEN", "")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://ai-service:8000")

# 健康检查路径前缀放行（不需网关 token，供 compose healthcheck）
_HEALTH_PREFIXES = ("/health",)


@dataclass
class Identity:
    user_id: str = ""
    entity_type: str = "employee"
    auth_token: str = ""


_IDENTITY: contextvars.ContextVar[Identity] = contextvars.ContextVar(
    "mcp_gateway_identity", default=Identity()
)


def get_identity() -> Identity:
    return _IDENTITY.get()


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _HEALTH_PREFIXES):
            return await call_next(request)

        supplied = request.headers.get("X-Gateway-Token", "")
        if not GATEWAY_TOKEN or supplied != GATEWAY_TOKEN:
            # 不回显任何 token；只说明缺哪个头
            return JSONResponse(
                {"error": "missing or invalid X-Gateway-Token"},
                status_code=401,
            )

        ident = Identity(
            user_id=request.headers.get("X-User-ID", ""),
            entity_type=request.headers.get("X-Entity-Type", "employee"),
            auth_token=request.headers.get("X-Auth-Token", ""),
        )
        token = _IDENTITY.set(ident)
        try:
            return await call_next(request)
        finally:
            _IDENTITY.reset(token)


async def forward_to_ai_service(
    tool_name: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """转发到同机 ai-service 内部端点，带 contextvar 里的身份头。"""
    ident = get_identity()
    url = f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}"
    logger.info(f"[gateway] forward tool={tool_name} user={ident.user_id}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            json=params,
            headers={
                "X-User-ID": ident.user_id,
                "X-Entity-Type": ident.entity_type,
                "X-Auth-Token": ident.auth_token,
            },
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gateway_core.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add fastapi-service/mcp_servers/_gateway_core.py fastapi-service/tests/test_gateway_core.py
git commit -m "feat(gateway): 鉴权中间件+身份contextvar+转发助手 (方案2核心)"
```

---

### Task 3: 网关 server（FastMCP 实例 + save_workhour 转发工具 + app 装配）

**Files:**
- Create: `fastapi-service/mcp_servers/http_gateway_server.py`
- Test: `fastapi-service/tests/test_http_gateway_server.py`

先打通端到端一个工具（save_workhour，写路径最关键，验证 confirm→dry_run 经网关到 ai-service 闸门），其余只读工具在 Task 4 同范式补齐。

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_http_gateway_server.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_gateway_server.py -q`
Expected: FAIL（`ModuleNotFoundError: mcp_servers.http_gateway_server`）

- [ ] **Step 3: 实现 `http_gateway_server.py`**

创建 `fastapi-service/mcp_servers/http_gateway_server.py`：

```python
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
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_servers._gateway_core import (
    GatewayAuthMiddleware,
    forward_to_ai_service,
)

mcp = FastMCP("workhour-gateway")


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
    只为当前请求身份（X-Auth-Token 对应的人）填报，不接受代填他人。

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
```

> 注：`build_app()` 把 `/health/health` 路由 append 进 streamable_http_app 的 router，再挂中间件（中间件已对 `/health` 前缀放行，见 Task 2）。`@mcp.tool()` 装饰器在 import 时注册工具，`_save_workhour_impl` 抽出便于不走 MCP 协议直接单测。

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_gateway_server.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归核心**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gateway_spike_header_access.py tests/test_gateway_core.py tests/test_http_gateway_server.py -q`
Expected: PASS（11 passed）

- [ ] **Step 6: 提交**

```bash
git add fastapi-service/mcp_servers/http_gateway_server.py fastapi-service/tests/test_http_gateway_server.py
git commit -m "feat(gateway): FastMCP网关+save_workhour二段确认转发壳+app装配 (方案2)"
```

---

### Task 4: 补齐其余只读转发工具（query_timesheet / query_project / compute_statistics / generate_weekly_report / sql_query / 4 个 kb_*）

沿用 Task 3 已验证的 `forward_to_ai_service` 范式，每个工具一个具名转发壳。schema 由函数签名+docstring 生成，签名照搬现有对应 `*_mcp_server.py`（已知，不臆造）。

**Files:**
- Modify: `fastapi-service/mcp_servers/http_gateway_server.py`
- Test: `fastapi-service/tests/test_http_gateway_server.py`

- [ ] **Step 1: 追加失败测试**

在 `fastapi-service/tests/test_http_gateway_server.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_gateway_server.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute '_query_timesheet_impl'` / `list_tool_names`）

- [ ] **Step 3: 追加转发壳与工具登记辅助**

在 `http_gateway_server.py` 的 `save_workhour` 工具之后、`_health` 之前，追加：

```python
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


def list_tool_names() -> list[str]:
    """已注册 MCP 工具名（供自检/测试）。"""
    import asyncio
    tools = asyncio.get_event_loop().run_until_complete(mcp.list_tools())
    return [t.name for t in tools]
```

> 注：`sql_query` 经网关转发，由 ai-service 容器执行（其生产环境本就连 DB），spec §7 例外消失，与其他工具同等。`kb_*` 同样走转发到 ai-service 内部端点（ai-service 已注册 kb_* 工具，Phase 1）。`list_tool_names` 仅供测试自检；若 `mcp.list_tools()` 在无事件循环时报错，测试里改用 `asyncio.run(m._list_tool_names_async())` —— 见下方备选。

若 `list_tool_names()` 因事件循环问题失败，改为在模块内提供 async 版并让测试用 `asyncio.run`：

```python
async def _list_tool_names_async() -> list[str]:
    tools = await mcp.list_tools()
    return [t.name for t in tools]
```

并把 `test_all_expected_tools_registered` 改为：

```python
def test_all_expected_tools_registered():
    import asyncio
    m = _mod()
    names = set(asyncio.run(m._list_tool_names_async()))
    assert names == {
        "save_workhour", "query_timesheet", "query_project",
        "compute_statistics", "generate_weekly_report", "sql_query",
        "kb_outline", "kb_keyword_search", "kb_semantic_search",
        "kb_read_section",
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_gateway_server.py -q`
Expected: PASS（6 passed）。若 `test_all_expected_tools_registered` 因事件循环失败，按 Step 3 备选改 async 版后再跑至 PASS。

- [ ] **Step 5: 全量回归**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gateway_spike_header_access.py tests/test_gateway_core.py tests/test_http_gateway_server.py -q`
Expected: PASS（14 passed）

- [ ] **Step 6: 提交**

```bash
git add fastapi-service/mcp_servers/http_gateway_server.py fastapi-service/tests/test_http_gateway_server.py
git commit -m "feat(gateway): 补齐只读+sql+kb 共9个转发工具 (方案2全工具)"
```

---

### Task 5: 开发者接入文档（远程 HTTP 段）

**Files:**
- Modify: `docs/mcp-usage.md`

- [ ] **Step 1: 追加「远程 HTTP 接入（方案 2）」章节**

在 `docs/mcp-usage.md` 末尾追加一节，内容含：

```markdown
## 9. 远程 HTTP 接入（方案 2，推荐给团队开发者）

无需本机仓库/venv/起 ai-service。前提：外网开发者先连公司 VPN（连上即内网，可达 172）。

`.mcp.json` 配置（替换原 stdio 条目，或新增）：

\`\`\`json
"workhour-gateway": {
  "type": "http",
  "url": "http://172.19.3.136:8765/mcp",
  "headers": {
    "X-Gateway-Token": "<向管理员索取的网关 token>",
    "X-Auth-Token": "<你自己的 SpringBoot JWT，见 §4>",
    "X-User-ID": "<你的用户 ID>",
    "X-Entity-Type": "employee"
  }
}
\`\`\`

- 改完重启 MCP 客户端（重开会话）生效。
- `X-Gateway-Token` 向管理员索取，不入 git。
- `X-Auth-Token` 是你自己的 JWT（§4 方式获取，过期重取）；写工时的真实权限由它决定。
- save_workhour 仍二段确认：先 confirm=False 看预览，确认无误再 confirm=True。
- 排错：401 → 网关 token 缺/错；连接超时 → 未连 VPN 或网关未起；写 401 → JWT 过期。
```

（实际写入时把 `\`\`\`` 还原为三反引号代码块。）

- [ ] **Step 2: 校验 JSON 示例合法**

Run（在 `ai-service/`）: `./fastapi-service/.venv/Scripts/python.exe -c "import json,re,io; s=open('docs/mcp-usage.md',encoding='utf-8').read(); blocks=re.findall(r'\`\`\`json\n(.*?)\`\`\`', s, re.S); [json.loads('{'+b.split('{',1)[1].rsplit('}',1)[0]+'}') for b in blocks if 'workhour-gateway' in b]; print('json ok')"`
Expected: `json ok`（仅校验新增网关示例片段语法）

- [ ] **Step 3: 提交**

```bash
git add docs/mcp-usage.md
git commit -m "docs(mcp): 远程 HTTP 接入(方案2)开发者配置说明"
```

---

## 部署章节（**非自动化 Task**，生产动作，须用户在场分步执行）

实施完成且全部单测绿后，由用户在场执行以下 172 部署（规则 7，不在无人值守批）：

1. **生成网关 token**：`python -c "import secrets; print(secrets.token_urlsafe(32))"`，记录到安全位置（不入 git）。
2. **docker-compose.yml 加 service**（在 `ai-service` service 之后、`networks:` 之前）。沿用 ai-service 的 build/volumes，改 command 与端口绑定到 172 内网 IP：

```yaml
  mcp-gateway:
    build:
      context: ./fastapi-service
      dockerfile: Dockerfile
    container_name: ai-assistant-mcp-gateway
    depends_on:
      - ai-service
    env_file:
      - .env
    environment:
      - AI_SERVICE_URL=http://ai-service:8000
      - MCP_GATEWAY_TOKEN=${MCP_GATEWAY_TOKEN}
    ports:
      - "172.19.3.136:8765:8765"
    volumes:
      - ./fastapi-service:/app
      - ./prompts:/app/prompts
      - ./knowledge-base:/app/knowledge-base
      - ai-service-logs:/app/logs
    command: python mcp_servers/http_gateway_server.py
    networks:
      - ai-network
```

3. **172 `.env` 增** `MCP_GATEWAY_TOKEN=<第1步生成值>`（172 上操作，不改本仓库 .env）。
4. **起服务（仅新 service，不扰其余容器）**：
   `ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d mcp-gateway"`
5. **核验**：宿主 `nc -zv 172.19.3.136 8765`；`curl -s -o /dev/null -w '%{http_code}' http://172.19.3.136:8765/health/health` → 200；无 token 打 `/mcp` → 401。
6. **真实 e2e（用户在场）**：开发者机 VPN 后配 .mcp.json → Claude Code 实连 → query_timesheet 往返；save_workhour confirm=False 预览→confirm=True 写库可查；ai-service 审计日志含 attempt/result、无 token。属写生产库，须用户在场授权。

---

## Self-Review（对照 spec）

- **Spec 覆盖**：§2.1 物理约束→Task1 spike 选定中间件主路径；§4 架构→Task2(中间件/contextvar/转发)+Task3(装配)；§5.1 单 server 全工具→Task3+4；§5.2 身份 header 透传→Task2 `GatewayAuthMiddleware`+`Identity`；§5.3 双层鉴权→Task2 网关 token + 转发带 JWT（业务层在 ai-service/SpringBoot）；§5.4 Compose→部署章节；§5.5 复用 Phase3 闸门→转发到 `/api/internal/tools/*`（Task2 `forward_to_ai_service`）；§7 sql_query 例外消失→Task4 sql_query 走转发；§8 错误处理→401/转发异常/health；§9 测试→各 Task TDD+回归；§10 部署前提→部署章节；§12 方案3→spec 已记录，本计划非目标（部署章节仅方案2）。覆盖完整。
- **占位符**：无 TBD/TODO；每个改码步骤给完整代码；`list_tool_names` 事件循环风险已给具体备选实现，非占位。
- **类型一致**：`Identity`/`get_identity`/`_IDENTITY`/`GatewayAuthMiddleware`/`forward_to_ai_service` 在 Task2 定义，Task3/4 import 一致；`forward_to_ai_service(tool_name, params)->dict` 签名贯穿一致；`_save_workhour_impl`/`_query_timesheet_impl` 等 impl 命名与测试一致；header 名 `X-User-ID/X-Entity-Type/X-Auth-Token` 与 ai-service Phase3 internal_tools 端点 alias 一致。
- **既有影响**：新增文件为主，不动现有 stdio server（spec §11 非目标：保留 stdio）；docker-compose 改动隔离在部署章节非自动化。
