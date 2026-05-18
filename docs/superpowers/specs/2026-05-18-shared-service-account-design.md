# 共享 Service Account 认证模块 设计文档

> 日期：2026-05-18
> 状态：设计定稿，待写实施计划
> 关联：[`2026-05-17-mcp-phase3-save-workhour-write-security-design.md`](2026-05-17-mcp-phase3-save-workhour-write-security-design.md)（写安全闸门）、[`2026-05-18-team-mcp-shared-access-design.md`](2026-05-18-team-mcp-shared-access-design.md)（方案2 网关，正交）

## 1. 背景与目标

`save_workhour_mcp_server.py` 已接入 Service Account 认证（`MCP_ENTITY_ID` + `MCP_API_KEY` → 经 ai-service `/api/internal/auth/mcp-token` 代理到 SpringBoot `/api/auth/mcp-token` 换 JWT，懒加载 + 进程级缓存），用户已本地实测跑通。

其余 6 个 A 类只读 MCP server（`timesheet` / `project` / `statistics` / `weekly_report` / `sql_query` / `knowledge_qa`）仍只支持预配 `MCP_TEST_AUTH_TOKEN`。`.mcp.json` 里这些 token 故意留空（安全默认），导致本地调它们必须手动贴 JWT，过期即失效。

经核实，6 个只读薄壳与 `timesheet` **逐字节同构**：各自一份相同的 env 块（`AI_SERVICE_URL`/`USER_ID`/`ENTITY_TYPE`/`AUTH_TOKEN`）、相同的 `_call_ai_service_tool`、相同的 `if not USER_ID or not AUTH_TOKEN` 守卫，**无共享 helper（6 份重复）**。

**目标**：把 Service Account 自取 JWT 逻辑抽成共享模块，7 个 A 类 server（含 save_workhour，全 DRY）统一复用；本地仅凭 `MCP_ENTITY_ID`/`MCP_API_KEY` 即可让全部 7 个 A 类工具自取 token 跑通；保留预配 token 优先回退；凭据入库空占位纪律不破。

**非目标**：不动 MCP 工具面（不新增工具、不改签名/docstring）；不把 `batch_save_workhour` 纳入 MCP（其写安全须独立 Phase 评审）；不动 `kb_mcp_server`（B 类进程内直跑）与方案2 网关（`http_gateway_server`/`_gateway_core`，身份走 header 透传，与本模块正交）。

## 2. 选定方案

**方案 A — 共享模块出"高层转发函数"**（已对比否决 B「只抽鉴权、转发留各自」DRY 不彻底、C「基类/工厂重构骨架」过度设计且动到 per-tool 契约）。

新建 `fastapi-service/mcp_servers/_service_account.py`，对标已有 `_gateway_core.py` 的 `forward_to_ai_service` 范式。7 个 server 删除各自鉴权/转发样板，改 import。

## 3. 共享模块 API

文件：`fastapi-service/mcp_servers/_service_account.py`
依赖：仅 `httpx` + `os` env + `logging`，**无 app 依赖**（纯函数，易单测，不连真网络可 mock）。

```
# env（模块加载时读一次）
AI_SERVICE_URL          AI_SERVICE_URL          默认 http://localhost:8000
MCP_ENTITY_ID           MCP_ENTITY_ID           Service Account 凭据
MCP_API_KEY             MCP_API_KEY             Service Account 凭据
MCP_TEST_USER_ID        MCP_TEST_USER_ID        预配回退
MCP_TEST_ENTITY_TYPE    MCP_TEST_ENTITY_TYPE    默认 employee（entityType 回退兜底）
MCP_TEST_AUTH_TOKEN     MCP_TEST_AUTH_TOKEN     预配回退

# 进程级缓存（模块全局）
_cached_token / _cached_user_id / _cached_entity_type

def auth_configured() -> bool
    True 当 MCP_TEST_AUTH_TOKEN 非空，或 (MCP_ENTITY_ID 且 MCP_API_KEY) 均非空

async def ensure_auth() -> tuple[str, str, str]   # (user_id, entity_type, auth_token)
    优先级（沿用 save_workhour 已验证逻辑）：
      1. 预配 MCP_TEST_AUTH_TOKEN 非空
         → (MCP_TEST_USER_ID or MCP_ENTITY_ID, MCP_TEST_ENTITY_TYPE, MCP_TEST_AUTH_TOKEN)
      2. 进程级缓存命中
         → (_cached_user_id, _cached_entity_type, _cached_token)
      3. SA 自取：POST {AI_SERVICE_URL}/api/internal/auth/mcp-token
         body {entity_id: MCP_ENTITY_ID, api_key: MCP_API_KEY}
         resp.raise_for_status()
         token = resp["token"]（空 → raise RuntimeError("Service Account 认证返回空 token")）
         user_id = resp.get("userId", "")
         entity_type = <spike 确认的角色键> ；缺失/空 → 回退 MCP_TEST_ENTITY_TYPE
         三者写入进程级缓存后返回
      4. 都没有 → ("", "", "")  # 由调用方守卫提前拦，正常不到这

async def call_ai_service_tool(tool_name: str, params: dict) -> dict
    user_id, entity_type, auth_token = await ensure_auth()
    POST {AI_SERVICE_URL}/api/internal/tools/{tool_name}
      json=params
      headers X-User-ID / X-Entity-Type / X-Auth-Token
    resp.raise_for_status()
    return resp.json()
```

进程级缓存说明：每个 stdio server 是独立子进程，各自一份模块实例 → 各自首调拉一次 token、各自缓存，**这是预期**，无需跨进程共享。

