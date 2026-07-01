# Git Workhour MCP Server

## 1. What

`git_workhour_mcp_server.py` —— 在 **Claude Code CLI** 里根据本地 git 历史自动生成工时草稿的 MCP。

一个工具：`collect_git_activity(since, until, author?, repo_path?, include_merges?)`
- 就地跑 `git log --numstat`，按**日期**聚合：每天的 commit 列表、增删行数、文件数、首/末 commit 时间跨度。
- 返回内置**启发式 `estimated_hours` 基线**（时间跨度 + commit 数，clamp 0.5~8，取 0.5 整数倍）+ 全部原始数据，供 Claude 结合上下文调整。
- **纯本地只读**：不写库、不连 ai-service、不需要任何鉴权。

## 2. Why 单独一个 server

和其它 7 个 MCP 不同，这个不转发到 ai-service —— 它读的是**用户机器上的 git 仓库**，只有 stdio 子进程就地跑才能拿到。把「读 git（确定性）」和「归纳/估时（判断）」分离：前者进工具，后者交给 Claude。写库仍复用现有 `workhour-save`（`save_workhour`：dry_run → 二段确认 → 审计），不重复造写链路。

## 3. 端到端工作流

```
collect_git_activity(since, until)      # 本 MCP：git → 每日草稿
   → Claude 归纳每天工作内容、按改动量调整工时、让用户指定工时项目
   → save_workhour(confirm=False)       # workhour-save MCP：预览
   → 用户确认
   → save_workhour(confirm=True)        # 真写入 + 审计
```

## 4. 接入 Claude Code

需要一个装了 `mcp[cli]` 的 Python 环境（见 `requirements.txt`）。用 `claude mcp add`：

```bash
claude mcp add git-workhour \
  -- /path/to/python \
     /Users/caiche/code/workhour_agent/fastapi-service/mcp_servers/git_workhour_mcp_server.py
```

或写进 `.mcp.json`（本 server 无需任何 env）：

```json
"git-workhour": {
  "command": "/path/to/python",
  "args": ["<abs>/fastapi-service/mcp_servers/git_workhour_mcp_server.py"],
  "env": { "PYTHONIOENCODING": "utf-8" },
  "disabled": false,
  "autoApprove": []
}
```

配合写库时，同时接入现有 `workhour-save`（需配 `MCP_ENTITY_ID`+`MCP_API_KEY` 或 `MCP_TEST_AUTH_TOKEN`）。

## 5. 参数说明

| 参数 | 说明 |
|------|------|
| `since` | 起始，`YYYY-MM-DD` 或 git 相对时间（`"1 week ago"`）。必填。 |
| `until` | 截止，同格式；留空到现在。 |
| `author` | 留空=按仓库 `user.email` 过滤本人；`"*"`=不过滤；其它=按姓名/邮箱子串过滤。 |
| `repo_path` | 仓库目录，留空=当前工作目录（自动归一到仓库根）。 |
| `include_merges` | 是否含 merge commit，默认 `False`。 |

## 6. 估时基线口径

`estimated_hours = clamp(max(span_hours + 0.5, 0.5 × commit_count), 0.5, 8)`，四舍五入到 0.5。
`span_hours` 为当天首末 commit 时间差。这是**基线不是权威**，Claude 应结合改动量/内容判断后覆盖。每条草稿带 `estimate_basis` 说明推导过程。
