# 方案2 网关 C1 身份增量 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把方案2 网关身份从「客户端 X-Auth-Token JWT 透传」全替换为「网关侧共享 MCP_API_KEY + 自声明 X-Entity-ID → Service Account 换绑定该钉钉ID的 JWT」，per-entity_id TTL 缓存 + ai-service 401 重换，7 工具含 save_workhour 全覆盖。

**Architecture:** 从 `_service_account.py` 抽出纯取数 `fetch_service_account_token`（行为保持，stdio `ensure_auth` 改调它）。`_gateway_core.py` 加 `Identity.entity_id` 字段、per-entity_id TTL 缓存、`resolve_identity`、`forward_to_ai_service` 401 重换、中间件读 `X-Entity-ID`。`http_gateway_server.py` 仅改 save_workhour docstring。

**Tech Stack:** Python 3.11、httpx、Starlette BaseHTTPMiddleware、FastMCP、pytest + pytest-asyncio、httpx mock（`patch("httpx.AsyncClient.post")`）。

**依据 spec：** `docs/superpowers/specs/2026-05-19-mcp-gateway-c1-identity-design.md`

**全程纪律：**
- 后端改完不执行 `mvn`/编译命令（仅 Python 侧）。
- 工作目录：`E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service`
- pytest 一律走 `fastapi-service/.venv/Scripts/python.exe -m pytest`，cwd = `fastapi-service/`
- **凭据铁律：本计划不改 `.mcp.json`；提交一律只 `git add <明确文件>`，绝不 `git add -A` / `git add .`。**
- 网关运行 env（`MCP_API_KEY`/`MCP_GATEWAY_TOKEN`）在 172 `.env` 注入，**不入库**，本计划不涉及。
- 每 Task 独立 commit。
- 失败即停类步骤（Task 1 Step 4 回归）若不通过：停、报告、不硬推。

**现状代码锚点（实施前已确认）：**
- `_service_account.py`：`_ROLE_KEY="entityType"`（第 39 行）、`ensure_auth` 第 3 分支在第 66~93 行、`tests/test_service_account.py` 现有 **12** 个用例。
- `_gateway_core.py`：`GATEWAY_TOKEN`/`AI_SERVICE_URL` env 第 27~28 行、`Identity` dataclass 第 34~38 行、`GatewayAuthMiddleware.dispatch` 第 50~73 行、`forward_to_ai_service` 第 76~94 行。
- `tests/test_gateway_core.py`：现有 5 用例；`_app(monkeypatch)` helper + `test_identity_populated_from_headers`（**本计划 Task 3 会改写它**，因身份模型变了）。

---

### Task 1: 抽出 `fetch_service_account_token`（行为保持）

**Files:**
- Modify: `fastapi-service/mcp_servers/_service_account.py`
- Test: `fastapi-service/tests/test_service_account.py`（追加 2 用例，不改现有 12）

- [ ] **Step 1: 追加针对新函数的失败测试**

在 `tests/test_service_account.py` 末尾追加：

