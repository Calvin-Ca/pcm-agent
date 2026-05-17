# 团队开发者 MCP 共享接入 设计

> 状态：设计定稿待用户 review。用户 2026-05-18 决策：现按方案 2（172 远程 HTTP MCP，VPN 内网可达）实现，后续切方案 3（公网域名），方案 3 作为演进章节记录备查。

## 1. 背景与目标

MCP Phase 1/2/3 已把 8 个工具薄壳化（7 只读 + 1 写 save_workhour），注册在 `.mcp.json`。当前全部是 **stdio transport**：每个开发者本机要 clone 仓库 + 建 venv，A 类工具还要本机能连到 ai-service（本地起或隧道）。团队反馈"太麻烦"。

目标：让技术同事在 Claude Code/Cursor 里用这些 MCP 工具时，**开发者侧零本机环境**（只配 `.mcp.json` 一个 url + token），不碰 172 登录、不配密钥到 172、不破坏内部端点 `/api/internal/tools/*` 不对公网直达的安全前提。

## 2. 现状与约束（均已实测/读源码核实，非推测）

### 2.1 MCP transport 物理约束（决定方案空间）

MCP 协议只有两种 transport，无第三种：
- **stdio**：客户端在开发者本机用子进程拉起 server，本机必须有脚本+环境。
- **远程 HTTP（Streamable HTTP/SSE）**：客户端配一个 `url` 直连，本机零环境，但**服务端必须有一个常驻、可达的 MCP HTTP 服务**。

客户端越省，服务端越重。没有"两端皆零"的方案。要"配个 url 就完事"，代价必然是服务端建并常驻 HTTP MCP 服务。

### 2.2 网络连通性实测（2026-05-18，开发机 → 172.19.3.136）

| 端口 | 结果 | 含义 |
|---|---|---|
| 22 SSH | OPEN | 开发机到 172 网络层通 |
| 8000 ai-service | **CLOSED rc=10061** | 容器只绑 127.0.0.1，开发机不可达 —— 现状"麻烦"根因实锤 |
| 19530 / 29530 Milvus | OPEN | kb 连 172:19530 真实可行已验证 |
| 8099 / 8097 vLLM | OPEN | 172 上服务本就绑内网 IP 对开发机开放 |
| 16379 Redis | OPEN | 172 内网暴露面已存在（现状，非本方案引入） |
| 8765（拟用网关端口） | CLOSED（空闲） | 可用 |

结论：开发机 → 172 内网服务端口这条路通；新增一个绑 172 内网 IP 的网关端口（如 8765）与现有 Milvus/vLLM/Redis 暴露**完全同级**，不抬高暴露基线。方案 2 网络前提成立。

### 2.3 kb 范式真相 + A 类依赖（读源码核实）

- `kb_mcp_server.py` 不是薄壳转发，是**进程内直跑**：`from app.services.kb_navigator import ...` 在本机进程内执行，连 172 的 Milvus/vLLM（业务库自己连）。它"不麻烦"是因为依赖本机可直连，但仍需本机仓库+venv。
- A 类工具（`save_workhour`/`query_timesheet`/`query_project`/`compute_statistics`/`weekly_report`）外部依赖只有：httpx 调 SpringBoot。生产 `SPRINGBOOT_BASE_URL=https://gst.thsware.com`（公网域名）。`param_resolver` 纯 httpx + 进程内 dict 缓存。
- `sql_query` 直连 MySQL（192.168.0.94 内网）。**但走方案 2 的"转发到同机 ai-service"后，由 ai-service 容器执行（其生产环境本就连着 DB），sql_query 例外消失。**

### 2.4 现状安全（Phase 3 已落地）

`internal_tools.py` 端点 chokepoint 已有：写白名单 `INTERNAL_WRITE_TOOLS`、写工具强制 `dry_run` 默认、结构化审计（不记 auth_token）。写操作权威闸门 = SpringBoot 对 `/api/workhour` 的 JWT 鉴权（透传 token）。

## 3. 方案选型

| 方案 | 开发者侧 | 服务端代价 | 安全/风险 |
|---|---|---|---|
| 1. kb 式进程内直跑 | 要仓库+venv | 零改动零风险 | 绕过 Phase 3 端点闸门，最快落地 |
| **2. 172 远程 HTTP MCP（选定）** | 只配 1 个内网 url+token | 172 加 1 Compose service+鉴权 | 不碰公网/nginx/WAF；与 kb 连 172 同级；**Phase 3 闸门全保留** |
| 3. 公网域名远程 HTTP MCP（演进） | 零环境且免 VPN | 改生产 nginx + WAF + 鉴权 | 工程最重风险最高 |