## 4. entityType 解析（重点：必须确认真读到）

**前置 spike（实施计划第一个 Task，失败即停报告，不硬推）**：
用本地真 `MCP_ENTITY_ID`/`MCP_API_KEY` 实打一发 `POST {AI_SERVICE_URL}/api/internal/auth/mcp-token`，把**完整响应 JSON 原样落盘存证**（对标派单纪律规则 8 防造假），肉眼确认角色字段真实键名（`entityType` / `role` / `userType` / 或不存在）。**不靠猜、不靠文档。**

**解析规则（spike 出结论后在实现中定死）**：
- `ensure_auth()` 第 3 分支按 spike 确认的键名取角色字段
- **角色字段缺失或为空 → 安全回退 `MCP_TEST_ENTITY_TYPE`（env，默认 `employee`）**。绝不向下游传空 `X-Entity-Type`
- spike 阶段及运行时日志打出"实际 entity_type 取值 + 来源（SA 响应键 / env 回退）"，实施时可核验"确实读下来了"（用户强调点）
- entity_type 连同 token/user_id 一起进缓存

## 5. 7 个 server 迁移

每个 server 同构改动：
- 删除各自 env 块（`AI_SERVICE_URL`/`USER_ID`/`ENTITY_TYPE`/`AUTH_TOKEN`/save_workhour 的 SA 块）
- 删除各自 `_call_ai_service_tool`；删除 save_workhour 的 `_fetch_token_via_service_account`/`_ensure_auth`/缓存全局
- 改 `from mcp_servers._service_account import call_ai_service_tool, auth_configured`（各 server 已有 `sys.path.insert(0, fastapi-service/)`，`mcp_servers` 包内 import 可达）
- 工具函数体守卫 `if not USER_ID or not AUTH_TOKEN` → `if not auth_configured()`，文案统一为 save_workhour 现有 hint（两组认证任一即可）
- **工具签名 / docstring 零改动**（喂 LLM 的契约不动）
- save_workhour 的 `_build_params`（confirm→dry_run、G4 不接受任意 user_id）**保留本文件**，仅转发改走共享 `call_ai_service_tool`

范围边界：
- 改：7 个 A 类 = `timesheet` / `project` / `statistics` / `weekly_report` / `sql_query` / `knowledge_qa` / `save_workhour`
- 不改：`kb_mcp_server`（B 类）、`http_gateway_server` / `_gateway_core`（方案2 网关，正交）、`batch_save_workhour`（无 MCP 壳，写安全留独立 Phase）

## 6. `.mcp.json` 凭据纪律

- 6 个只读 server 的 `env` 各加 `MCP_ENTITY_ID` / `MCP_API_KEY`
- **入库一律空串占位**；本地工作区填真值、**故意不提交**（`git status` 长期 ` M .mcp.json` 是预期态）
- 提交只 `git add .mcp.json` 且入库前 `git diff --cached` 核验凭据字段为空串；**绝不 `git add -A` / `git add .`**
- 关联记忆铁律 `feedback_mcp_json_local_secret.md`

## 7. 错误处理

- `ensure_auth()`：SA 响应 `token` 空 → `raise RuntimeError("Service Account 认证返回空 token")`（沿用原文案）；HTTP 非 2xx → `httpx` 抛，冒泡
- `call_ai_service_tool`：保留 `resp.raise_for_status()`；异常**不在共享模块吞**，由各 server 工具函数体 `try/except` 兜成 `{"error": ...}` JSON（保持现有用户可见行为）
- 守卫前移：`auth_configured()` 为 False → 工具函数直接返回 "MCP server not configured"（统一 hint），**不进 `call_ai_service_tool`**
- 日志铁律：共享模块**只记 `tool_name` + `user_id` + auth 来源**，**绝不记 `auth_token` / `api_key`**（对标 `_gateway_core` 不记 token 铁律）

## 8. 测试策略

TDD，pytest + httpx mock（对标已有 `test_gateway_*` / `test_*_mcp` 范式，**不连真网络**）：

`tests/test_service_account.py`：
- `auth_configured()` 真值表（空 / 仅预配 / 仅 SA / 两者全）
- `ensure_auth()` 三优先级：预配优先、缓存命中只拉一次（断言 httpx 只调一次）、SA 自取解析 token+userId+entityType
- **entityType 回退**：SA 响应含角色键 → 取之；缺失/空 → 回退 env 默认（用户强调核验点，单测钉死）
- SA 响应空 token → raise RuntimeError
- `call_ai_service_tool` 拼对 3 个 header、打对 URL（httpx mock 断言）
- 日志不含 token/api_key（caplog 断言）

迁移回归：每个 server 至少 1 个 mock 用例证明 import 共享模块后转发链路不变（尤重 save_workhour 的 confirm→dry_run、G4 不传 user_id 保持）。

spike 真实 mcp-token 响应原始 JSON 落盘存证（规则 8 防造假）。

## 9. 验收

- 7 个 A 类 server 全部 import `_service_account`，无残留本地鉴权/转发样板
- `tests/test_service_account.py` + 迁移回归用例全绿
- spike 原始响应 JSON 落盘，entityType 键名有据可查
- `.mcp.json` 入库版凭据字段全空串（`git diff --cached` 实证）
- save_workhour 二段确认 / G4 行为经回归用例确认未变
- 本地用户实测：7 个 A 类工具凭 SA 凭据自取 token 跑通（属用户在场动作，不在无人值守批）
