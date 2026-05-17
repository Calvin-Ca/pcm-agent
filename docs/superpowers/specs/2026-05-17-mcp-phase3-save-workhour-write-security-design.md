# MCP Phase 3（save_workhour 写工具）安全评审 + 落地设计

> 状态：设计已定稿（用户 2026-05-17 批准范围=仅 save_workhour、方向=Approach C）。下一步 writing-plans 出实施计划。

## 1. 背景与目标

MCP Phase 1/2 已把只读工具薄壳化暴露（kb_* / timesheet / project / statistics / weekly_report / sql_query / knowledge_qa）。Phase 3 要暴露**写工具**，本设计**仅覆盖 `save_workhour`（单条工时填报）**，作为写路径"鉴权 + dry_run + 审计"范式的最小验证，batch_save / 审批类留后续 Phase。

目标：在不丢失安全性的前提下，把 `save_workhour` 经 MCP 暴露，使外部 agent 可发起单条工时填报，且：
- 默认**不会**直接写库（dry_run 预览优先，显式确认才写）
- 每次写尝试**可追溯**（结构化审计，含被拦截的尝试）
- 越权/跨人写在 MCP 层被中和
- 形成后续写工具**可复用**的端点级范式

## 2. 现状（代码实况，已核验）

- `internal_tools.py`：`POST /api/internal/tools/{tool_name}` 泛型派发，接受**任意已注册工具**；只注入 `X-User-ID/X-Entity-Type/X-Auth-Token` 到 user_context 后直调 handler；**不读 `requires_permission`**；审计仅 1 行 `logger.info`（无 params、无结果）
- `save_workhour.py`：`save_workhour_handler` 校验（duration 0.5 步长 / ≤10h / 日期非未来）+ `resolve_project_id` + work_type/workhour_type 推断后，**直接** `POST {SPRINGBOOT_BASE_URL}/api/workhour`，带 `Authorization: Bearer <auth_token>`；`memberId` 来自 `user_id` 参数；**无 dry_run**；注册时 `requires_permission=True`（当前内部端点路径不生效）
- 真实写权限闸门 = **SpringBoot 对 `/api/workhour` 的 JWT authz**（透传的 auth_token）。无合法 token 写不进

## 3. 缺口（编号贯穿全设计）

| 编号 | 缺口 | 依据 |
|---|---|---|
| G1 | 内部端点绕过 PermissionValidator（`requires_permission=True` 对此路径 no-op） | internal_tools.py 无权限校验 |
| G2 | save_workhour 无 dry_run，LLM/agent 误判即写生产库 | handler 直接 POST |
| G3 | 写操作几乎无审计（无 params/结果/dry_run 标记） | 仅 1 行 info |
| G4 | 身份头可伪造且与 token 不绑定；`user_id`→`memberId` 可替他人填 | 头被全信任；SpringBoot 是否拒跨人写未定 |
| G5 | 泛型端点无读/写隔离、无写白名单 | 任意已注册工具可调 |

## 4. 方向决策

采用 **Approach C（混合）**：横切关注点（写白名单 / dry_run 强制 / 审计）集中在端点 chokepoint（一次修 G1*/G3/G5，适配所有未来写工具）；dry_run 预览语义放 `save_workhour` 域内（"预览这条工时"是领域知识）；MCP server 层强制 dry_run 默认 + 二段确认 + 中和 G4。

（备选 A=全端点：最 DRY 但对只读工具有波及风险；B=全工具内：局部但不可复用且 G1/G5 不修。均不采用，理由见评审讨论。）

> G1 处置说明：真实 authz 权威闸门是 SpringBoot JWT。本设计**不**在内部端点新增 ai-service 侧 PermissionValidator（避免与既有 chat/task_executor 路径的权限语义重复且引入分歧），而是：①端点强制写白名单 + dry_run 默认（纵深防御）；②文档化 nginx 限源 IP + 合法 SpringBoot JWT 为**必备部署前提**；③MCP server 层只为 token 自身身份写（不接受任意 `user_id`），从源头消除 G4 跨人写提权。这是最小且正确的处置，不是遗漏。

