# MCP 工具使用说明（内部）

> 面向：在 Claude Code / Cursor / Claude Desktop 里使用本项目 MCP 工具的团队成员。
> 不是对外接入文档；MCP 协议层设计见 `docs/mcp-full-migration-design.md`，写工具安全评审见 `docs/superpowers/specs/2026-05-17-mcp-phase3-save-workhour-write-security-design.md`。

## 1. 这是什么

把工时系统的查询/填报能力，通过 MCP 协议暴露给 AI 客户端。注册在项目根 `.mcp.json`，Claude Code 在本项目目录启动时**自动加载**，无需手动配置 server。

## 2. 架构（两类 server，依赖不同）

```
A 类（7 个，薄壳）：MCP client → *_mcp_server.py → POST ai-service:8000 /api/internal/tools/{tool} → SpringBoot/MySQL
B 类（1 个，直连）：MCP client → kb_mcp_server.py → Milvus + 本地 knowledge-base/
```

| server 名 | 工具 | 类 | 依赖 | 需要 JWT |
|---|---|---|---|---|
| workhour-knowledge-base | kb 大纲/检索/读段 | B | Milvus(172.19.3.136:19530) + 本地 KB_PATH | 否 |
| workhour-knowledge-qa | 知识库问答 | A | ai-service:8000 | 是 |
| workhour-timesheet | 工时查询 | A | ai-service:8000 → SpringBoot | 是 |
| workhour-project | 项目查询 | A | 同上 | 是 |
| workhour-statistics | 统计分析 | A | 同上 | 是 |
| workhour-weekly-report | 周报生成 | A | 同上 | 是 |
| workhour-sql-query | 自然语言 SQL | A | 同上 | 是 |
| **workhour-save** | **单条工时填报（写）** | **A** | 同上 | **是（缺省空=拒写，安全默认）** |

> A 类 server 在 `MCP_TEST_AUTH_TOKEN` 为空时会直接返回 `MCP server not configured` 错误（不是静默失败）。只有 B 类（knowledge-base）不需要 token。

## 3. 前提依赖

| 用哪类工具 | 需要起什么 |
|---|---|
| 只用 knowledge-base | Milvus 可达 + 本地 `knowledge-base/` 存在（**不**需要 ai-service） |
| 用其余任意工具 | ai-service 跑在 `http://localhost:8000` |

**启动 ai-service**（在 `ai-service/` 目录）：

```bash
./start.sh          # 或 docker-compose up -d
```

确认就绪：浏览器打开 `http://localhost:8000/docs`，能看到 Swagger 即正常。

## 4. 配置身份（A 类工具必需）

编辑项目根 `.mcp.json`，对应 server 的 `env` 填两项：

```jsonc
"workhour-timesheet": {            // 或 workhour-save 等任意 A 类
  "env": {
    "MCP_TEST_USER_ID": "<你的用户ID>",
    "MCP_TEST_AUTH_TOKEN": "<合法 SpringBoot JWT>",
    // 其余 env 保持不动
  }
}
```

- **JWT 怎么拿**：见 `ai-service/CLAUDE.md` 的「获取 JWT Token」一节（浏览器 DevTools 复制，或 `POST /api/authenticate`）。JWT 有有效期，过期需重取。
- 改完 `.mcp.json` 后**重启 MCP 客户端**（重开 Claude Code 会话）使 env 生效。
- **不要把真实 JWT 提交进 git**。仓库里 token 字段默认留空就是这个原因。

## 5. 怎么用

配好后，直接用自然语言让助手做事，它会自动选对应 MCP 工具：

- "查我这周的工时" → workhour-timesheet
- "出差报销标准是什么" → workhour-knowledge-base / knowledge-qa
- "上个月各项目工时占比" → workhour-statistics / sql-query

## 6. 写工具 workhour-save：二段确认协议（重点）

填报会写生产库，所以强制两步，**不要跳过**：

1. **预览**：第一次调用 `confirm=False`（默认）。工具只校验+解析+返回 `preview`，**不写库**。把 preview 原样给用户看。
2. **确认写入**：用户明确同意后，**完全相同参数 + `confirm=True`** 再调一次，才真正落库。

参数：`save_workhour(project_id, date, duration, description="", confirm=False)`
- `project_id` 项目名或 ID（系统会解析）
- `date` `YYYY-MM-DD`
- `duration` 小时，0.5 的整数倍，0.5~10
- 它**只为当前配置身份填报，不接受代填他人**（杜绝跨人写）

多重安全保障：
- MCP 薄壳：`confirm=False → dry_run=true`
- ai-service 端点：写工具未显式 `dry_run=False` 一律强制 `dry_run=true`（纵深防御）
- 真实写权限闸门：SpringBoot 对 `/api/workhour` 的 JWT 鉴权——token 非法/空则写不进
- 每次写尝试有结构化审计日志（attempt/result/blocked，不含 auth_token）

## 7. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `MCP server not configured: 缺少 ... env` | A 类工具未配 `MCP_TEST_USER_ID`/`MCP_TEST_AUTH_TOKEN`，见 §4 |
| 工具调用连接失败 / 超时 | ai-service 没起或不在 8000，见 §3 |
| 改了 `.mcp.json` 不生效 | 没重启 MCP 客户端（重开会话） |
| 填报返回 401/403 / 写不进 | JWT 过期或越权，重新获取 JWT |
| knowledge-base 无结果 | Milvus 不可达，或本地 `knowledge-base/` 缺失 |
| save 一直只返回预览 | 这是预期：必须第二次带 `confirm=True` 才写 |

## 8. 安全须知（团队共识）

- `.mcp.json` 里 token 字段**默认空 = 安全默认**：未配合法 JWT 时 SpringBoot 拒写，防误写生产库。
- 内部端点 `/api/internal/tools/*` 生产环境必须由 nginx 限源 IP，禁止公网直达。
- 写白名单：目前只有 `save_workhour` 允许经内部端点写；新增写工具须显式评审 + 加白名单。
