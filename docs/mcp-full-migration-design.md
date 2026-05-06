# 14 工具 MCP 化完整迁移设计文档

> 版本：v1.0
> 日期：2026-05-06
> 作者：ai-service 工程团队
> 背景：C 方案已完成 4 个 kb_* 工具 MCP 化，本文档解决业务工具（有状态、有权限、有 SpringBoot 依赖）的 MCP 化工程路径

---

## 1. TL;DR

把 ai-service 的 14 个工具全部通过 Model Context Protocol（MCP）暴露，核心价值在于**协议化解耦**——工具实现与消费端（Claude Code、Cursor、IDE 插件、自动化脚本）解绑，一次封装、多处复用，未来任何支持 MCP 的客户端均可零成本接入工时管理系统能力。真正考验工程能力的是 3 个跨越进程边界的难题：权限上下文（HTTP header 无法穿透 MCP 协议）、参数解析缓存（进程级 LRU 单例跨进程失效）、SpringBoot 依赖注入（TaskExecutor 的 client pool 不在 MCP server 进程内）。本文档给出 3 套备选方案，并选定「内部 HTTP 转发」作为 Phase 1 PoC 路线——ai-service 维持单点权威源，MCP server 只做协议薄壳，验证 MCP 协议层能穿透业务边界。

---

## 2. 现状梳理

| # | 工具名 | 类别标记 | 说明 |
|---|--------|----------|------|
| 1 | `query_timesheet` | 有权限 / 有 SpringBoot 依赖 | 工时查询，HTTP 调 SpringBoot |
| 2 | `query_project` | 有权限 / 有 SpringBoot 依赖 | 项目查询 |
| 3 | `compute_statistics` | 有权限 / 有 SpringBoot 依赖 | 统计分析 |
| 4 | `generate_weekly_report` | 有权限 / 有 SpringBoot 依赖 | 周报生成 |
| 5 | `save_workhour` | 有权限 / 有 SpringBoot 依赖 / **写操作** | 单条工时填报 |
| 6 | `batch_save_workhour` | 有权限 / 有 SpringBoot 依赖 / **写操作** | 批量工时填报 |
| 7 | `sql_query` | 有权限 / **直连 MySQL** | SQL Agent，不走 SpringBoot |
| 8 | `kb_outline` | 无状态 / 无权限 / 纯只读 | C 方案已完成 |
| 9 | `kb_keyword_search` | 无状态 / 无权限 / 纯只读 | C 方案已完成 |
| 10 | `kb_semantic_search` | 无状态 / 无权限 / 纯只读 | C 方案已完成 |
| 11 | `kb_read_section` | 无状态 / 无权限 / 纯只读 | C 方案已完成 |
| 12 | `knowledge_qa` | 无状态 / 无权限 / 纯只读 | RAG 问答，内部调 kb_* |
| 13 | `general_chat` | 无状态 / 无权限 / 纯只读 | 通用对话，纯 LLM |
| 14 | `approve_workhour` | 有权限 / 有 SpringBoot 依赖 / **写操作** | 工时审核 |

**分类统计**：
- 无状态只读：8 个（4 kb + knowledge_qa + general_chat + 未来可拆分的只读工具）
- 有 SpringBoot HTTP 依赖：10 个
- 有权限分级：10 个
- 直连 MySQL：1 个（sql_query）
- 写操作：3 个（save_workhour、batch_save_workhour、approve_workhour）

---

## 3. 三大工程难点 + 解法

### 3.1 权限传递：HTTP Header → MCP 协议

**问题**：ai-service 现有链路中，SpringBoot 网关把 `X-User-ID`、`X-Entity-Type`、`Authorization` 注入 HTTP header，PermissionValidator + TaskExecutor 依赖这些头完成角色校验。MCP 协议（stdio transport）没有 header 概念，身份信息怎么穿到 MCP server？

**方案对比**：

| 方案 | 原理 | 适用场景 | 缺点 |
|------|------|----------|------|
| A. MCP Resource | 服务端注册 `auth://current-user` resource，客户端每次 `call_tool` 前先 `read_resource` 获取 token | 多租户、多用户共享一个 MCP server | 需要客户端配合，协议支持度待验证 |
| B. 环境变量注入 | MCP server 启动时从 env 读 `MCP_USER_ID` / `MCP_AUTH_TOKEN`，单进程单用户 | **单租户 / PoC 阶段** | 多用户场景需要起多个 server 进程 |
| C. 显式参数传递 | 每个 tool 的 schema 增加 `user_id` / `auth_token` 字段 | 最简单，最可控 | token 暴露在 tool 参数里，有泄露风险 |

**推荐**：Phase 1 PoC 用 **B（env 注入）**，因为 ai-service 当前是单租户部署（企业内网，一个 Docker 容器服务一个组织）。生产化后迁移到 **A（MCP Resource）** 或 **C（显式参数 + 加密）**。

