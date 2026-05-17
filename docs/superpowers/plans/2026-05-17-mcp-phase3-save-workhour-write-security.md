# MCP Phase 3（save_workhour 写工具）安全落地 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `save_workhour` 经 MCP 安全暴露：端点级写白名单+dry_run强制+结构化审计，save_workhour 域内 dry_run 预览语义，新建二段确认 MCP server。

**Architecture:** Approach C（混合）。横切关注点（白名单/dry_run强制/审计）在 `internal_tools.py` chokepoint；dry_run 预览语义在 `save_workhour.py`；`save_workhour_mcp_server.py` 默认 dry_run + confirm 二段。真实 authz 权威闸门仍是 SpringBoot JWT，不在端点新增 PermissionValidator。

**Tech Stack:** FastAPI、pytest、Pydantic v1 validator、mcp FastMCP、httpx。测试 Python 解释器固定 `./fastapi-service/.venv/Scripts/python.exe`。

**依据 spec:** `docs/superpowers/specs/2026-05-17-mcp-phase3-save-workhour-write-security-design.md`（缺口编号 G1-G5 贯穿）。

**环境约定:** 后端改完不执行 mvn/编译；Python 侧不需要编译。所有命令在 `fastapi-service/` 目录下用 `.venv` 解释器。

---

### Task 1: tool_registry 写分类（is_write）

**Files:**
- Modify: `fastapi-service/app/models/tool.py:47`
- Modify: `fastapi-service/app/services/tool_registry.py:108-169`
- Test: `fastapi-service/tests/test_tool_registry_is_write.py`

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_tool_registry_is_write.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_tool_registry_is_write.py -q`
Expected: FAIL（`TypeError: register_tool() got unexpected keyword 'is_write'` 或 `AttributeError: 'ToolRegistry' object has no attribute 'is_write_tool'`）

- [ ] **Step 3: ToolDefinition 加字段**

`fastapi-service/app/models/tool.py`，在第 47 行 `requires_permission` 之后新增一行：

```python
    requires_permission: bool = Field(default=True, description="是否需要权限验证")
    is_write: bool = Field(default=False, description="是否为写操作工具（MCP 写白名单/审计据此区分）")
```

- [ ] **Step 4: register_tool 加参数 + is_write_tool 方法**

`fastapi-service/app/services/tool_registry.py`，`register_tool` 签名（第 116 行）改为：

```python
        timeout: int = 30,
        requires_permission: bool = True,
        is_write: bool = False
    ) -> ToolDefinition:
