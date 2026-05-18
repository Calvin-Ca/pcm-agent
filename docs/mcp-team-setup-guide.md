# MCP 共享接入 — 团队配置指南

> **环境**：测试环境 `192.168.2.52:9651`
> **认证方式**：Service Account（MCP_ENTITY_ID + MCP_API_KEY）
> **适用**：Claude Code / Cursor 等支持 MCP 的客户端

---

## 前置条件

1. 安装 Python 3.11
2. 能访问测试环境 `192.168.2.52:9651`
3. 知道自己的**钉钉 userid**（见下方查询方式）

---

## Step 1：拉取代码并创建虚拟环境

```bash
# 克隆仓库
git clone https://github.com/lh229470989/workhour_agent.git
cd workhour_agent

# 创建虚拟环境（在项目根目录）
python -m venv .venv

# Windows 激活
.venv\Scripts\activate

# 安装依赖（只需两个包）
pip install mcp httpx
```

> 只需 `mcp` 和 `httpx` 两个包，不需要装完整的 requirements.txt。

---

## Step 2：查询自己的钉钉 userid

在测试环境数据库执行：

```sql
-- 在 192.168.2.52:3306 workhour 库执行
SELECT real_name, entity_id
FROM sys_user
WHERE login = '你的登录名';
```

或者让管理员帮忙查。`entity_id` 就是你要填的 `MCP_ENTITY_ID`。

**示例**：
```
real_name | entity_id
----------|------------------
张三      | 0103163734221037995
```

---

## Step 3：配置 `.mcp.json`

在你的 **Claude Code 配置目录**（或 Cursor MCP 配置目录）创建/编辑 `.mcp.json`：

### 3.1 确定本地路径

用绝对路径替换下面模板中的 `<YOUR_PATH>`：
- `<YOUR_PATH>/.venv/Scripts/python.exe` → 你刚创建的虚拟环境 Python
- `<YOUR_PATH>/ai-service/fastapi-service/mcp_servers/*.py` → 仓库中的 server 脚本

**示例**（假设 clone 到 `D:/work/workhour_agent`）：
```
D:/work/workhour_agent/.venv/Scripts/python.exe
D:/work/workhour_agent/fastapi-service/mcp_servers/timesheet_mcp_server.py
```

### 3.2 完整配置模板

```json
{
  "mcpServers": {
    "workhour-save": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/save_workhour_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-timesheet": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/timesheet_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-project": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/project_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-statistics": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/statistics_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-weekly-report": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/weekly_report_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-sql-query": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/sql_query_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    },
    "workhour-knowledge-qa": {
      "command": "<YOUR_PATH>/.venv/Scripts/python.exe",
      "args": ["<YOUR_PATH>/fastapi-service/mcp_servers/knowledge_qa_mcp_server.py"],
      "env": {
        "AI_SERVICE_URL": "http://192.168.2.52:9651",
        "MCP_ENTITY_ID": "<你的钉钉userid>",
        "MCP_API_KEY": "375e32525dc7611d983ca4462d65dae931283f570d86c10203ad92f4df371830",
        "MCP_TEST_ENTITY_TYPE": "employee",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

**只读 server**（timesheet/project/statistics/weekly-report/sql-query/knowledge-qa）按需启用，不一定全部配。

---

## Step 4：验证配置

在 Claude Code 中执行：

```
/tools workhour-save save_workhour 项目名="工时管理系统" 日期="2026-05-18" 时长=2 confirm=false
```

**首次调用会**：
1. 自动通过 Service Account 换取 JWT token（内部完成，无需手动操作）
2. 返回预览（不写库）

**用户确认后**，再执行：

```
/tools workhour-save save_workhour 项目名="工时管理系统" 日期="2026-05-18" 时长=2 confirm=true
```

---

## 快速检查清单

| 检查项 | 状态 |
|--------|------|
| `.venv/Scripts/python.exe` 存在 | |
| `mcp` 包已安装（`pip list \| grep mcp`） | |
| `.mcp.json` 中 `MCP_ENTITY_ID` 是自己的钉钉 userid | |
| `AI_SERVICE_URL` 是 `http://192.168.2.52:9651` | |
| 能 ping 通 `192.168.2.52` | |

---

## 常见问题

### Q1: 报错 "MCP server not configured"
`MCP_ENTITY_ID` 或 `MCP_API_KEY` 为空。检查 `.mcp.json` 中这两个字段是否填了值。

### Q2: 报错 "MCP 认证失败" / 401
测试环境可能没启动，或 `entity_id` 不对。确认：
- `AI_SERVICE_URL` 是不是 `http://192.168.2.52:9651`
- `entity_id` 是不是自己的钉钉 userid（不是登录名）

### Q3: 怎么只启用部分工具？
把不用的 server 从 `.mcp.json` 中删掉，或设置 `"disabled": true`。

### Q4: 路径里有中文/空格怎么办？
Windows 下用正斜杠 `/` 或双反斜杠 `\\`，确保 JSON 语法正确。