```python
async def test_fetch_service_account_token_parses(monkeypatch):
    m = _reload(monkeypatch)

    async def fake_post(self, url, json=None, headers=None, timeout=None):
        assert url.endswith("/api/internal/auth/mcp-token")
        assert json == {"entity_id": "E9", "api_key": "K9"}
        return _FakeResp({"token": "T9", "userId": "uid9", "entityType": "deptAdmin"})

    with patch("httpx.AsyncClient.post", new=fake_post):
        tok, uid, et = await m.fetch_service_account_token(
            "E9", "K9", ai_service_url="http://ai-svc:8000")
    assert (tok, uid, et) == ("T9", "uid9", "deptAdmin")


async def test_fetch_service_account_token_role_fallback_and_empty(monkeypatch):
    m = _reload(monkeypatch)

    async def fake_no_role(self, url, json=None, headers=None, timeout=None):
        return _FakeResp({"token": "T", "userId": "u"})  # 无角色键

    with patch("httpx.AsyncClient.post", new=fake_no_role):
        tok, uid, et = await m.fetch_service_account_token(
            "E", "K", ai_service_url="http://x", fallback_entity_type="regionAdmin")
    assert et == "regionAdmin"  # 回退，绝不空

    async def fake_empty(self, url, json=None, headers=None, timeout=None):
        return _FakeResp({"token": "", "userId": "u"})

    with patch("httpx.AsyncClient.post", new=fake_empty):
        with pytest.raises(RuntimeError, match="返回空 token"):
            await m.fetch_service_account_token("E", "K", ai_service_url="http://x")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: 新 2 用例 FAIL（`AttributeError: ... 'fetch_service_account_token'`），现有 12 仍 PASS

- [ ] **Step 3: 抽出纯函数 + ensure_auth 改调它**

在 `_service_account.py` 中，把 `ensure_auth()` 第 3 分支（现第 66~91 行，即 `if MCP_ENTITY_ID and MCP_API_KEY:` 到该分支 `return (user_id, role, token)`）替换为：

```python
    if MCP_ENTITY_ID and MCP_API_KEY:
        logger.info("auth source=service_account fetching entity_id=%s", MCP_ENTITY_ID)
        token, user_id, role = await fetch_service_account_token(
            MCP_ENTITY_ID, MCP_API_KEY,
            ai_service_url=AI_SERVICE_URL,
            role_key=_ROLE_KEY,
            fallback_entity_type=ENTITY_TYPE,
        )
        _cached_token = token
        _cached_user_id = user_id
        _cached_entity_type = role
        logger.info(
            "auth resolved source=service_account user_id=%s entity_type=%s",
            user_id, role,
        )
        return (user_id, role, token)

    return ("", "", "")