```

`ToolDefinition(...)` 构造（第 151-160 行）加 `is_write=is_write`：

```python
            tool_def = ToolDefinition(
                name=name,
                description=description,
                category=category,
                json_schema=json_schema,
                timeout=timeout,
                requires_permission=requires_permission,
                is_write=is_write,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
```

在 `get_handler`（第 182 行）方法之后新增方法：

```python
    def is_write_tool(self, name: str) -> bool:
        """该工具是否为写操作工具（未注册视为 False）。"""
        tool = self._tools.get(name)
        return bool(tool.is_write) if tool else False
```

- [ ] **Step 5: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_tool_registry_is_write.py -q`
Expected: PASS（3 passed）

- [ ] **Step 6: 提交**

```bash
git add fastapi-service/app/models/tool.py fastapi-service/app/services/tool_registry.py fastapi-service/tests/test_tool_registry_is_write.py
git commit -m "feat(registry): 工具 is_write 写分类 (MCP Phase 3 G5 基础)"
```

---

### Task 2: save_workhour dry_run 语义 + is_write 注册

**Files:**
- Modify: `fastapi-service/app/tools/save_workhour.py`（schema 第 49-56 行附近、handler 第 142-214 行、register 第 312-320 行）
- Test: `fastapi-service/tests/test_save_workhour_dry_run.py`

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_save_workhour_dry_run.py`：

```python
"""save_workhour dry_run：预览不写库（MCP Phase 3 G2）。"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.save_workhour import save_workhour_handler

pytestmark = pytest.mark.asyncio


async def _resolvers_ok():
    """patch 项目/工作日历/工种解析为确定值，隔离外部依赖。"""
    return patch.multiple(
        "app.tools.save_workhour",
        resolve_project_id=AsyncMock(return_value=("123", None)),
        _get_workhour_type_for_date=AsyncMock(return_value="正常工时"),
        resolve_work_type=AsyncMock(return_value="开发"),
    )


async def test_dry_run_true_does_not_post():
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            r = await save_workhour_handler(
                project_id="AI平台", date="2026-05-10", duration=8,
                description="开发", dry_run=True, auth_token="t",
            )
    assert r["success"] is True
    assert r["dry_run"] is True
    assert r["preview"]["payload"]["projectId"] == "123"
    assert r["preview"]["payload"]["workhour"] == 8
    mock_post.assert_not_called()


async def test_dry_run_false_attempts_post():
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=RuntimeError("posted"))) as mock_post:
            await save_workhour_handler(
                project_id="AI平台", date="2026-05-10", duration=8,
                dry_run=False, auth_token="t",
            )
    mock_post.assert_called()


async def test_dry_run_validation_fail_no_post():
    """未来日期触发 _validate_date 失败：dry_run 下校验失败也不 POST。"""
    with await _resolvers_ok():
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            r = await save_workhour_handler(
                project_id="AI平台", date="2099-01-01", duration=8,
                dry_run=True, auth_token="t",
            )
    assert r["success"] is False
    mock_post.assert_not_called()
```

> 注：第三个测试用未来日期 `2099-01-01` 触发 `_validate_date` 失败，验证 dry_run 下校验失败也不 POST。

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_save_workhour_dry_run.py -q`
Expected: FAIL（`KeyError: 'dry_run'` —— handler 尚无 dry_run 分支，返回结构无 `dry_run`/`preview`）

- [ ] **Step 3: schema 增 dry_run**

`fastapi-service/app/tools/save_workhour.py`，`SAVE_WORKHOUR_SCHEMA.properties` 内 `user_id` 之后新增：

```python
        "user_id": {
            "type": "string",
            "description": "用户ID（可选，不填则使用当前登录用户）",
        },
        "dry_run": {
            "type": "boolean",
            "description": "true=仅预览校验不写入；缺省由调用环境决定（MCP 写工具端点会强制默认 true）",
        },
```

- [ ] **Step 4: handler 加 dry_run 分支**

`fastapi-service/app/tools/save_workhour.py`，`save_workhour_handler` 内取参处（第 157 行 `user_id` 之后）新增：

```python
    user_id: Optional[str] = kwargs.get("user_id")
    dry_run: bool = bool(kwargs.get("dry_run", False))
```

在构建 `payload` 之后、`try:`（原第 215 行）之前插入 dry_run 短路：

```python
    if description:
        payload["workContent"] = description
    if user_id:
        payload["memberId"] = user_id

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "preview": {
                "payload": payload,
                "summary": (
                    f"预览（未写入）：{date_str} {duration}h，"
                    f"项目 {project_id}，类别 {workhour_type}/{resolved_work_type}"
                ),
            },
            "message": "以上为预览，确认无误后再提交。",
        }

    try:
        url = f"{base_url}/api/workhour"
```

> 校验/解析失败分支（基础校验、`_validate_date`、`_validate_duration`、`resolve_project_id`）在 payload 构建之前，已 `return {"success": False, ...}`，dry_run 下天然不 POST，无需额外改动。

- [ ] **Step 5: register 置 is_write=True**

`fastapi-service/app/tools/save_workhour.py`，`register_save_workhour_tool` 内 `tool_registry.register_tool(...)` 调用增参：

```python
            category=ToolCategory.WORKHOUR,
            timeout=30,
            requires_permission=True,
            is_write=True,
        )
```

- [ ] **Step 6: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_save_workhour_dry_run.py -q`
Expected: PASS（3 passed）

- [ ] **Step 7: 提交**

```bash
git add fastapi-service/app/tools/save_workhour.py fastapi-service/tests/test_save_workhour_dry_run.py
git commit -m "feat(save_workhour): dry_run 预览语义 + is_write 注册 (MCP Phase 3 G2)"
```

---

### Task 3: internal_tools 端点闸门（白名单 + dry_run 强制 + 审计）

**Files:**
- Modify: `fastapi-service/app/api/internal_tools.py`（全文重写 handler，新增审计与闸门）
- Test: `fastapi-service/tests/test_internal_tools_write_gate.py`

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_internal_tools_write_gate.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_internal_tools_write_gate.py -q`
Expected: FAIL（`AttributeError: module 'app.api.internal_tools' has no attribute 'INTERNAL_WRITE_TOOLS'`，且写工具未拦截）

- [ ] **Step 3: 重写 internal_tools.py**

`fastapi-service/app/api/internal_tools.py` 全文替换为：

```python
"""
内部工具接口 — 仅供 MCP server / 内部脚本调用，不对外暴露。

注意：生产环境必须用 nginx 限制源 IP，禁止公网直接访问。
写工具额外受：写白名单 + dry_run 强制默认 + 结构化审计 约束（MCP Phase 3）。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("audit")

router = APIRouter(prefix="/api/internal/tools", tags=["internal"])

# 允许经内部端点调用的写工具白名单。新增写工具必须显式加入（G5）。
INTERNAL_WRITE_TOOLS = {"save_workhour"}

# 审计仅记录的业务参数字段白名单，绝不含 auth_token / user_context（G3）。
_AUDIT_PARAM_FIELDS = ("project_id", "date", "duration", "description", "user_id")


def _emit_audit(
    tool_name: str,
    phase: str,
    x_user_id: str,
    x_entity_type: str,
    params: Dict[str, Any],
    dry_run: Optional[bool] = None,
    success: Optional[bool] = None,
    error: Optional[str] = None,
    record_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """落一条结构化审计行。失败不得阻断主流程。"""
    try:
        safe_params = {k: params.get(k) for k in _AUDIT_PARAM_FIELDS if k in params}
        _audit_logger.info(json.dumps({
            "tag": "AUDIT",
            "ts": datetime.now().isoformat(),
            "tool": tool_name,
            "phase": phase,
            "user_id": x_user_id,
            "entity_type": x_entity_type,
            "dry_run": dry_run,
            "params": safe_params,
            "success": success,
            "error": error,
            "record_id": record_id,
            "reason": reason,
        }, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[audit] emit failed (non-blocking): {e}")


@router.post("/{tool_name}")
async def call_internal_tool(
    tool_name: str,
    params: Dict[str, Any],
    x_user_id: str = Header(..., alias="X-User-ID"),
    x_entity_type: str = Header(..., alias="X-Entity-Type"),
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
) -> Dict[str, Any]:
    """转发工具调用到 ToolRegistry，带身份头。

    只读工具：行为与历史一致（无 dry_run 注入、无审计）。
    写工具：白名单校验 → dry_run 强制默认 → 审计(attempt/result)。
    """
    logger.info(
        f"[internal] tool={tool_name}, user={x_user_id}, entity_type={x_entity_type}"
    )

    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_name}")

    handler = tool_registry.get_handler(tool_name)
    if not handler:
        raise HTTPException(
            status_code=500, detail=f"tool handler not found: {tool_name}"
        )

    is_write = tool_registry.is_write_tool(tool_name)

    if is_write and tool_name not in INTERNAL_WRITE_TOOLS:
        _emit_audit(tool_name, "blocked", x_user_id, x_entity_type, params,
                    success=False, reason="not_whitelisted")
        raise HTTPException(
            status_code=403,
            detail=f"write tool not whitelisted for internal endpoint: {tool_name}",
        )

    # 写工具：未显式 dry_run=False 一律强制 dry_run=True（G2，不论调用方）
    if is_write:
        if params.get("dry_run") is not False:
            params["dry_run"] = True

    # 把 user context 注进 params（ai-service 现有的 user_context 协议）
    params.setdefault("user_context", {})
    params["user_context"]["user_id"] = x_user_id
    params["user_context"]["entity_type"] = x_entity_type
    params["user_context"]["auth_token"] = x_auth_token
    params["auth_token"] = x_auth_token

    if is_write:
        _emit_audit(tool_name, "attempt", x_user_id, x_entity_type, params,
                    dry_run=bool(params.get("dry_run")))

    try:
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(handler):
            result = await handler(**params)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: handler(**params))

        if is_write:
            inner = result if isinstance(result, dict) else {}
            _emit_audit(
                tool_name, "result", x_user_id, x_entity_type, params,
                dry_run=bool(params.get("dry_run")),
                success=bool(inner.get("success", True)),
                error=inner.get("error"),
                record_id=inner.get("record_id"),
            )
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        if is_write:
            _emit_audit(tool_name, "result", x_user_id, x_entity_type, params,
                        dry_run=bool(params.get("dry_run")),
                        success=False, error=str(e))
        logger.error(f"[internal] tool={tool_name} execution failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"tool execution failed: {str(e)}"
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_internal_tools_write_gate.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 回归既有内部端点 + 全量相关测试**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_internal_tools_write_gate.py tests/test_tool_registry_is_write.py tests/test_save_workhour_dry_run.py -q`
Expected: PASS（11 passed）

- [ ] **Step 6: 提交**

```bash
git add fastapi-service/app/api/internal_tools.py fastapi-service/tests/test_internal_tools_write_gate.py
git commit -m "feat(internal): 写工具白名单+dry_run强制+结构化审计 (MCP Phase 3 G1*/G3/G5)"
```

---

### Task 4: save_workhour MCP server 薄壳（二段确认）

**Files:**
- Create: `fastapi-service/mcp_servers/save_workhour_mcp_server.py`
- Test: `fastapi-service/tests/test_save_workhour_mcp_shell.py`

- [ ] **Step 1: 写失败测试**

创建 `fastapi-service/tests/test_save_workhour_mcp_shell.py`：

```python
"""save_workhour MCP 薄壳：confirm→dry_run 映射 + 不接受任意 user_id（G4）。"""
import importlib


def test_confirm_false_maps_dry_run_true():
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    assert mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=False)["dry_run"] is True


def test_confirm_true_maps_dry_run_false():
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    assert mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=True)["dry_run"] is False


def test_params_have_no_target_user_id():
    """薄壳不接受任意目标 user_id，杜绝跨人写（G4）。"""
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    p = mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=True)
    assert "user_id" not in p and "memberId" not in p
