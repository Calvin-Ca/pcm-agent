# 方案2 网关 C1 身份增量 设计文档

> 日期：2026-05-19
> 状态：设计定稿，待写实施计划
> 关联：[`2026-05-18-team-mcp-shared-access-design.md`](2026-05-18-team-mcp-shared-access-design.md)（方案2 网关原始设计）、[`2026-05-18-shared-service-account-design.md`](2026-05-18-shared-service-account-design.md)（共享 Service Account）
> 决策依据：记忆 `project_mcp_gateway_c1_risk_accepted`（C1 自声明可冒充风险已被用户有意识接受）

## 1. 背景与目标

方案2 网关（`_gateway_core.py` + `http_gateway_server.py`，已在 main + 172 磁盘）现行身份模型是 **X-Auth-Token JWT 透传**：`GatewayAuthMiddleware` 从 header 取 `X-User-ID/X-Entity-Type/X-Auth-Token` 存 contextvar，`forward_to_ai_service` 原样透传 —— 假设客户端已自带 JWT。这要求每个同事先登录拿 JWT 并填进 `.mcp.json`，token 会过期，未达"零配置"。

用户已选 **C1**：7 个 A 类工具（**含 save_workhour 写**）全部经网关；网关侧持共享 `MCP_API_KEY`；每请求的钉钉 `entity_id` 由客户端 header **自声明**（非认证）；网关据此向 ai-service `/api/internal/auth/mcp-token` 换取**绑定该 entity_id 的 JWT**，复用已完成的 Service Account 取数逻辑。

**目标**：同事 `.mcp.json` 只需 `X-Gateway-Token` + 自己钉钉 `X-Entity-ID` 两个 header，零本机环境、零手动 JWT，即可用全部 7 工具。

**非目标**：不动 stdio 7 server 与 `_service_account.ensure_auth` 的对外行为（仅做行为保持的取数抽取）；不动 ai-service Phase3 闸门（白名单/dry_run/审计在 ai-service 端，不绕过）；不删 stdio；不引入 X-Auth-Token 透传回退（用户 YAGNI 砍掉，全替换）。

## 2. 关键技术事实

- Service Account 响应 `userId` 是 **UUID**（spike 实测 `d1e88d66-cc87-40c7-bbe3-2dff2d093b41`），**≠ 钉钉 entity_id**（`0103163734221037995`）。网关必须用 entity_id 换回 `(token, userId=UUID, entityType)`，转发时用换回的 userId/entityType，**不得**把自声明 entity_id 当 user_id 透传。
- `_service_account.ensure_auth()` 是 env 单身份 + 进程全局缓存，网关是多用户单进程，不能直接复用其缓存/env 绑定；复用的是其 SA 取数（POST mcp-token + 解析 token/userId/entityType + 角色键 `_ROLE_KEY="entityType"` + env 回退）。
- 已实测：172 `/api/internal/auth/mcp-token` 部署可用（空体 400、真凭据 200 返 token/userId/entityType）；172:8000 已 `0.0.0.0` 对外绑定。

## 3. 选定方案

**方案 A**（已对比否决 B「新建 `_gateway_identity.py`」过度拆分无第二消费者、C「每请求改 os.environ 复用 ensure_auth」多用户单进程全局态竞态不安全）：

抽出纯取数函数进 `_service_account.py`；网关 resolver + per-entity_id TTL 缓存写进 `_gateway_core.py`（其本就是网关身份的家，仅 ~95 行）。stdio 与网关共用取数，各自管自己的缓存/身份模型。

## 4. 抽出纯取数函数

`fastapi-service/mcp_servers/_service_account.py` 新增：

```
async def fetch_service_account_token(
    entity_id: str, api_key: str, *,
    ai_service_url: str, role_key: str = _ROLE_KEY,
    fallback_entity_type: str = ENTITY_TYPE,
) -> tuple[str, str, str]:        # (token, user_id, entity_type)
    POST {ai_service_url}/api/internal/auth/mcp-token  json={entity_id, api_key}
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token", "")
    if not token: raise RuntimeError("Service Account 认证返回空 token")
    user_id = data.get("userId", "")
    entity_type = data.get(role_key) or fallback_entity_type   # 解析不到回退，绝不空
    return token, user_id, entity_type
```