```python
# timesheet_mcp_server.py — env 注入示例
USER_ID = os.getenv("MCP_TEST_USER_ID", "")
ENTITY_TYPE = os.getenv("MCP_TEST_ENTITY_TYPE", "employee")
AUTH_TOKEN = os.getenv("MCP_TEST_AUTH_TOKEN", "")

@mcp.tool()
async def query_timesheet(member_id: str | None = None, ...) -> str:
    # 每次调用把 env 里的身份通过 HTTP header 转发给 ai-service
    headers = {
        "X-User-ID": USER_ID,
        "X-Entity-Type": ENTITY_TYPE,
        "X-Auth-Token": AUTH_TOKEN,
    }
```

### 3.2 参数解析跨进程：ParamResolver 的 LRU 缓存

**问题**：`param_resolver.py` 使用进程级字典 `_resolve_cache: Dict[str, Optional[str]] = {}` 缓存「项目名→ID」解析结果。MCP server 是独立进程，缓存全部失效，每次调用都要重新查 SpringBoot。

**方案对比**：

| 方案 | 原理 | 改动量 | 性能影响 |
|------|------|--------|----------|
| A. Redis 共享缓存 | 把 `_resolve_cache` 从 `dict` 改 `redis-py`，key 格式不变 | **1 行替换**（`_resolve_cache = redis.Redis(...)`）+ 序列化 | 多一次 Redis RTT（< 1ms） |
| B. MCP server 启动时同步 | server 启动时拉取全量项目/用户列表到内存，本地 dict 缓存 | 需新增同步接口 | 启动慢，数据滞后 |
| C. 不复用 ParamResolver | MCP server 直接透传原始参数，让 ai-service 做解析 | 零改动，但失去 MCP 层预处理能力 | ai-service 压力不变 |

**推荐**：**C（不复用，直接透传）** 配合「内部 HTTP 转发」架构。理由：MCP server 是薄壳，参数解析仍在 ai-service 内完成，缓存继续生效。如果未来要把工具真正迁出 ai-service，再考虑 A（Redis 共享）。

```python
# internal_tools.py — ai-service 侧接收透传参数
@router.post("/{tool_name}")
async def call_internal_tool(tool_name: str, params: dict, ...):
    # params 直接传给 tool.handler，ParamResolver 在 handler 内自然生效
    result = await tool.handler(**params, auth_token=x_auth_token)
```

### 3.3 依赖注入：TaskExecutor 的上下文怎么重建

**问题**：TaskExecutor 在 ai-service 进程内把 `auth_token`、`SpringBoot client`、`PermissionValidator` 注入每个工具。MCP server 独立进程，没有这些依赖。

**三个选项**：

| 选项 | 描述 | 评价 |
|------|------|------|
| 1. 进程内重建 | MCP server 自己初始化 httpx client、读 SpringBoot URL env、重建权限校验 | 重写一遍 TaskExecutor + PermissionValidator，违背 DRY |
| 2. 转发到 ai-service `/internal/tools/{name}` | MCP server 只做协议转换，实际执行仍在 ai-service | **最优**：0 重写，单点权威 |
| 3. 转发到 SpringBoot 直接 | 跳过 ai-service，MCP server 直接调 SpringBoot API | 丢失 ParamResolver、PermissionValidator、格式化逻辑 |

**选定：选项 2（内部 HTTP 转发）**

```python
# timesheet_mcp_server.py — 薄壳转发
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.post(
        f"{AI_SERVICE_URL}/api/internal/tools/query_timesheet",
        json=params,
        headers={"X-User-ID": USER_ID, "X-Entity-Type": ENTITY_TYPE, "X-Auth-Token": AUTH_TOKEN},
    )
    return json.dumps(response.json(), ensure_ascii=False, indent=2)
```

```python
# internal_tools.py — ai-service 侧接收并执行
@router.post("/{tool_name}")
async def call_internal_tool(
    tool_name: str,
    params: dict,
    x_user_id: str = Header(..., alias="X-User-ID"),
    x_entity_type: str = Header(..., alias="X-Entity-Type"),
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
):
    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_name}")

    # 注入 user_context（ai-service 现有协议）
    params.setdefault("user_context", {})
    params["user_context"]["user_id"] = x_user_id
    params["user_context"]["entity_type"] = x_entity_type
    params["user_context"]["auth_token"] = x_auth_token

    result = await tool.handler(**params)
    return {"success": True, "result": result}
```

---

## 4. 三期渐进式迁移路线

### Phase 1（已完成 + 本 PoC，1 周）

- **已完成**：4 个 `kb_*` 工具（C 方案）
- **本 PoC**：`query_timesheet` MCP 化，验证「内部 HTTP 转发」方案可行
- 产出：本文档 + `timesheet_mcp_server.py` + `internal_tools.py`

### Phase 2（2 周）：只读业务工具

- 工具：`query_project`、`compute_statistics`、`generate_weekly_report`、`sql_query`、`knowledge_qa`
- 工作：每个工具新建对应的 `_mcp_server.py`，模式同 timesheet（薄壳转发）
- 特殊：`sql_query` 直连 MySQL 不走 SpringBoot，但仍在 ai-service 内执行，转发方案同样适用