```

- [ ] **Step 2: 运行确认失败**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_save_workhour_mcp_shell.py -q`
Expected: FAIL（`ModuleNotFoundError: mcp_servers.save_workhour_mcp_server`）

- [ ] **Step 3: 新建 MCP server**

创建 `fastapi-service/mcp_servers/save_workhour_mcp_server.py`：

```python
"""
Save Workhour MCP Server — 把 save_workhour 写工具经 MCP 暴露。

安全设计（MCP Phase 3）：
    - 默认 dry_run（confirm=False）：首次调用只返回预览，不写库
    - 二段确认：用户明确同意后，以 confirm=True 重发才真写
    - 不接受任意目标 user_id：只为 env 注入身份（token）写，杜绝跨人写
    - 真实写权限闸门是 SpringBoot JWT（MCP_TEST_AUTH_TOKEN 必须合法）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "save_workhour_mcp_server.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("save-workhour-mcp")

from mcp.server.fastmcp import FastMCP

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

mcp = FastMCP("workhour-save")


def _build_params(
    project_id: str, date: str, duration: float, description: str, confirm: bool
) -> dict[str, Any]:
    """confirm→dry_run 映射。不含任何目标 user_id（只为 token 身份写，G4）。"""
    return {
        "project_id": project_id,
        "date": date,
        "duration": duration,
        "description": description,
        "dry_run": not confirm,
    }


async def _call_ai_service_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}",
            json=params,
            headers={
                "X-User-ID": USER_ID,
                "X-Entity-Type": ENTITY_TYPE,
                "X-Auth-Token": AUTH_TOKEN,
            },
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def save_workhour(
    project_id: str,
    date: str,
    duration: float,
    description: str = "",
    confirm: bool = False,
) -> str:
    """填报单条工时。

    **二段确认协议（必须遵守）**：
    1. 首次调用 confirm=False（默认）→ 返回预览（不写库）。把 preview
       原样呈现给用户。
    2. 仅在用户明确同意后，用完全相同参数 + confirm=True 再次调用，才真正写入。

    本工具只为当前配置身份填报，不接受代填他人。

    Args:
        project_id: 项目名称或 ID（系统会解析）
        date: 工时日期 YYYY-MM-DD
        duration: 工时（小时），0.5 的整数倍，0.5~10
        description: 工作内容（可选）
        confirm: False=预览（默认）；True=确认写入
    """
    logger.info(
        f"save_workhour called: project={project_id!r}, date={date!r}, "
        f"duration={duration!r}, confirm={confirm!r}"
    )

    if not USER_ID or not AUTH_TOKEN:
        return json.dumps({
            "error": "MCP server not configured: 缺少 MCP_TEST_USER_ID / MCP_TEST_AUTH_TOKEN env",
            "hint": "请在 .mcp.json 的 env 中配置 MCP_TEST_USER_ID 和 MCP_TEST_AUTH_TOKEN（须合法 SpringBoot JWT）",
        }, ensure_ascii=False, indent=2)

    params = _build_params(project_id, date, duration, description, confirm)
    try:
        result = await _call_ai_service_tool("save_workhour", params)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_workhour failed: {e}", exc_info=True)
        return json.dumps({"error": f"填报失败: {str(e)}"}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    logger.info(
        f"Starting save_workhour MCP server, AI_SERVICE_URL={AI_SERVICE_URL}, "
        f"USER_ID={'set' if USER_ID else 'NOT SET'}"
    )
    mcp.run()
```

