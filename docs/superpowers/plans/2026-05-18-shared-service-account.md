# 共享 Service Account 认证模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Service Account 自取 JWT 逻辑抽成共享模块 `mcp_servers/_service_account.py`，7 个 A 类 MCP server 全部 DRY 复用，本地仅凭 `MCP_ENTITY_ID`/`MCP_API_KEY` 即可让全部 A 类工具自取 token 跑通。

**Architecture:** 新建纯函数模块（仅依赖 httpx + env，无 app 依赖）暴露 `auth_configured()` / `ensure_auth()` / `call_ai_service_tool()`，对标已有 `_gateway_core.py` 范式。7 个 server 删除各自鉴权/转发样板改 import。entityType 键名先 spike 实测 mcp-token 真实响应再定，解析不到安全回退 env。

**Tech Stack:** Python 3.11、FastMCP（mcp 库）、httpx、pytest + pytest-asyncio、httpx mock（`patch("httpx.AsyncClient.post")`）。

**依据 spec：** `docs/superpowers/specs/2026-05-18-shared-service-account-design.md`

**全程纪律：**
- 后端改完不执行 `mvn`/编译命令（仅 Python 侧，不涉及）。
- `.mcp.json` **绝不 `git add -A` / `git add .`**；只 `git add .mcp.json` 且入库前 `git diff --cached` 核验凭据字段为空串。
- 工作目录：`E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service`
- pytest 一律走 `fastapi-service/.venv/Scripts/python.exe -m pytest`，cwd = `fastapi-service/`
- 每 Task 独立 commit。

---

### Task 1: Spike — 实测 mcp-token 真实响应，确认角色字段键名

**性质：** 调查型，非 TDD。**失败即停、报告、不硬推**（无法连通 / 无凭据 / 响应无角色字段都属"停并报告"，由人决定下一步）。

**前提：** 本地 ai-service 跑在 `http://localhost:8000`；本地 `.mcp.json` 的 `workhour-save` 段 `MCP_ENTITY_ID`/`MCP_API_KEY` 已填真值（用户已实测 save_workhour 跑通即满足）。

**Files:**
- Create: `fastapi-service/mcp_servers/logs/spike_mcp_token_response.json`（落盘存证，git 忽略，不提交）
- Create: `fastapi-service/scripts/spike_mcp_token.py`（一次性 spike 脚本）

- [ ] **Step 1: 写 spike 脚本**

`fastapi-service/scripts/spike_mcp_token.py`：

```python
"""一次性 spike：实打 /api/internal/auth/mcp-token，原样落盘完整响应 JSON。
确认 SpringBoot 经 ai-service 返回的角色字段真实键名。失败即停，不掩盖。"""
import json
import os
import sys
from pathlib import Path

import httpx

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")
ENTITY_ID = os.getenv("MCP_ENTITY_ID", "")
API_KEY = os.getenv("MCP_API_KEY", "")

if not ENTITY_ID or not API_KEY:
    print("STOP: 缺 MCP_ENTITY_ID / MCP_API_KEY env，无法 spike", file=sys.stderr)
    sys.exit(2)

out_path = Path(__file__).parent.parent / "mcp_servers" / "logs" / "spike_mcp_token_response.json"
out_path.parent.mkdir(parents=True, exist_ok=True)

try:
    resp = httpx.post(
        f"{AI_SERVICE_URL}/api/internal/auth/mcp-token",
        json={"entity_id": ENTITY_ID, "api_key": API_KEY},
        timeout=10.0,
    )
except Exception as e:
    print(f"STOP: 请求失败 {e!r}", file=sys.stderr)
    sys.exit(3)

print(f"HTTP {resp.status_code}")
raw = resp.text
out_path.write_text(raw, encoding="utf-8")
print(f"原始响应已落盘: {out_path}")

if resp.status_code != 200:
    print(f"STOP: 非 200，body={raw[:500]}", file=sys.stderr)
    sys.exit(4)

data = resp.json()
keys = sorted(data.keys())
print(f"响应顶层键: {keys}")
role_candidates = [k for k in keys if k.lower() in
                   ("entitytype", "role", "usertype", "userrole", "entity_type")]
print(f"疑似角色字段: {role_candidates}")
print(f"token 是否非空: {bool(data.get('token'))}")
print(f"userId 是否非空: {bool(data.get('userId'))}")
```