无 env 读取、无缓存、无全局态，纯输入→输出。`ensure_auth()` 第 3 分支改为调它（**行为逐字等价**，`tests/test_service_account.py` 现有 12 用例须仍全绿，属行为保持重构）。

## 5. 网关 resolver + per-entity_id 缓存（`_gateway_core.py`）

**新增 env：**
```
MCP_API_KEY           = os.getenv("MCP_API_KEY", "")                       # 网关侧共享密钥
MCP_GATEWAY_TOKEN_TTL = int(os.getenv("MCP_GATEWAY_TOKEN_TTL", "1500"))    # 25min，保守 < 典型30min JWT寿命
```

**`Identity` 加字段：**
```
@dataclass
class Identity:
    user_id: str = ""
    entity_type: str = "employee"
    auth_token: str = ""
    entity_id: str = ""        # 新增：自声明钉钉ID；缓存键 + 401 evict 用
```

**缓存 + resolver：**
```
_TOKEN_CACHE: dict[str, tuple[Identity, float]] = {}   # entity_id -> (Identity, expiry_monotonic)

async def resolve_identity(entity_id: str) -> Identity:
    now = time.monotonic()
    hit = _TOKEN_CACHE.get(entity_id)
    if hit and hit[1] > now:
        return hit[0]
    token, user_id, etype = await fetch_service_account_token(
        entity_id, MCP_API_KEY, ai_service_url=AI_SERVICE_URL)
    ident = Identity(user_id=user_id, entity_type=etype,
                     auth_token=token, entity_id=entity_id)
    _TOKEN_CACHE[entity_id] = (ident, now + MCP_GATEWAY_TOKEN_TTL)
    return ident

def _evict(entity_id: str) -> None:
    _TOKEN_CACHE.pop(entity_id, None)
```
- 缓存键 = 自声明 entity_id，不同同事各自条目互不串。
- `time.monotonic()` 不受系统时钟跳变影响。
- 单进程 asyncio，dict 读改临界区无 await 跨越，无需锁。

**`forward_to_ai_service` 加 ai-service 401 重换一次：**
```
调 ai-service；捕 httpx.HTTPStatusError 且 e.response.status_code == 401：
    _evict(ident.entity_id)
    ident = await resolve_identity(ident.entity_id)
    用新 token 重发一次；仍失败则抛
```
覆盖 TTL 兜底之外的 JWT 过期/失效；只重试一次，避免循环。

## 6. 中间件 header 契约 + save_workhour/C1 + 错误

**同事 `.mcp.json` header 契约（仅两个）：**
```json
{ "mcpServers": { "workhour": {
  "type": "http",
  "url": "http://172.19.3.136:8765/mcp",
  "headers": { "X-Gateway-Token": "<管理员发>", "X-Entity-ID": "<自己钉钉userid>" }
}}}
```
X-User-ID / X-Entity-Type / X-Auth-Token 从客户端契约移除（网关从 SA 响应导出真 userId/entityType）。

**`GatewayAuthMiddleware.dispatch`：**
1. health 路径前缀放行（不变）
2. 校 `X-Gateway-Token`，缺/错 → 401 `{"error":"missing or invalid X-Gateway-Token"}`（不回显任何 token，不变）
3. 读 `X-Entity-ID`，空 → 401 `{"error":"missing or invalid X-Entity-ID"}`
4. `await resolve_identity(entity_id)`
   - 抛 `RuntimeError`（空 token）或 `httpx` 错（SA/SpringBoot 失败）→ 502 `{"error":"identity resolution failed"}`，**不回显** token/key/上游 detail；内部日志记 entity_id + 错误类型，**不记** token/key
5. 成功 → `_IDENTITY.set(ident)`；`finally` reset

**save_workhour（C1，含在内）：**
- 7 工具全经网关。`http_gateway_server.py` 的 `_save_workhour_impl` 参数**不含目标 user_id**（现状已是），写落到 resolve 回来的身份 —— G4「不接受任意 user_id 代填」的结构性保护保留。
- C1 风险：自声明 `X-Entity-ID` 可填他人钉钉ID → 持共享 key 者可冒充任意人写工时。**用户作为 G4 设定者已有意识接受**（记忆 `project_mcp_gateway_c1_risk_accepted`）。spec 显式记录，实施时不得擅自把 save_workhour 排除出网关或重新收紧。
- `save_workhour` docstring 那句"只为 X-Auth-Token 对应的人填报"改为"只为本请求 X-Entity-ID 经 Service Account 换取的身份填报"。

