# MCP 共享接入 — 团队配置指南（网关模式）

> **接入方式**：HTTP 网关（方案 2 / C1），**零本机环境**——不 clone 仓库、不建 venv、不装依赖
> **网关地址**：`http://172.19.3.136:8765/mcp`
> **认证**：两个 HTTP header（`X-Gateway-Token` + 你的钉钉 `X-Entity-ID`）
> **适用客户端**：Claude Code / Cursor 等支持 `type: http` MCP 的客户端
>
> ⚠️ 旧版本本指南是「clone 仓库 + venv + 起本地进程」的 stdio 模式，已被网关模式取代，**请勿再按旧流程操作**。

---

## 前置条件

1. 网络能访问 `172.19.3.136:8765`（公司内网/可达即可，无需 VPN 以外的特殊配置）
2. 知道自己的**钉钉 userid**（即 `entity_id`，见 Step 1）
3. 向管理员索取**网关令牌** `X-Gateway-Token`（带外分发，不在仓库里）

> 不需要 Python，不需要 clone 代码，不需要 `pip install`。

---

## Step 1：拿到自己的钉钉 entity_id

向管理员索取，或在测试库执行（连接信息找管理员）：

```sql
SELECT real_name, entity_id FROM sys_user WHERE login = '你的登录名';
```

`entity_id` 就是要填的 `X-Entity-ID`（一串数字，**不是登录名**）。

---

## Step 2：配置 `.mcp.json`

在你的 Claude Code / Cursor 的 MCP 配置文件里加一段（**就这 4 行有效内容**）：

```json
{
  "mcpServers": {
    "workhour": {
      "type": "http",
      "url": "http://172.19.3.136:8765/mcp",
      "headers": {
        "X-Gateway-Token": "<向管理员索取>",
        "X-Entity-ID": "<你的钉钉 userid>"
      }
    }
  }
}
```

无 `command`、无 `args`、无本地路径、无 `MCP_API_KEY`（共享密钥在网关侧，客户端不持有）。换 token 过期由网关内部自动处理，你无需关心 JWT。

---

## Step 3：可用工具一览（10 个）

网关聚合了全部工具，连上后客户端会自动发现：

| 工具 | 作用 | 示例话术 |
|------|------|----------|
| `query_timesheet` | 查工时记录（按人/项目/时间，默认本人近 30 天） | “我这个月在工时管理系统项目上报了多少工时” |
| `query_project` | 查项目信息 | “帮我查下叫‘工时管理’的项目” |
| `compute_statistics` | 工时统计/分组汇总 | “统计我本月各项目的工时分布” |
| `generate_weekly_report` | 基于本人工时生成周报 | “生成我这周的周报” |
| `sql_query` | 自然语言复杂分析（后台转 SQL） | “上个月部门里谁的工时填报最少” |
| `kb_outline` | 知识库目录大纲 | “知识库里都有哪些制度文档” |
| `kb_keyword_search` | 知识库 BM25 关键词检索 | “搜一下‘加班补偿’相关条款” |
| `kb_semantic_search` | 知识库语义检索 | “请假流程是怎样的” |
| `kb_read_section` | 精读某文档指定章节 | （通常由上面检索结果引导调用） |
| `save_workhour` | **填报单条工时（二段确认，见下）** | “帮我填报今天工时管理系统项目 2 小时” |

---

## Step 4：填报工时的二段确认流程

`save_workhour` 是写操作，强制两步：

1. **第一次**（`confirm=False`，默认）→ 返回**预览，不写库**。把预览原样确认无误。
2. **确认后**，相同参数 + `confirm=True` 再调一次，才真正写入。

示例（Claude Code）：

```
帮我填报 2026-05-19 工时管理系统项目 2 小时，工作内容“MCP 网关接入”
```

助手会先给预览；你回复“确认”后它再用 `confirm=True` 写入。

> **身份说明**：工时只会落到你 `X-Entity-ID` 对应的人名下，参数里没有“代填目标用户”字段。
> ⚠️ 注意：`X-Entity-ID` 是客户端自声明的，理论上填别人的钉钉 id 就会以别人身份写入——这是已知并被有意识接受的设计权衡，**网关会记录每次调用的 entity_id 用于审计**。请如实填自己的。

---

## Step 5：验证

配好后在客户端里问一句只读的，例如：

```
查一下我最近的工时记录
```

能返回你本人的真实数据即接入成功。

---

## 快速检查清单

| 检查项 | 状态 |
|--------|------|
| `.mcp.json` 有 `"type": "http"` 且 url 是 `http://172.19.3.136:8765/mcp` | |
| `X-Entity-ID` 填的是自己的钉钉 userid（数字，非登录名） | |
| `X-Gateway-Token` 是管理员发的那串 | |
| 网络能访问 `172.19.3.136:8765` | |

---

## 常见问题

### Q1: 401 "missing or invalid X-Gateway-Token"
令牌没填或填错。检查 `headers.X-Gateway-Token` 是否是管理员发的最新值。

### Q2: 401 "missing or invalid X-Entity-ID"
`X-Entity-ID` 为空。填上自己的钉钉 userid。

### Q3: 502 "identity resolution failed"
网关用你的 entity_id 换身份失败。可能 entity_id 不对，或测试环境后端异常。先确认 entity_id 是自己的钉钉 userid；仍不行找管理员看网关日志。

### Q4: 连不上 `172.19.3.136:8765`
确认在公司内网/可达该地址；网关可能未启动，找管理员确认网关服务状态。

### Q5: 我只想用部分工具？
网关聚合在一个 server 下，工具是否调用由助手按你的问题决定，无需手动裁剪；不想用某工具不提它即可。