- [ ] **Step 2: 跑 spike**

把本地真凭据导入环境后运行（PowerShell）：

```powershell
$env:MCP_ENTITY_ID = (Get-Content .mcp.json -Raw | ConvertFrom-Json).mcpServers.'workhour-save'.env.MCP_ENTITY_ID
$env:MCP_API_KEY   = (Get-Content .mcp.json -Raw | ConvertFrom-Json).mcpServers.'workhour-save'.env.MCP_API_KEY
cd fastapi-service
.venv\Scripts\python.exe scripts\spike_mcp_token.py
cd ..
```

Expected：打印 `HTTP 200`、顶层键列表、疑似角色字段、token/userId 非空；`mcp_servers/logs/spike_mcp_token_response.json` 落盘。

**失败即停判据：** 任一为真即停并报告，不进 Task 2 —— exit code 非 0 / HTTP 非 200 / `疑似角色字段` 为空列表（说明响应无角色，需人决定回退策略是否够）/ token 为空。

- [ ] **Step 3: 记录结论**

在本计划文件 Task 1 末尾追加一行（替换下方占位为实测结果）：

```
> SPIKE 结论（实测 YYYY-MM-DD）：角色字段键名 = `<实测键名 或 "无，全程回退 env">`，token/userId 键名 = `token`/`userId`，原始响应见 mcp_servers/logs/spike_mcp_token_response.json
```

后续 Task 4 的 `_ROLE_KEY` 常量取此实测键名。

- [ ] **Step 4: Commit（不含落盘 JSON）**

```bash
git add "fastapi-service/scripts/spike_mcp_token.py" "docs/superpowers/plans/2026-05-18-shared-service-account.md"
git commit -m "spike(mcp): 实测 mcp-token 响应角色字段键名"
```

> 落盘 `spike_mcp_token_response.json` 含真实身份信息，**不提交**；确认 `fastapi-service/mcp_servers/logs/` 已被 `.gitignore` 覆盖（既有 `*_mcp_server.log` 同目录，若未忽略则在本 commit 前补 `fastapi-service/mcp_servers/logs/` 到 `.gitignore`）。

---

### Task 2: 共享模块骨架 + `auth_configured()`

**Files:**
- Create: `fastapi-service/mcp_servers/_service_account.py`
- Test: `fastapi-service/tests/test_service_account.py`

- [ ] **Step 1: 写失败测试**

`fastapi-service/tests/test_service_account.py`：