- [ ] **Step 4: 运行确认通过**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_save_workhour_mcp_shell.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add fastapi-service/mcp_servers/save_workhour_mcp_server.py fastapi-service/tests/test_save_workhour_mcp_shell.py
git commit -m "feat(mcp): save_workhour 薄壳 二段确认+不接受任意user_id (MCP Phase 3 G4)"
```

---

### Task 5: .mcp.json 注册（token 默认空 = 安全默认）

**Files:**
- Modify: `.mcp.json`（仓库根，与 ai-service/ 同级；当前 `ai-service/.mcp.json`）

- [ ] **Step 1: 加 workhour-save 条目**

`.mcp.json` 的 `mcpServers` 内新增（路径绝对，与现有 `workhour-timesheet` 同构；`MCP_TEST_AUTH_TOKEN` 留空 = 未配 JWT 则 SpringBoot 拒写）：

```json
    "workhour-save": {
      "command": "E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service/fastapi-service/.venv/Scripts/python.exe",
      "args": [
        "E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service/fastapi-service/mcp_servers/save_workhour_mcp_server.py"
      ],
      "env": {
        "AI_SERVICE_URL": "http://localhost:8000",
        "MCP_TEST_USER_ID": "",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "MCP_TEST_AUTH_TOKEN": "",
        "PYTHONIOENCODING": "utf-8"
      },
      "disabled": false,
      "autoApprove": []
    }