## 5. 组件设计

### 5.1 tool_registry：写分类

`register_tool(...)` 增参 `is_write: bool = False`，存入 tool 元数据并提供 `tool_registry.is_write_tool(name) -> bool`。`save_workhour` 注册置 `is_write=True`。其余工具不动（默认 False）。只读端点路径据此**全程 no-op**，零行为变化。

### 5.2 internal_tools.py：端点闸门（仅 `is_write` 生效）

按序：

1. **写白名单**：常量 `INTERNAL_WRITE_TOOLS = {"save_workhour"}`（模块级，便于后续显式扩列）。`is_write_tool(tool_name)` 为真且 `tool_name not in INTERNAL_WRITE_TOOLS` → 落审计（reason=`blocked_not_whitelisted`）后 `HTTPException(403)`。
2. **dry_run 强制默认**：`is_write` 工具，若 `params` 未显式带 `dry_run`（缺省）或 `dry_run` 非显式 `False` → 强制 `params["dry_run"] = True`。仅当调用方显式传 `dry_run=False`（MCP server 在 confirm 时才会这么传）才真写。
3. **审计（前）**：写工具在调用 handler 前落一条结构化审计行（见 5.5），phase=`attempt`。
4. 调 handler（原逻辑）。
5. **审计（后）**：落 phase=`result`，含 success / error / record_id / dry_run。
6. 只读工具：以上 1-3、5 全部跳过，行为与现状逐字节一致。

### 5.3 save_workhour.py：dry_run 语义

`SAVE_WORKHOUR_SCHEMA.properties` 增 `dry_run`（boolean，描述："true=仅预览校验不写入；缺省按调用环境默认"）。`save_workhour_handler`：

- 取 `dry_run = bool(kwargs.get("dry_run", False))`（handler 自身默认 False；"默认安全"由端点 5.2 强制保证，handler 不重复决策，职责单一）
- 执行**全部**现有前置：基础校验 → `_validate_date` → `_validate_duration` → `resolve_project_id` → `_get_workhour_type_for_date` → `resolve_work_type` → 构建 `payload`
- `if dry_run:` 在 POST 前返回：
  ```
  {"success": True, "dry_run": True,
   "preview": {"payload": payload,
               "summary": f"预览（未写入）：{date_str} {duration}h，项目 {project_id}，类别 {workhour_type}/{resolved_work_type}"},
   "message": "以上为预览，确认无误后再提交。"}
  ```
- `else:` 走现有 POST 分支，**逐字节不变**
- 校验/解析失败：dry_run 与否都返回结构化 error（dry_run 下等于"预检失败"，本就不应写）

### 5.4 mcp_servers/save_workhour_mcp_server.py（新建薄壳）

套 `timesheet_mcp_server.py` 范式：

- 暴露工具 `save_workhour(project_id, date, duration, description="", confirm=False)`
- 身份走 env：`MCP_TEST_USER_ID / MCP_TEST_ENTITY_TYPE / MCP_TEST_AUTH_TOKEN`；**不接受**目标 user_id 参数（只为 token 身份写，中和 G4）
- 映射：`dry_run = not confirm`。`confirm=False`（默认）→ 转发 `dry_run=true` → 返回 preview；`confirm=True` → 转发 `dry_run=false` → 真写
- 工具 description 明确指示：**首次调用必 confirm=False，把 preview 原样呈现给用户，得到用户明确同意后才以 confirm=True 重发**
- 转发 `POST {AI_SERVICE_URL}/api/internal/tools/save_workhour`，带 `X-User-ID/X-Entity-Type/X-Auth-Token`
- `.mcp.json` 注册 `workhour-save`，env 中 `MCP_TEST_AUTH_TOKEN` 默认空（未配真实 JWT 则 SpringBoot 拒写，安全默认）

### 5.5 审计记录格式

专用 logger `audit`（复用现有 JSON 日志管道，`AUDIT` 前缀便于 grep / 后续接 DB）：