```python
"""共享 Service Account：auth_configured / ensure_auth / call_ai_service_tool。"""
import importlib

import pytest

from mcp_servers import _service_account as sa


def _reload(monkeypatch, **env):
    for k in ("MCP_TEST_AUTH_TOKEN", "MCP_TEST_USER_ID", "MCP_TEST_ENTITY_TYPE",
              "MCP_ENTITY_ID", "MCP_API_KEY", "AI_SERVICE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(sa)
    return sa


def test_auth_configured_none(monkeypatch):
    m = _reload(monkeypatch)
    assert m.auth_configured() is False


def test_auth_configured_preconfigured_token(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT")
    assert m.auth_configured() is True


def test_auth_configured_service_account(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    assert m.auth_configured() is True


def test_auth_configured_partial_sa_is_false(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1")  # 缺 API_KEY
    assert m.auth_configured() is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: FAIL（`ModuleNotFoundError: mcp_servers._service_account`）

- [ ] **Step 3: 写最小实现**

`fastapi-service/mcp_servers/_service_account.py`：

```python
"""共享 Service Account 认证 + ai-service 内部工具转发。

7 个 A 类 MCP server 复用本模块，消除各自重复的鉴权/转发样板。
对标 _gateway_core.py 范式。仅依赖 httpx + env，无 app 依赖。

认证优先级（ensure_auth）：
    1. 预配 MCP_TEST_AUTH_TOKEN
    2. 进程级缓存的 Service Account token
    3. Service Account 自取（MCP_ENTITY_ID + MCP_API_KEY）

日志铁律：只记 tool_name + user_id + auth 来源，绝不记 auth_token / api_key。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("mcp-service-account")

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# Service Account 凭据
MCP_ENTITY_ID = os.getenv("MCP_ENTITY_ID", "")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

# 预配回退
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

# 进程级缓存（每个 stdio server 是独立子进程，各自一份，预期行为）
_cached_token: str | None = None
_cached_user_id: str | None = None
_cached_entity_type: str | None = None


def auth_configured() -> bool:
    """预配 token 或 (entity_id + api_key) 任一齐备即视为已配置。"""
    return bool(AUTH_TOKEN) or bool(MCP_ENTITY_ID and MCP_API_KEY)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_service_account.py" "fastapi-service/tests/test_service_account.py"
git commit -m "feat(mcp): _service_account 模块骨架 + auth_configured"
```

---

### Task 3: `ensure_auth()` 优先级 1（预配）+ 2（缓存）

**Files:**
- Modify: `fastapi-service/mcp_servers/_service_account.py`（追加 `ensure_auth`）
- Test: `fastapi-service/tests/test_service_account.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_service_account.py` 末尾追加：

```python
pytestmark = pytest.mark.asyncio


async def test_ensure_auth_preconfigured_priority(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT",
                MCP_TEST_USER_ID="u9", MCP_TEST_ENTITY_TYPE="deptAdmin",
                MCP_ENTITY_ID="E1", MCP_API_KEY="K1")  # SA 也在，但预配优先
    uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("u9", "deptAdmin", "JWT")


async def test_ensure_auth_cache_hit(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    m._cached_token = "CT"
    m._cached_user_id = "cu"
    m._cached_entity_type = "regionAdmin"
    uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("cu", "regionAdmin", "CT")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'ensure_auth'`）

- [ ] **Step 3: 实现 ensure_auth 前两分支**

在 `_service_account.py` `auth_configured` 之后追加：

```python
async def ensure_auth() -> tuple[str, str, str]:
    """返回 (user_id, entity_type, auth_token)。

    优先级：预配 token → 进程级缓存 → Service Account 自取（Task 4 补）。
    """
    global _cached_token, _cached_user_id, _cached_entity_type

    if AUTH_TOKEN:
        logger.info("auth source=preconfigured user_id=%s", USER_ID or MCP_ENTITY_ID)
        return (USER_ID or MCP_ENTITY_ID, ENTITY_TYPE, AUTH_TOKEN)

    if _cached_token:
        logger.info("auth source=cache user_id=%s", _cached_user_id or MCP_ENTITY_ID)
        return (
            _cached_user_id or MCP_ENTITY_ID,
            _cached_entity_type or ENTITY_TYPE,
            _cached_token,
        )

    return ("", "", "")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_service_account.py" "fastapi-service/tests/test_service_account.py"
git commit -m "feat(mcp): ensure_auth 预配+缓存两分支"
```

---

### Task 4: `ensure_auth()` 优先级 3（SA 自取 + entityType 解析回退）

**Files:**
- Modify: `fastapi-service/mcp_servers/_service_account.py`
- Test: `fastapi-service/tests/test_service_account.py`

> `_ROLE_KEY` 取 **Task 1 spike 实测键名**。下方代码以 `"entityType"` 为占位示例 —— 实施时替换为 spike 结论中的真实键名；若 spike 结论为"无角色字段"，`_ROLE_KEY` 仍设为最可能键名但所有用例覆盖"缺失即回退 env"。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_service_account.py` 末尾追加（`_ROLE_KEY` 用 spike 实测键名，下示用 `entityType`）：

```python
from unittest.mock import patch


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


async def test_ensure_auth_sa_fetch_parses_role(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1",
                MCP_TEST_ENTITY_TYPE="employee")
    payload = {"token": "SAJWT", "userId": "sa-uid", "entityType": "deptAdmin"}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        uid, etype, tok = await m.ensure_auth()
    assert (uid, etype, tok) == ("sa-uid", "deptAdmin", "SAJWT")
    # 缓存已写入
    assert m._cached_token == "SAJWT" and m._cached_entity_type == "deptAdmin"


async def test_ensure_auth_sa_role_missing_falls_back_to_env(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1",
                MCP_TEST_ENTITY_TYPE="regionAdmin")
    payload = {"token": "SAJWT", "userId": "sa-uid"}  # 无角色字段

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        uid, etype, tok = await m.ensure_auth()
    assert etype == "regionAdmin"  # 回退 env 默认，绝不空


async def test_ensure_auth_sa_empty_token_raises(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    payload = {"token": "", "userId": "x"}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(RuntimeError, match="返回空 token"):
            await m.ensure_auth()


async def test_ensure_auth_sa_called_once_then_cached(monkeypatch):
    m = _reload(monkeypatch, MCP_ENTITY_ID="E1", MCP_API_KEY="K1")
    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResp({"token": "T", "userId": "u", "entityType": "employee"})

    with patch("httpx.AsyncClient.post", new=fake_post):
        await m.ensure_auth()
        await m.ensure_auth()
    assert calls["n"] == 1  # 第二次走缓存
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: FAIL（SA 分支未实现，返回 `("","","")`，断言不符）

- [ ] **Step 3: 实现 SA 分支**

在 `_service_account.py` 顶部 env 区后加常量（键名替换为 spike 实测值）：

```python
# Task 1 spike 实测确认的角色字段键名
_ROLE_KEY = "entityType"
```

把 `ensure_auth()` 末尾的 `return ("", "", "")` 替换为：

```python
    if MCP_ENTITY_ID and MCP_API_KEY:
        import httpx

        logger.info("auth source=service_account fetching entity_id=%s", MCP_ENTITY_ID)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AI_SERVICE_URL}/api/internal/auth/mcp-token",
                json={"entity_id": MCP_ENTITY_ID, "api_key": MCP_API_KEY},
            )
            resp.raise_for_status()
            data = resp.json()

        token = data.get("token", "")
        if not token:
            raise RuntimeError("Service Account 认证返回空 token")
        user_id = data.get("userId", "")
        role = data.get(_ROLE_KEY) or ENTITY_TYPE  # 解析不到安全回退 env，绝不空

        _cached_token = token
        _cached_user_id = user_id
        _cached_entity_type = role
        logger.info(
            "auth resolved source=service_account user_id=%s entity_type=%s(from=%s)",
            user_id, role, _ROLE_KEY if data.get(_ROLE_KEY) else "env-fallback",
        )
        return (user_id, role, token)

    return ("", "", "")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_service_account.py" "fastapi-service/tests/test_service_account.py"
git commit -m "feat(mcp): ensure_auth SA 自取 + entityType 解析与 env 回退"
```

---

### Task 5: `call_ai_service_tool()` 转发 + 日志不记 token

**Files:**
- Modify: `fastapi-service/mcp_servers/_service_account.py`
- Test: `fastapi-service/tests/test_service_account.py`

- [ ] **Step 1: 追加失败测试**

末尾追加：

```python
async def test_call_ai_service_tool_headers_and_url(monkeypatch):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="JWT",
                MCP_TEST_USER_ID="u1", MCP_TEST_ENTITY_TYPE="employee",
                AI_SERVICE_URL="http://ai-svc:8000")
    captured = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return R()

    with patch("httpx.AsyncClient.post", new=fake_post):
        out = await m.call_ai_service_tool("query_timesheet", {"a": 1})

    assert out == {"ok": True}
    assert captured["url"] == "http://ai-svc:8000/api/internal/tools/query_timesheet"
    assert captured["headers"] == {
        "X-User-ID": "u1", "X-Entity-Type": "employee", "X-Auth-Token": "JWT"}
    assert captured["json"] == {"a": 1}


async def test_call_logs_no_token(monkeypatch, caplog):
    m = _reload(monkeypatch, MCP_TEST_AUTH_TOKEN="SECRET_JWT",
                MCP_TEST_USER_ID="u1")

    class R:
        def raise_for_status(self): pass
        def json(self): return {}

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        return R()

    import logging as _lg
    with caplog.at_level(_lg.INFO, logger="mcp-service-account"):
        with patch("httpx.AsyncClient.post", new=fake_post):
            await m.call_ai_service_tool("query_project", {})
    assert "SECRET_JWT" not in caplog.text
    assert "query_project" in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: FAIL（`AttributeError: ... 'call_ai_service_tool'`）

- [ ] **Step 3: 实现 call_ai_service_tool**

`_service_account.py` 末尾追加：

```python
async def call_ai_service_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """解析认证后转发到 ai-service 内部工具端点。

    异常不在此吞，由各 server 工具函数体 try/except 兜成 {"error": ...}。
    """
    import httpx

    user_id, entity_type, auth_token = await ensure_auth()
    logger.info("forward tool=%s user_id=%s", tool_name, user_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}",
            json=params,
            headers={
                "X-User-ID": user_id,
                "X-Entity-Type": entity_type,
                "X-Auth-Token": auth_token,
            },
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_service_account.py" "fastapi-service/tests/test_service_account.py"
git commit -m "feat(mcp): call_ai_service_tool 转发 + 日志不记 token"
```

---

### Task 6: 迁移 `save_workhour_mcp_server.py`（全 DRY，保留 _build_params/G4）

**Files:**
- Modify: `fastapi-service/mcp_servers/save_workhour_mcp_server.py`
- Test: `fastapi-service/tests/test_save_workhour_mcp_shell.py`（追加回归用例）

保留：模块 docstring、`_build_params`（confirm→dry_run、G4 不传 user_id）、`save_workhour` 工具签名/docstring、`__main__` 日志块。
删除：`AI_SERVICE_URL`/`MCP_ENTITY_ID`/`MCP_API_KEY`/`USER_ID`/`ENTITY_TYPE`/`AUTH_TOKEN` env 行、`_cached_*` 全局、`_fetch_token_via_service_account`、`_ensure_auth`、本文件的 `_call_ai_service_tool`。

- [ ] **Step 1: 追加回归测试**

`tests/test_save_workhour_mcp_shell.py` 末尾追加：

```python
def test_save_workhour_uses_shared_module():
    """迁移后必须 import 共享模块，不得残留本地鉴权/转发样板。"""
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    import inspect
    src = inspect.getsource(mod)
    assert "from mcp_servers._service_account import" in src
    assert "_fetch_token_via_service_account" not in src
    assert "_ensure_auth" not in src
    # _build_params / G4 仍在
    p = mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=True)
    assert p["dry_run"] is False and "user_id" not in p
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_save_workhour_mcp_shell.py -v`
Expected: FAIL（`from mcp_servers._service_account import` 尚未出现在源码）

- [ ] **Step 3: 改写 save_workhour_mcp_server.py**

把第 36~133 行（从 `from mcp.server.fastmcp import FastMCP` 到本文件 `_call_ai_service_tool` 结束）替换为：

```python
from mcp.server.fastmcp import FastMCP

from mcp_servers._service_account import auth_configured, call_ai_service_tool

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
```

把 `save_workhour` 工具体内"检查认证配置"块（原第 165~172 行 `if not AUTH_TOKEN and not (MCP_ENTITY_ID and MCP_API_KEY):` 整段）替换为：

```python
    if not auth_configured():
        return json.dumps({
            "error": "MCP server not configured",
            "hint": "请配置以下任一组认证信息：\n"
                    "1) Service Account（推荐）: MCP_ENTITY_ID + MCP_API_KEY\n"
                    "2) 预配 Token: MCP_TEST_USER_ID + MCP_TEST_AUTH_TOKEN",
        }, ensure_ascii=False, indent=2)
```

把工具体内 `result = await _call_ai_service_tool("save_workhour", params)` 改为
`result = await call_ai_service_tool("save_workhour", params)`。

把 `__main__` 块（原第 183~193 行）替换为：

```python
if __name__ == "__main__":
    from mcp_servers._service_account import auth_configured as _ac
    logger.info("Starting save_workhour MCP server, auth_configured=%s", _ac())
    mcp.run()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_save_workhour_mcp_shell.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/save_workhour_mcp_server.py" "fastapi-service/tests/test_save_workhour_mcp_shell.py"
git commit -m "refactor(mcp): save_workhour 迁移共享 _service_account（保留 _build_params/G4）"
```

---

### Task 7: 迁移 6 个只读 server

**Files（逐个改，每个同构）：**
- Modify: `fastapi-service/mcp_servers/timesheet_mcp_server.py`
- Modify: `fastapi-service/mcp_servers/project_mcp_server.py`
- Modify: `fastapi-service/mcp_servers/statistics_mcp_server.py`
- Modify: `fastapi-service/mcp_servers/weekly_report_mcp_server.py`
- Modify: `fastapi-service/mcp_servers/sql_query_mcp_server.py`
- Modify: `fastapi-service/mcp_servers/knowledge_qa_mcp_server.py`
- Test: `fastapi-service/tests/test_readonly_mcp_migration.py`

工具名映射（每 server 转发的内部工具名，迁移时**不改**调用名）：

| server 文件 | `call_ai_service_tool` 第一参 |
|---|---|
| timesheet | `query_timesheet` |
| project | `query_project` |
| statistics | `compute_statistics` |
| weekly_report | `generate_weekly_report` |
| sql_query | `sql_query` |
| knowledge_qa | `knowledge_qa` |

- [ ] **Step 1: 写失败测试**

`fastapi-service/tests/test_readonly_mcp_migration.py`：

```python
"""6 个只读 server 迁移后：import 共享模块、无残留样板、guard 用 auth_configured。"""
import importlib
import inspect

import pytest

MODS = [
    "mcp_servers.timesheet_mcp_server",
    "mcp_servers.project_mcp_server",
    "mcp_servers.statistics_mcp_server",
    "mcp_servers.weekly_report_mcp_server",
    "mcp_servers.sql_query_mcp_server",
    "mcp_servers.knowledge_qa_mcp_server",
]


@pytest.mark.parametrize("name", MODS)
def test_imports_shared_module(name):
    src = inspect.getsource(importlib.import_module(name))
    assert "from mcp_servers._service_account import" in src
    assert "async def _call_ai_service_tool" not in src
    assert 'os.getenv("MCP_TEST_AUTH_TOKEN"' not in src
    assert "if not USER_ID or not AUTH_TOKEN" not in src
    assert "auth_configured()" in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_readonly_mcp_migration.py -v`
Expected: FAIL（6 个 failed，源码未改）

- [ ] **Step 3: 逐个改写（对 6 个文件各执行同一过程）**

对每个文件：

(a) 把 env 块（从 `AI_SERVICE_URL = os.getenv(` 那行起，连同其后到 `AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")` 行，含中间 PoC 注释两行与 `USER_ID`/`ENTITY_TYPE` 两行）整体删除，替换为一行 import（放在 `from mcp.server.fastmcp import FastMCP` 之后、`mcp = FastMCP(...)` 之前）：

```python
from mcp_servers._service_account import auth_configured, call_ai_service_tool
```

(b) 删除整个本地 `async def _call_ai_service_tool(...)` 函数（从 `async def _call_ai_service_tool(` 到其 `return response.json()` 行，含其上的 `# ─── 内部 HTTP 调用 ───` 注释段如有）。

(c) 工具函数体内 guard：

把
```python
    if not USER_ID or not AUTH_TOKEN:
        return json.dumps(
            {
                "error": "MCP server not configured: 缺少 MCP_TEST_USER_ID / MCP_TEST_AUTH_TOKEN env",
                "hint": "请在 .mcp.json 的 env 中配置 MCP_TEST_USER_ID 和 MCP_TEST_AUTH_TOKEN",
            },
            ensure_ascii=False,
            indent=2,
        )
```
替换为
```python
    if not auth_configured():
        return json.dumps(
            {
                "error": "MCP server not configured",
                "hint": "请配置以下任一组认证信息：\n"
                        "1) Service Account（推荐）: MCP_ENTITY_ID + MCP_API_KEY\n"
                        "2) 预配 Token: MCP_TEST_USER_ID + MCP_TEST_AUTH_TOKEN",
            },
            ensure_ascii=False,
            indent=2,
        )
```

(d) 把工具体内 `result = await _call_ai_service_tool(<工具名>, params)` 改为 `result = await call_ai_service_tool(<工具名>, params)`（工具名照上表，不变）。

(e) `__main__` 块替换为（保留各自 server 名不影响，此处统一日志）：

```python
if __name__ == "__main__":
    logger.info("Starting MCP server, auth_configured=%s", auth_configured())
    mcp.run()
```

> 注意：`json` / `logging` / `os` / `sys` / `Path` 等 import 与 `logger` 定义、`mcp = FastMCP(...)`、`@mcp.tool()` 函数签名与 docstring **全部不动**。各 server 的参数构建逻辑（如 timesheet 的 `if member_id is not None` 段）**不动**。

- [ ] **Step 4: 跑迁移测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_readonly_mcp_migration.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑全 MCP 相关测试无回归**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py tests/test_readonly_mcp_migration.py tests/test_save_workhour_mcp_shell.py -v`
Expected: PASS（12 + 6 + 4 = 22 passed）

- [ ] **Step 6: Commit**

```bash
git add "fastapi-service/mcp_servers/timesheet_mcp_server.py" "fastapi-service/mcp_servers/project_mcp_server.py" "fastapi-service/mcp_servers/statistics_mcp_server.py" "fastapi-service/mcp_servers/weekly_report_mcp_server.py" "fastapi-service/mcp_servers/sql_query_mcp_server.py" "fastapi-service/mcp_servers/knowledge_qa_mcp_server.py" "fastapi-service/tests/test_readonly_mcp_migration.py"
git commit -m "refactor(mcp): 6 个只读 server 迁移共享 _service_account"
```

---

### Task 8: `.mcp.json` 6 个只读 server 加 SA 凭据空占位

**Files:**
- Modify: `.mcp.json`（仅 6 个只读 server 的 `env` 块）

> **凭据铁律**：入库版 `MCP_ENTITY_ID`/`MCP_API_KEY` **必须空串**。本地工作区可填真值自测但**绝不提交**。提交只 `git add .mcp.json`，**绝不 `git add -A`/`git add .`**。

- [ ] **Step 1: 给 6 个只读 server 的 env 加两个空占位键**

对 `.mcp.json` 中 `workhour-timesheet` / `workhour-project` / `workhour-statistics` / `workhour-weekly-report` / `workhour-sql-query` / `workhour-knowledge-qa` 六个 server 的 `env`，在 `"AI_SERVICE_URL": "http://localhost:8000",` 之后插入两行：

```json
        "MCP_ENTITY_ID": "",
        "MCP_API_KEY": "",
```

（与 `workhour-save` 段同构；`workhour-knowledge-base` 不动 —— B 类无此需求。）

- [ ] **Step 2: 入库前核验凭据为空**

```bash
git add .mcp.json
git diff --cached .mcp.json
```

Expected：diff 中所有新增 `MCP_ENTITY_ID` / `MCP_API_KEY` 行右值均为 `""`（空串）。**若任一非空，立即 `git restore --staged .mcp.json` 并把真值移回工作区后重做本步。**

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(mcp): 6 个只读 server .mcp.json 加 SA 凭据空占位"
```

- [ ] **Step 4: 本地自测可填真值（不提交）**

如需本地跑通：用 Edit 把 6 个 server 的 `MCP_ENTITY_ID`/`MCP_API_KEY` 填为与 `workhour-save` 相同的真值，**保持工作区未提交**（`git status` 显示 ` M .mcp.json` 是预期态，勿提交）。

---

### Task 9: 全量回归 + push

- [ ] **Step 1: 跑本批全部新增/相关测试**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py tests/test_readonly_mcp_migration.py tests/test_save_workhour_mcp_shell.py tests/test_gateway_core.py -v`
Expected: PASS（22 + 既有 gateway 用例全绿；gateway 用例须不受影响，证明方案2 网关未被波及）

- [ ] **Step 2: 核验无越界文件改动**

```bash
git diff --stat 3435e4b..HEAD
```

Expected：仅本计划涉及文件（`_service_account.py`、`test_service_account.py`、`test_readonly_mcp_migration.py`、`test_save_workhour_mcp_shell.py`、7 个 server、`.mcp.json`、`scripts/spike_mcp_token.py`、本计划 md）。`kb_mcp_server.py` / `_gateway_core.py` / `http_gateway_server.py` / `batch_save_workhour*` **零改动**。

- [ ] **Step 3: 核验 .mcp.json 凭据入库为空**

```bash
git show HEAD:.mcp.json | python -c "import sys,json; d=json.load(sys.stdin)['mcpServers']; print({k:(v['env'].get('MCP_API_KEY'),v['env'].get('MCP_ENTITY_ID')) for k,v in d.items() if 'MCP_API_KEY' in v.get('env',{})})"
```

Expected：所有 server 的值均为 `('', '')`。

- [ ] **Step 4: push**

```bash
git push origin main
```

---

## 部署后人工 e2e 章节（非自动化，用户在场，不在无人值守批）

下列属本地真实写/查验证，**须用户在场手动执行，不进自动化批**：

1. 本地 `.mcp.json` 6 个只读 server 填真 `MCP_ENTITY_ID`/`MCP_API_KEY`（Task 8 Step 4，不提交）。
2. 重启 MCP 客户端（Claude Code / Cursor）加载新 `.mcp.json`。
3. 逐一实测 7 个 A 类工具自取 token 跑通：
   - 只读 6 个：各调一次，确认返回真实数据而非 "MCP server not configured"。
   - `save_workhour`：confirm=False 预览 → confirm=True 写库 → `/api/workhour` 可查 + 审计日志含 attempt/result 且**不含 auth_token**。
4. 跨角色抽验：若 SA 账号为管理员，确认 `query_timesheet`/`compute_statistics` 跨人查询按 spike 解析出的 `X-Entity-Type` 放行（验证 entityType 确实读下来生效）。

---

## Self-Review

**Spec 覆盖：** spec §3 模块 API → Task 2-5；§4 entityType spike+回退 → Task 1 + Task 4；§5 7 server 迁移 → Task 6（save）+ Task 7（6 只读）；§6 .mcp.json 凭据纪律 → Task 8 + Task 9 Step 3；§7 错误处理 → Task 4（空 token raise）/ Task 5（不吞异常）/ Task 6-7（guard 前移）；§8 测试 → 各 Task 的 TDD 步 + Task 9；§9 验收 → Task 9 + 人工 e2e 章节。无 spec 要求缺任务。

**占位扫描：** 仅 Task 4 `_ROLE_KEY` 依赖 Task 1 spike 实测值 —— 这是 spec 明确要求的"先实测再定"，已给占位示例 + 替换指令 + 回退兜底，非懒占位。其余步均含完整代码与确切命令。

**类型一致性：** `auth_configured()->bool`、`ensure_auth()->tuple[str,str,str]`、`call_ai_service_tool(tool_name,params)->dict` 三签名在 Task 2/3/4/5 定义与 Task 6/7 调用处一致；`_cached_token/_cached_user_id/_cached_entity_type` 命名贯穿 Task 2-4 一致；测试 helper `_reload` 贯穿一致。