**错误处理汇总：**

| 场景 | 响应 | 回显 |
|---|---|---|
| 缺/错 X-Gateway-Token | 401 | 仅提示缺哪个头 |
| 缺 X-Entity-ID | 401 | 仅提示缺哪个头 |
| SA 换 token 失败（空 token / SpringBoot 4xx5xx / 网络） | 502 "identity resolution failed" | 无 token/key/上游 detail |
| ai-service 返 401 | evict+重换+重试一次；仍 401 则抛 → FastMCP 工具体 try/except 兜 `{"error":...}` | 无 token |
| ai-service 其他非 2xx | `raise_for_status` 抛 → 工具体兜 `{"error":...}` | 无 token |

## 7. 日志铁律

绝不记 `MCP_API_KEY` / `X-Gateway-Token` / SA 换得的 `auth_token`。只记 `entity_id` + `tool_name` + `user_id`(UUID)。现有 `forward ... user={user_id}` 行形保留，新增 resolve/evict 日志同守此律。

## 8. 测试策略

TDD，扩 `fastapi-service/tests/test_gateway_core.py`，httpx-mock（`patch("httpx.AsyncClient.post")`），**不连真网络**：

- 中间件：缺 X-Gateway-Token → 401（现有，保留）；缺 X-Entity-ID → 401；正常 → resolve 被调，contextvar 为换回的 userId(UUID)/entityType
- `resolve_identity`：cache miss → fetch 调一次；TTL 内同 entity_id 再请求 → fetch **不再调**（断言调用次数）；TTL 过期 → 再 fetch；不同 entity_id → 各自缓存条目
- SA 失败（`RuntimeError` / httpx 4xx）→ 中间件 502，响应体断言**无** token/key
- `forward_to_ai_service` 遇 ai-service 401 → `_evict` + 重 resolve + 重发一次（fetch 调两次，第二次 forward 成功）；仍 401 → 抛
- `_service_account`：`fetch_service_account_token` 抽出后，`tests/test_service_account.py` 现有 12 用例仍全绿（行为保持）
- 日志：caplog 断言不含 MCP_API_KEY / token

## 9. 部署章节（非自动化，用户在场，规则7，不在无人值守批）

1. `docker-compose.yml`（172）加 `mcp-gateway` service：跑 `http_gateway_server.py`，端口 `0.0.0.0:8765`，env 注入 `MCP_GATEWAY_TOKEN`（新生成强随机）、`MCP_API_KEY`（共享，进 172 `.env` **不入库**）、`AI_SERVICE_URL=http://ai-service:8000`、`MCP_GATEWAY_TOKEN_TTL`。
2. 生成 `MCP_GATEWAY_TOKEN`（强随机），分发给同事（带外，非入库）。
3. 172 `docker compose up -d mcp-gateway`；`curl http://172.19.3.136:8765/health/health` 通。
4. 2 个同事真实 e2e：仅配两 header → 只读 6 工具返真实数据；save_workhour confirm=False 预览 → confirm=True 写库 → `/api/workhour` 可查 + 审计日志含本人 + 无 token。
5. 跨人抽验：A 的 X-Entity-ID 填 B 的钉钉ID → 确认确实以 B 身份写（验证 C1 行为符合预期，非 bug）。

## 10. 验收

- `fetch_service_account_token` 抽出；`ensure_auth` 现有 12 测试仍全绿（行为保持实证）
- `_gateway_core` resolver/缓存/401 重换/中间件 X-Entity-ID 全单测覆盖并自跑通过
- 日志无密钥（caplog 实证）
- 客户端契约仅 X-Gateway-Token + X-Entity-ID；X-Auth-Token 透传路径已移除
- save_workhour 仍在网关内、参数无目标 user_id（G4 结构性保留）；C1 风险 spec 显式记录
- 部署 + 2 同事 e2e 属用户在场，不在无人值守批