选 **方案 2**。关键优势：网关与 ai-service 同 Compose，转发走容器内网，**Phase 3 端点闸门（白名单/dry_run/审计）完全保留不绕过**，且 sql_query 例外消失。避开方案 3 最大坑（生产 nginx + 华为云 WAF）。VPN 公司已有。开发者不在 172 上有账号、不 SSH 登录，只 HTTP 请求一个端口，与现有 kb 连 172:19530 同级，无新增登录/密钥面。

## 4. 架构与数据流

```
开发者(VPN后,任意网络) Claude Code
  │ .mcp.json: { "type":"http",
  │              "url":"http://172.19.3.136:8765/mcp",
  │              "headers":{ "X-Gateway-Token":"<网关token>",
  │                          "X-Auth-Token":"<我的SpringBoot JWT>",
  │                          "X-User-ID":"<我的id>",
  │                          "X-Entity-Type":"employee" } }
  │ HTTP（到 172 内网:8765，与现 kb 连 172:19530 同级）
  ▼
172 Compose 新 service: http_gateway_server (FastMCP streamable-http, 绑 172 内网 IP:8765)
  │ ① 鉴权中间件：校验 X-Gateway-Token，缺失/错误 → 401
  │ ② 从请求头取 X-Auth-Token / X-User-ID / X-Entity-Type（不再用服务端 env 写死身份）
  │ HTTP 转发（compose 容器内网 http://ai-service:8000）
  ▼
ai-service:8000  POST /api/internal/tools/{tool}   ← Phase 3 闸门在此，白名单/dry_run/审计全保留
  ▼
SpringBoot https://gst.thsware.com (JWT 权威闸门) / MySQL(容器已连)
  ◄─ 结果原路返回
```

## 5. 组件设计

### 5.1 `fastapi-service/mcp_servers/http_gateway_server.py`（新建）

- 用 FastMCP 的 **streamable-http** transport（`mcp.run(transport="streamable-http")` 或等效），单一 server 注册全部工具（一个 url 暴露所有，沿用 kb_mcp_server 单 server 多 tool 范式）。
- 每个 `@mcp.tool()` 实现 = 取当前请求 header 的身份 → 转发到同机 `http://ai-service:8000/api/internal/tools/{tool}`（沿用现有 `timesheet_mcp_server._call_ai_service_tool` 逻辑，`AI_SERVICE_URL` 改指 compose 内 ai-service）。
- 工具集与 schema 复用现有薄壳定义（save_workhour 含 confirm→dry_run 二段确认，Phase 3 Task 4 已有）。

### 5.2 身份模型变更（关键）

现状 stdio：身份由服务端 env（`MCP_TEST_USER_ID/AUTH_TOKEN`）写死。远程 HTTP 多人共享一个服务，**不能**再服务端写死，否则所有人同一身份。

变更：身份由**客户端 `.mcp.json` headers 透传**。网关从入站请求头取 `X-Auth-Token`（每人自己的 SpringBoot JWT）、`X-User-ID`、`X-Entity-Type`，转发给 ai-service 内部端点。契合 Phase 3：写权威闸门是各自 JWT，本就需每人自己的 token。

> **实施前必须验证的技术前提**：FastMCP 的 streamable-http transport 是否支持"每请求读取自定义 HTTP header"（身份透传与网关鉴权都依赖它）。若 FastMCP 不直接暴露请求上下文，回退方案：用 Starlette/ASGI 中间件包裹 FastMCP 的 ASGI app，在中间件层取 header 做鉴权 + 经 contextvar 传给工具实现。实施计划第一步即做此技术验证（spike），失败则在计划内切到回退方案，不阻断整体设计。

### 5.3 鉴权（双层，最简起步）

1. **网关层**：共享高熵 `X-Gateway-Token`（172 env 配置，进服务用）。中间件校验，缺失/错误直接 401，不进转发。防止内网任意人/服务乱调。
2. **业务层**：每人 JWT（SpringBoot 校验），写操作权威闸门，未带/非法 → SpringBoot 拒。

起步用单一共享网关 token（最简）。per-user key / 网关接 JWT 留演进，不在本 spec。

### 5.4 Compose service（`docker-compose.yml`）

新增 1 个 service：复用同仓库代码/镜像，entrypoint 跑 `http_gateway_server.py`，端口**绑 172 内网 IP**（如 `172.19.3.136:8765:8765`，类比 Milvus 宿主映射），不绑回环、不公网。env 注入 `X-Gateway-Token` 期望值、`AI_SERVICE_URL=http://ai-service:8000`。

### 5.5 复用 Phase 3 闸门

网关只做"鉴权 + header 透传 + 转发"，不自己执行业务。实际执行在 ai-service 内部端点，**Phase 3 白名单/强制 dry_run/集中审计完全生效不绕过**。这是方案 2 相对 kb 式的核心安全优势。

## 6. 安全边界与威胁处理