### Phase 3（4 周）：写类工具

- 工具：`save_workhour`、`batch_save_workhour`、`approve_workhour`
- 额外设计：
  - MCP 层增加 `dry_run: bool = True` 参数（默认只预览不写入）
  - 写操作需要二次确认（MCP Resource 或客户端弹窗）
  - 审计日志：所有写操作经 ai-service 写入 `audit_log` 表，MCP 调用与 LangGraph 调用统一追踪

---

## 5. 每期验收标准

### Phase 1
- [ ] Claude Code `/mcp` 可见 5 个工具（4 kb + 1 timesheet）
- [ ] `query_timesheet` 实测返回真实工时数据（非 mock）
- [ ] ai-service 日志确认 `/api/internal/tools/query_timesheet` 被命中
- [ ] 性能：MCP 冷启动 < 5s（无 RAG 预热），工具调用 RTT < 500ms（含一次内部 HTTP）

### Phase 2
- [ ] 9 个只读工具全部 MCP 化，Claude Code 可见
- [ ] 原有 LangGraph agent 链路 100% 兼容（ToolRegistry 不动）
- [ ] E2E 测试集 M1-M5 全部通过（原有测试不破裂）

### Phase 3
- [ ] 3 个写工具 MCP 化，带 `dry_run` 默认保护
- [ ] 审计日志覆盖所有 MCP 写操作
- [ ] 安全评审通过（写权限不对外网暴露，nginx 限制 `/api/internal/*` 源 IP）

---

## 6. 风险与回退

| 风险 | 影响 | 回退方案 |
|------|------|----------|
| MCP server 进程崩溃 | 外部客户端不可用 | ai-service 主链路（LangGraph → ToolRegistry）完全不受影响，平行运行 |
| 内部 HTTP 接口被滥用 | 安全 | nginx 限制 `/api/internal/*` 只允许 localhost / Docker 内网；生产环境配 IP 白名单 |
| 性能不达标（RTT > 500ms） | 用户体验差 | 增加 MCP server 与 ai-service 的连接池复用（httpx.AsyncClient 持久化） |
| MCP 协议版本不兼容 | 客户端升级后断连 | 锁定 `mcp>=1.0.0`，升级前在 staging 验证 |
| 任何一期失败 | 项目延期 | **核心原则**：ai-service 主链路保留原 ToolRegistry 不动，MCP server 是平行系统，失败即回退到原有架构 |

---

## 7. 不做的事

1. **不破坏现有 ai-service 架构** — LangGraph agent loop、ToolRegistry、TaskExecutor、PermissionValidator 全部不动
2. **不重写 ToolRegistry** — 内部接口直接复用现有 `tool_registry.get_tool()` + `tool.handler()`
3. **不改现有业务工具代码** — `app/tools/*.py` 零修改，只新增 `app/api/internal_tools.py`
4. **不把工具真正迁出 ai-service** — 本期只做协议薄壳，不做进程级拆分（那是 Phase 4 的事）
5. **不引入新的依赖注入框架** — 内部 HTTP 转发天然解决依赖问题，无需 DI 容器
6. **不改 LangGraph 主流程** — SSE 输出格式、节点路由、RAG 调用链路全部保持原样

---

## 附录：工具分类速查表

```
┌─────────────────────┬──────────┬──────────┬─────────────┬──────────┐
│ 工具                │ 无状态   │ 有权限   │ SpringBoot  │ 直连DB   │
├─────────────────────┼──────────┼──────────┼─────────────┼──────────┤
│ kb_outline          │ ✓        │ ✗        │ ✗           │ ✗        │
│ kb_keyword_search   │ ✓        │ ✗        │ ✗           │ ✗        │
│ kb_semantic_search  │ ✓        │ ✗        │ ✗           │ ✗        │
│ kb_read_section     │ ✓        │ ✗        │ ✗           │ ✗        │
│ knowledge_qa        │ ✓        │ ✗        │ ✗           │ ✗        │
│ general_chat        │ ✓        │ ✗        │ ✗           │ ✗        │
│ query_timesheet     │ ✗        │ ✓        │ ✓           │ ✗        │
│ query_project       │ ✗        │ ✓        │ ✓           │ ✗        │
│ compute_statistics  │ ✗        │ ✓        │ ✓           │ ✗        │
│ generate_weekly_report│ ✗      │ ✓        │ ✓           │ ✗        │
│ sql_query           │ ✗        │ ✓        │ ✗           │ ✓        │
│ save_workhour       │ ✗        │ ✓        │ ✓           │ ✗ (写)   │
│ batch_save_workhour │ ✗        │ ✓        │ ✓           │ ✗ (写)   │
│ approve_workhour    │ ✗        │ ✓        │ ✓           │ ✗ (写)   │
└─────────────────────┴──────────┴──────────┴─────────────┴──────────┘
```