```

> 行为保持说明：返回值/缓存写入/空 token raise 语义逐字不变；仅 SA 解析日志由原来带 `(from=...)` 细节简化为不带 —— **现有 12 用例无一断言该日志文本**（断言的是返回值、httpx 调用次数、call_ai_service_tool 的 caplog），故安全。

在文件顶部 `auth_configured()` 定义**之前**（紧接 `_ROLE_KEY = "entityType"` 之后）新增纯函数：

```python
async def fetch_service_account_token(
    entity_id: str,
    api_key: str,
    *,
    ai_service_url: str,
    role_key: str = _ROLE_KEY,
    fallback_entity_type: str = ENTITY_TYPE,
) -> tuple[str, str, str]:
    """纯取数：POST mcp-token 换 (token, user_id, entity_type)。

    无 env 读取、无缓存、无全局态。stdio ensure_auth 与网关 resolver 共用。
    token 空 → RuntimeError；角色键缺失 → 回退 fallback_entity_type（绝不空）。
    """
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{ai_service_url}/api/internal/auth/mcp-token",
            json={"entity_id": entity_id, "api_key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("token", "")
    if not token:
        raise RuntimeError("Service Account 认证返回空 token")
    user_id = data.get("userId", "")
    entity_type = data.get(role_key) or fallback_entity_type
    return token, user_id, entity_type
```

- [ ] **Step 4: 跑全 service_account 测试确认行为保持（失败即停）**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py -v`
Expected: **14 passed**（现有 12 全绿 = 行为保持实证 + 新 2 绿）。
若现有 12 任一变红 → **停、报告**，说明抽取破坏了 ensure_auth 行为，不硬推。

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_service_account.py" "fastapi-service/tests/test_service_account.py"
git commit -m "refactor(mcp): 抽 fetch_service_account_token 纯函数(ensure_auth 行为保持)"
```

---

### Task 2: `Identity.entity_id` + per-entity_id TTL 缓存 + `resolve_identity`

**Files:**
- Modify: `fastapi-service/mcp_servers/_gateway_core.py`
- Test: `fastapi-service/tests/test_gateway_core.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_gateway_core.py` 末尾追加：

```python
async def test_resolve_identity_caches_per_entity(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-svc:8000")
    import importlib
    importlib.reload(gc)
    calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        calls["n"] += 1
        return ("TOK-" + entity_id, "uuid-" + entity_id, "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)

    i1 = await gc.resolve_identity("E1")
    i1b = await gc.resolve_identity("E1")          # 命中缓存
    i2 = await gc.resolve_identity("E2")           # 不同人，另起
    assert calls["n"] == 2
    assert i1.user_id == "uuid-E1" and i1.auth_token == "TOK-E1"
    assert i1.entity_id == "E1" and i1b is i1
    assert i2.user_id == "uuid-E2"


async def test_resolve_identity_ttl_expiry(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("MCP_GATEWAY_TOKEN_TTL", "0")  # 立即过期
    import importlib
    importlib.reload(gc)
    calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        calls["n"] += 1
        return ("T", "u", "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)
    await gc.resolve_identity("E1")
    await gc.resolve_identity("E1")   # TTL=0 → 再次 fetch
    assert calls["n"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py -v`
Expected: 新 2 用例 FAIL（`AttributeError: ... 'resolve_identity'` / `Identity` 无 `entity_id`）

- [ ] **Step 3: 实现**

`_gateway_core.py` 顶部 import 区加 `import time`；env 区（现第 27~28 行 `GATEWAY_TOKEN`/`AI_SERVICE_URL` 之后）追加：

```python
MCP_API_KEY = os.getenv("MCP_API_KEY", "")
MCP_GATEWAY_TOKEN_TTL = int(os.getenv("MCP_GATEWAY_TOKEN_TTL", "1500"))
```

文件顶部 import 区加：

```python
from mcp_servers._service_account import fetch_service_account_token
```

`Identity` dataclass（现第 34~38 行）加字段：

```python
@dataclass
class Identity:
    user_id: str = ""
    entity_type: str = "employee"
    auth_token: str = ""
    entity_id: str = ""
```

在 `get_identity()` 之后、`GatewayAuthMiddleware` 之前新增：

```python
_TOKEN_CACHE: dict[str, tuple[Identity, float]] = {}


async def resolve_identity(entity_id: str) -> Identity:
    """按自声明 entity_id 换 JWT，per-entity_id TTL 缓存。"""
    now = time.monotonic()
    hit = _TOKEN_CACHE.get(entity_id)
    if hit and hit[1] > now:
        return hit[0]
    token, user_id, etype = await fetch_service_account_token(
        entity_id, MCP_API_KEY, ai_service_url=AI_SERVICE_URL
    )
    ident = Identity(
        user_id=user_id, entity_type=etype,
        auth_token=token, entity_id=entity_id,
    )
    _TOKEN_CACHE[entity_id] = (ident, now + MCP_GATEWAY_TOKEN_TTL)
    return ident


def _evict(entity_id: str) -> None:
    _TOKEN_CACHE.pop(entity_id, None)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py -v`
Expected: 新 2 用例 PASS；现有 5 用例**可能**有 `test_identity_populated_from_headers` 仍按旧逻辑（Task 3 改写），此刻其余 4 应仍 PASS（缺 token 401×2 / health bypass / forward）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_gateway_core.py" "fastapi-service/tests/test_gateway_core.py"
git commit -m "feat(mcp): 网关 Identity.entity_id + per-entity_id TTL 缓存 + resolve_identity"
```

---

### Task 3: 中间件读 `X-Entity-ID` + resolve + 错误处理

**Files:**
- Modify: `fastapi-service/mcp_servers/_gateway_core.py`（`GatewayAuthMiddleware.dispatch`）
- Test: `fastapi-service/tests/test_gateway_core.py`（改写 1 旧用例 + 追加 3）

- [ ] **Step 1: 改写旧用例 + 追加失败测试**

在 `tests/test_gateway_core.py` 中，把现有 `test_identity_populated_from_headers` 整个函数替换为下面这组（身份模型已变：不再透传 header JWT，改 SA 换取）：

```python
def _app_resolved(monkeypatch, fetch_impl):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW_SECRET")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    import importlib
    importlib.reload(gc)
    monkeypatch.setattr(gc, "fetch_service_account_token", fetch_impl)

    async def probe(request):
        i = gc.get_identity()
        return PlainTextResponse(f"{i.user_id}|{i.entity_type}|{i.auth_token}|{i.entity_id}")

    app = Starlette(routes=[
        Route("/probe", probe),
        Route("/health/health", lambda r: PlainTextResponse("ok")),
    ])
    app.add_middleware(gc.GatewayAuthMiddleware)
    return app


def test_identity_resolved_via_service_account(monkeypatch):
    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        return ("JWTx", "uuid-99", "deptAdmin")

    client = TestClient(_app_resolved(monkeypatch, fake_fetch))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET",
                                      "X-Entity-ID": "ding-99"})
    assert r.status_code == 200
    assert r.text == "uuid-99|deptAdmin|JWTx|ding-99"


def test_missing_entity_id_401(monkeypatch):
    async def fake_fetch(*a, **k):
        raise AssertionError("不应被调用")

    client = TestClient(_app_resolved(monkeypatch, fake_fetch))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET"})
    assert r.status_code == 401
    assert "X-Entity-ID" in r.text


def test_sa_failure_502_no_secret(monkeypatch):
    async def boom(entity_id, api_key, *, ai_service_url,
                   role_key="entityType", fallback_entity_type="employee"):
        raise RuntimeError("Service Account 认证返回空 token")

    client = TestClient(_app_resolved(monkeypatch, boom))
    r = client.get("/probe", headers={"X-Gateway-Token": "GW_SECRET",
                                      "X-Entity-ID": "ding-1"})
    assert r.status_code == 502
    assert "SHARED" not in r.text and "JWT" not in r.text
    assert "identity resolution failed" in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py -v`
Expected: 3 新用例 FAIL（中间件仍按旧 header 透传逻辑，未读 X-Entity-ID / 未 resolve / 无 502）

- [ ] **Step 3: 改写 `GatewayAuthMiddleware.dispatch`**

把 `_gateway_core.py` 的 `GatewayAuthMiddleware.dispatch`（现第 51~73 行整个方法体）替换为：

```python
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _HEALTH_PREFIXES):
            return await call_next(request)

        supplied = request.headers.get("X-Gateway-Token", "")
        if not GATEWAY_TOKEN or supplied != GATEWAY_TOKEN:
            return JSONResponse(
                {"error": "missing or invalid X-Gateway-Token"},
                status_code=401,
            )

        entity_id = request.headers.get("X-Entity-ID", "")
        if not entity_id:
            return JSONResponse(
                {"error": "missing or invalid X-Entity-ID"},
                status_code=401,
            )

        try:
            ident = await resolve_identity(entity_id)
        except Exception as e:  # noqa: BLE001
            # 不回显 token/key/上游 detail；内部日志不记密钥
            logger.error(
                "[gateway] identity resolution failed entity_id=%s err=%s",
                entity_id, type(e).__name__,
            )
            return JSONResponse(
                {"error": "identity resolution failed"},
                status_code=502,
            )

        token = _IDENTITY.set(ident)
        try:
            return await call_next(request)
        finally:
            _IDENTITY.reset(token)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py -v`
Expected: 全绿（原 4 个未动的 + 改写后 test_identity_resolved_via_service_account + 2 新 401/502 + Task 2 的 2 缓存用例）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_gateway_core.py" "fastapi-service/tests/test_gateway_core.py"
git commit -m "feat(mcp): 网关中间件读 X-Entity-ID + SA resolve + 401/502 错误处理"
```

---

### Task 4: `forward_to_ai_service` ai-service 401 重换一次

**Files:**
- Modify: `fastapi-service/mcp_servers/_gateway_core.py`（`forward_to_ai_service`）
- Test: `fastapi-service/tests/test_gateway_core.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

末尾追加：

```python
async def test_forward_reauth_on_401(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "GW")
    monkeypatch.setenv("MCP_API_KEY", "SHARED")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-svc:8000")
    import importlib
    importlib.reload(gc)

    fetch_calls = {"n": 0}

    async def fake_fetch(entity_id, api_key, *, ai_service_url,
                         role_key="entityType", fallback_entity_type="employee"):
        fetch_calls["n"] += 1
        return (f"TOK{fetch_calls['n']}", "uid", "employee")

    monkeypatch.setattr(gc, "fetch_service_account_token", fake_fetch)

    import httpx
    post_calls = {"n": 0, "tokens": []}

    class Resp401:
        status_code = 401
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "401", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(401))
        def json(self): return {}

    class RespOK:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    async def fake_post(self, url, json=None, headers=None):
        post_calls["n"] += 1
        post_calls["tokens"].append(headers["X-Auth-Token"])
        return Resp401() if post_calls["n"] == 1 else RespOK()

    ident = await gc.resolve_identity("E1")           # 预热缓存
    tok = gc._IDENTITY.set(ident)
    try:
        with patch("httpx.AsyncClient.post", new=fake_post):
            out = await gc.forward_to_ai_service("query_timesheet", {})
    finally:
        gc._IDENTITY.reset(tok)

    assert out == {"ok": True}
    assert post_calls["n"] == 2                        # 重发一次
    assert fetch_calls["n"] == 2                        # evict 后重换
    assert post_calls["tokens"][0] != post_calls["tokens"][1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py::test_forward_reauth_on_401 -v`
Expected: FAIL（现 `forward_to_ai_service` 遇 401 直接抛，不重换）

- [ ] **Step 3: 改写 `forward_to_ai_service`**

把 `_gateway_core.py` 的 `forward_to_ai_service`（现第 76~94 行整个函数）替换为：

```python
async def forward_to_ai_service(
    tool_name: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """转发到同机 ai-service 内部端点。ai-service 返 401 → 重换 token 重发一次。"""
    ident = get_identity()
    url = f"{AI_SERVICE_URL}/api/internal/tools/{tool_name}"
    logger.info(f"[gateway] forward tool={tool_name} user={ident.user_id}")

    async def _post(identity: Identity) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(
                url,
                json=params,
                headers={
                    "X-User-ID": identity.user_id,
                    "X-Entity-Type": identity.entity_type,
                    "X-Auth-Token": identity.auth_token,
                },
            )

    resp = await _post(ident)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401 and ident.entity_id:
            _evict(ident.entity_id)
            ident = await resolve_identity(ident.entity_id)
            resp = await _post(ident)
            resp.raise_for_status()
        else:
            raise
    return resp.json()
```

- [ ] **Step 4: 跑全网关测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_gateway_core.py -v`
Expected: 全绿（含新 401 重换 + 原 `test_forward_passes_identity_headers` 仍 PASS —— 该用例直接 set contextvar Identity 且 mock 返回成功，不进 401 分支）

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/_gateway_core.py" "fastapi-service/tests/test_gateway_core.py"
git commit -m "feat(mcp): forward_to_ai_service ai-service 401 evict+重换+重试一次"
```

---

### Task 5: `http_gateway_server.py` save_workhour docstring 改

**Files:**
- Modify: `fastapi-service/mcp_servers/http_gateway_server.py`
- Test: `fastapi-service/tests/test_http_gateway_server.py`（追加 1 用例）

> 仅文档措辞：身份模型已变，原 docstring「只为当前请求身份（X-Auth-Token 对应的人）填报」不再准确。功能代码（`_save_workhour_impl` 不含目标 user_id，G4 结构性保护）**不动**。

- [ ] **Step 1: 追加失败测试**

`tests/test_http_gateway_server.py` 末尾追加：

```python
def test_save_workhour_docstring_reflects_sa_identity():
    import importlib
    m = importlib.import_module("mcp_servers.http_gateway_server")
    doc = m.save_workhour.__doc__ or ""
    assert "X-Auth-Token" not in doc
    assert "X-Entity-ID" in doc and "Service Account" in doc


def test_save_workhour_impl_no_target_user_id():
    """G4 结构性保护：网关 save 参数不含目标 user_id（保持）。"""
    import importlib, inspect
    m = importlib.import_module("mcp_servers.http_gateway_server")
    src = inspect.getsource(m._save_workhour_impl)
    assert '"user_id"' not in src and "'user_id'" not in src
    assert "memberId" not in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_http_gateway_server.py -v`
Expected: `test_save_workhour_docstring_reflects_sa_identity` FAIL（现 docstring 仍含 X-Auth-Token）；第二个应已 PASS（现状即不含 user_id）

- [ ] **Step 3: 改 docstring**

`http_gateway_server.py` 的 `save_workhour` 工具函数 docstring 中，把这一句：

```
    只为当前请求身份（X-Auth-Token 对应的人）填报，不接受代填他人。
```

替换为：

```
    只为本请求 X-Entity-ID 经 Service Account 换取的身份填报，不接受代填他人。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_http_gateway_server.py -v`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add "fastapi-service/mcp_servers/http_gateway_server.py" "fastapi-service/tests/test_http_gateway_server.py"
git commit -m "docs(mcp): save_workhour docstring 改为 SA 换取身份模型(C1)"
```

---

### Task 6: 全量回归 + push

- [ ] **Step 1: 跑本批全部相关测试**

Run: `cd fastapi-service && .venv\Scripts\python.exe -m pytest tests/test_service_account.py tests/test_gateway_core.py tests/test_http_gateway_server.py tests/test_readonly_mcp_migration.py tests/test_save_workhour_mcp_shell.py -v`
Expected: 全绿。`test_service_account.py` 14 passed（12 行为保持 + 2 新）；网关/http_gateway/迁移/save 壳全绿无回归。
若 `test_service_account.py` 现有 12 任一红 → 停报告（行为保持被破坏）。

- [ ] **Step 2: 核验无越界 + 无凭据入库**

```bash
git diff --stat d23d24f..HEAD
git show HEAD:.mcp.json | python -c "import sys,json; d=json.load(sys.stdin)['mcpServers']; print({k:(v['env'].get('MCP_API_KEY'),v['env'].get('MCP_ENTITY_ID')) for k,v in d.items() if 'MCP_API_KEY' in v.get('env',{})})"
```

Expected：改动文件仅 `_service_account.py`、`_gateway_core.py`、`http_gateway_server.py`、`test_service_account.py`、`test_gateway_core.py`、`test_http_gateway_server.py`、本计划 md。`.mcp.json` **不在改动列表**。.mcp.json 入库凭据值全 `('', '')`。

- [ ] **Step 3: push**

```bash
git push origin main
```

---

## 部署章节（非自动化，用户在场，规则7，不在无人值守批）

下列属生产/真实 e2e，**须用户在场手动执行，不进自动化批**：

1. `docker-compose.yml`（172）加 `mcp-gateway` service：`command` 跑 `python mcp_servers/http_gateway_server.py`，`ports: ["8765:8765"]`，env 注入 `MCP_GATEWAY_TOKEN`（新生成强随机）、`MCP_API_KEY`（共享，写 172 `.env` **不入库**）、`AI_SERVICE_URL=http://ai-service:8000`、`MCP_GATEWAY_TOKEN_TTL=1500`。
2. 生成强随机 `MCP_GATEWAY_TOKEN`，带外分发同事（非入库）。
3. 172：`docker compose up -d mcp-gateway`；`curl http://172.19.3.136:8765/health/health` 返 `ok`。
4. 2 个同事真实 e2e：`.mcp.json` 仅配 `X-Gateway-Token` + 自己 `X-Entity-ID` → 只读 6 工具返真实数据；`save_workhour` confirm=False 预览 → confirm=True 写库 → `/api/workhour` 可查 + 审计日志含本人、无 token。
5. 跨人抽验：A 的 `X-Entity-ID` 填 B 的钉钉ID → 确认以 B 身份写（验证 C1 行为符合预期，**非 bug**，见记忆 `project_mcp_gateway_c1_risk_accepted`）。

---

## Self-Review

**Spec 覆盖：** spec §4 抽函数 → Task 1；§5 Identity.entity_id/缓存/resolve_identity/forward 401 重换 → Task 2（缓存/resolve）+ Task 4（401 重换）；§6 中间件 X-Entity-ID + 401/502 → Task 3；§6 save_workhour docstring → Task 5；§7 日志铁律 → Task 3（502 不回显、日志记 type 不记密钥）+ Task 4（forward 日志仅 user_id）+ Task 6 Step 2 核验；§8 测试 → 各 Task TDD 步 + Task 6；§9 部署 → 部署章节；§10 验收 → Task 6 + 部署章节。无 spec 要求缺任务。

**占位扫描：** 无 TBD/TODO；每步含完整代码与确切命令。`<管理员发>`/`<自己钉钉userid>`/`<新生成强随机>` 仅出现在部署章节面向人的配置说明，非代码占位。

**类型一致性：** `fetch_service_account_token(entity_id, api_key, *, ai_service_url, role_key, fallback_entity_type) -> tuple[str,str,str]` 在 Task 1 定义，Task 2 `resolve_identity` 与 Task 4 测试 mock 签名一致；`Identity(user_id, entity_type, auth_token, entity_id)` Task 2 定义后 Task 3/4 一致使用；`resolve_identity`/`_evict`/`_TOKEN_CACHE`/`MCP_GATEWAY_TOKEN_TTL`/`MCP_API_KEY` 命名贯穿一致；测试 helper `_app_resolved` Task 3 引入后 Task 内自洽。