- 内部端点 `/api/internal/tools/*` 仍只在 ai-service 容器、经 compose 内网被网关调，**不绑 172 内网、不公网**。安全前提不破坏。
- 新增暴露面 = 172 内网 :8765，与现有 kb/Milvus/vLLM/Redis 同级（2.2 实测佐证），不抬高基线。
- **审计铁律延续 Phase 3**：网关日志/审计**绝不记录 X-Auth-Token / X-Gateway-Token**。
- 网关 token 管理：高熵随机；泄露则轮换 env + recreate。文档化不入 git。
- 残留风险（记录，非本 spec 处理）：172 内网暴露面整体偏大（Redis 16379 等），建议未来用防火墙限 VPN 段统一收敛——独立运维项。

## 7. sql_query 处理

走方案 2 转发，由 ai-service 容器执行，容器生产本就连 DB（172→116→192 MySQL 隧道现存）。**例外消失，无需单列**，与其他 A 类同等待遇。

## 8. 错误处理

- 网关 token 缺失/错误 → 401，响应体明确提示配置项。
- 转发 ai-service 失败（超时/连接拒绝）→ 透传明确错误，不静默吞。
- 缺 X-Auth-Token → 转发后 SpringBoot 拒写；网关可前置友好提示（不强制）。
- 网关健康检查端点（供 Compose healthcheck）。
- 审计落盘失败不阻断主流程（沿用 Phase 3 原则）。

## 9. 测试计划

单元（pytest，`./fastapi-service/.venv/Scripts/python.exe`）：
- 鉴权中间件：无/错 `X-Gateway-Token` → 401；正确 → 放行。
- header → 转发映射：入站 X-Auth-Token/X-User-ID/X-Entity-Type 正确透传到对 ai-service 的请求（mock httpx 断言）。
- 工具注册：网关注册的工具集与预期一致。
- save_workhour 经网关：confirm=False → 转发 dry_run=true（沿用 Phase 3 薄壳测试范式，不需起全栈）。

验收：上述全绿（独立复跑不采信）；薄壳冒烟过。真实 e2e（开发者机 VPN 后配 .mcp.json → Claude Code 实连 172:8765 → 工具往返 + save 二段确认写库可查 + 审计正确无 token）= 部署后人工验收，写生产库，须用户在场授权，不在无人值守批。

## 10. 部署前提与步骤

- 网络前提已实测成立（§2.2）；外网开发者使用前须先连公司 VPN（已有设施，不在本方案范围）。
- `docker-compose.yml` 加 service → `docker compose up -d`（仅起新 service，不扰其余容器）。**生产 172 动作须用户在场分步执行（规则 7）**。
- 开发者文档（`docs/mcp-usage.md` 增"远程 HTTP 接入"段）：如何在 `.mcp.json` 配 type:http + 三个 header + 从哪拿 JWT/网关 token。

## 11. 非目标（明确排除）

- 不改生产 116 nginx、不碰华为云 WAF（属方案 3）。
- 不做 per-user 网关 key / 网关接 JWT（起步用共享网关 token，演进项）。
- 不收敛 172 整体内网暴露面（独立运维项）。
- 不动 stdio 现有 server（保留，开发者可继续本地 stdio；本方案是新增远程选项）。

## 12. 演进：方案 3（公网域名，未来切换）

架构不变，仅在边缘加一层，平滑演进：
- 116 nginx 新增**独立鉴权 location**（如 `/mcp-gw/`），强 token/限流，反代到 172:8765。
- 为 MCP 单开一条反向隧道端口（如 9902，与 SpringBoot 的 9901 隔离，故障域不耦合）。
- **华为云 WAF**：该域名会 CC 限流（指纹 `HWWAFSESID` + `Server:CW`，见 CLAUDE.md）。MCP 长连接/高频，必须配 WAF 白名单或独立子域，否则开发者随用随封 —— 这是方案 3 最大工程坑，切换前必须先解决并实测。
- 开发者只把 `.mcp.json` 的 url 从 `http://172.19.3.136:8765/mcp` 换成 `https://gst.thsware.com/mcp-gw/mcp`，免 VPN。
- 切换前提：完成 WAF 处理 + nginx 改造（生产高风险，须用户在场）+ 独立隧道稳定性验证。

## 13. 前提验证证据（2026-05-18 实测原始结果）

```
172.19.3.136:22     OPEN     SSH
172.19.3.136:8000   CLOSED rc=10061   ai-service(只绑回环→开发机不可达，现状根因)
172.19.3.136:19530  OPEN     Milvus(kb env 用)
172.19.3.136:29530  OPEN     Milvus宿主映射
172.19.3.136:8099   OPEN     vLLM
172.19.3.136:8097   OPEN     vLLM-embed
172.19.3.136:16379  OPEN     Redis
172.19.3.136:8765   CLOSED   拟用网关端口(空闲可用)
```
探测方式：开发机用项目 .venv python socket.connect_ex，3s 超时，只读不登录不改动。