```
{"tag":"AUDIT","ts":<iso>,"tool":"save_workhour","phase":"attempt|result|blocked",
 "user_id":<x_user_id>,"entity_type":<x_entity_type>,"dry_run":<bool>,
 "params":{"project_id":..,"date":..,"duration":..,"description":..},  # 不含 auth_token
 "success":<bool|null>,"error":<str|null>,"record_id":<str|null>,"reason":<str|null>}
```

铁律：**绝不记录 `auth_token`**。`params` 仅取业务字段白名单（project_id/date/duration/description），避免无意带入敏感值。DB 审计表为后续扩展项，不在本 Phase。

## 6. 数据流

```
MCP client
  └─ save_workhour_mcp_server  (confirm=False 默认 → dry_run=true)
       └─ POST /api/internal/tools/save_workhour  (X-User-ID/Entity-Type/Auth-Token)
            ├─ is_write? → 白名单校验(否→audit blocked + 403)
            ├─ 强制/遵从 dry_run
            ├─ audit(attempt)
            ├─ save_workhour_handler(dry_run)
            │     ├─ dry_run=true : 校验+解析+构 payload，不 POST，返回 preview
            │     └─ dry_run=false: POST SpringBoot (Bearer token)  ← 真实 authz
            └─ audit(result)
  ← preview / 写入结果
  （用户确认后）MCP client 再发 confirm=True → dry_run=false → 真写
```

## 7. 错误处理

- 非白名单写工具：audit(blocked, reason=not_whitelisted) → 403
- dry_run 下校验/解析失败：返回结构化 error，phase=result success=false，**未 POST**
- handler 异常：沿用现有 try/except，audit(result) 记 error
- 审计落盘失败**不得**阻断主流程（与 conversation_logger 同原则：try/except + logger.warning）
- MCP server 转发失败：返回明确错误，不静默吞

## 8. 测试计划

单元（pytest，`./fastapi-service/.venv/Scripts/python.exe`）：

- `tool_registry`：`register_tool(is_write=True)` 后 `is_write_tool("save_workhour") is True`；未传时只读工具 False
- 端点：
  - 写工具未显式 dry_run → handler 收到 `dry_run=True`（mock handler 断言入参）
  - 写工具显式 `dry_run=False` → handler 收到 `dry_run=False`
  - 非白名单写工具 → 403 且产出 audit blocked 行
  - 只读工具 → 不注入 dry_run、不产 audit、行为同现状
  - audit 行含规定字段且**不含 auth_token**
- `save_workhour_handler`：
  - `dry_run=True` → 返回 `dry_run/preview/payload`，**mock httpx 断言 `post` 未被调用**
  - `dry_run=False` → 走 POST（沿用现有 mock 范式）
- MCP server：`confirm=False→dry_run=true` / `confirm=True→dry_run=false` 纯映射逻辑测试（套既有薄壳冒烟范式，不需起服务）

验收：上述全绿（独立复跑，不采信）；薄壳冒烟过；真实 e2e（配置真实 JWT 后 dry_run 预览→confirm 写入→SpringBoot 落库可查）列为部署后人工验收项（写生产库，须用户在场授权，不在无人值守批内）。

## 9. 部署前提（必备控制，文档化）

- 内部端点必须由 nginx 限源 IP，禁止公网直达（沿用 internal_tools.py 头部既有约束）
- `MCP_TEST_AUTH_TOKEN` 必须是合法、范围正确的 SpringBoot JWT —— 这是写权限的**权威闸门**
- save_workhour MCP server 不接受任意目标 user_id，只为 token 身份写

## 10. 非目标（明确排除）

- batch_save_workhour、审批类工具（后续 Phase，需独立安全评审）
- DB 审计表（本 Phase 用结构化日志，DB 表为后续扩展）
- 内部端点新增 ai-service 侧 PermissionValidator（理由见 §4 G1 处置说明）
- 跨用户代填能力（被 §5.4 主动排除以中和 G4）