```

> 放在 `workhour-knowledge-qa` 条目之后、`mcpServers` 闭合 `}` 之前；记得在前一条目末尾补逗号，保持 JSON 合法。

- [ ] **Step 2: 校验 JSON 合法**

Run: `./fastapi-service/.venv/Scripts/python.exe -c "import json; json.load(open('.mcp.json', encoding='utf-8')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: 全量回归（本计划全部新增测试）**

Run（在 `fastapi-service/`）: `./.venv/Scripts/python.exe -m pytest tests/test_tool_registry_is_write.py tests/test_save_workhour_dry_run.py tests/test_internal_tools_write_gate.py tests/test_save_workhour_mcp_shell.py -q`
Expected: PASS（14 passed）

- [ ] **Step 4: 提交**

```bash
git add .mcp.json
git commit -m "chore(mcp): 注册 workhour-save server (token 默认空=安全默认)"
```

---

## 验收（部署后人工，须用户在场授权）

- 配置真实 SpringBoot JWT 到 `workhour-save` env → MCP 客户端调 `save_workhour(confirm=False)` 返回 preview（DB 无变化）
- 用户确认 → `confirm=True` → SpringBoot 落库，`/api/workhour` 可查到该记录
- 审计日志含 attempt+result 两行、dry_run 标记正确、**无 auth_token**
- 非白名单写工具（如临时给 batch_save 置 is_write 但不加白名单）→ 403 + audit blocked
- 属写生产库动作，不在无人值守批内

## Self-Review（对照 spec）

- **Spec 覆盖**：§5.1→Task1；§5.3→Task2；§5.2(白名单/dry_run/审计)→Task3；§5.4→Task4；§5.5 审计格式→Task3 `_emit_audit`；§5.4 .mcp.json→Task5。G1*(端点不加 PermissionValidator，文档化前提)=spec §4 设计决策，无对应代码 Task（有意，非遗漏）；G2=Task2/3；G3=Task3；G4=Task4；G5=Task1/3。覆盖完整。
- **占位符**：无 TBD/TODO；每个改码步骤均给完整代码。
- **类型一致**：`is_write` 在 ToolDefinition(Task1)/register_tool(Task1)/is_write_tool(Task1)/save_workhour 注册(Task2)/internal_tools(Task3) 命名一致；`dry_run` 在 schema/handler(Task2)/端点(Task3)/薄壳 `_build_params`(Task4) 命名一致；`_build_params` 签名 Task4 测试与实现一致。
- **既有测试影响**：Task3 重写 internal_tools.py 保持只读路径行为不变（test_read_tool_no_dry_run_injection 守卫）；建议执行时附跑既有内部端点相关测试确认无回归。
