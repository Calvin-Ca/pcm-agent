# Agent 探针请求集（快速摸清全链路）

一批覆盖 **agent 全部路由路径** 的自然语言请求，用来快速理解本项目能做什么、怎么路由。
每条都标了：预期走哪条路径、命中哪个工具、是否只读、需要什么角色。

- 数据：`agent_probe_requests.jsonl`（37 条：33 只读 + 4 写）
- 跑只读集：`run_probe.py`（纯标准库，无需 jq）
- 单发调试：`send.sh`（配合断点，单次非流式请求）
- 多轮交互调试：`chat_repl.py`（同一 session_id 连续对话，配合断点，纯标准库）

## 请求会流经的路径（对照 CLAUDE.md「请求处理流程」）

```
POST /api/ai/chat → node_llm_with_tools（Function Calling 主节点）
   ├─ tool      → ParamResolver → PermissionValidator → TaskExecutor → 工具
   ├─ rag       → LangChain RAG（Milvus+BM25，知识问答）
   ├─ chat      → LLM 直接回复
   ├─ plan      → 多步规划（多工具串联 + summarize）
   └─ clarify   → 参数不全时追问
```

## 数据集分类总览

| 类别 | 条数 | 预期路径 | 只读? | 摸清什么 |
|------|-----|---------|------|---------|
| `query_timesheet` | 4 | tool | ✅ | 工时明细查询、模糊时间、本人回退 |
| `query_project` | 3 | tool | ✅ | 项目列表/详情/成员、名→ID 解析 |
| `compute_statistics` | 4 | tool | ✅ | 聚合/排名/占比/趋势（可能触发 `event:chart`） |
| `sql_query` | 3 | tool | ✅※ | 复杂 JOIN/加班时长/反连接 |
| `generate_weekly_report` | 2 | tool | ✅ | 自动周报生成 |
| `knowledge_qa` | 8 | rag | ✅ | 知识库 7 大主题 + 跨文档综合 |
| `general_chat` | 2 | chat | ✅ | 能力自述、闲聊不误触发工具 |
| `multi_step` | 2 | plan | ✅ | 多工具串联 + 归纳总结 |
| `permission` | 2 | tool | 半 | 越权拦截（employee 拿不到全员/无审核权） |
| `clarify` | 2 | tool/clarify | 半 | 参数不全时追问而非乱填 |
| `robustness` | 3 | chat/refuse | ✅ | 提示注入、越界需求、口语鲁棒性 |
| `write_DANGER` | 2 | tool | ⛔写 | 单条/批量填报（dry_run 二段确认） |

※ `sql_query` 只读，但本地 `.env.local` 里 `SQL_AGENT_ENABLED=false`，测这类需先开 SQL Agent 并建 MySQL 隧道，否则会降级/失败。

## 安全分级（关系到能不能对生产隧道跑）

- **read**：只读，随便跑，包括经方案A隧道打生产 SpringBoot。
- **write**（`WRITE-01/02`、`PERM-02`、`CLARIFY-01`）：会写库。
  ⛔ **本地方案A隧道连的是生产库**，这些**只在本地 SpringBoot(路B) 环境跑**。默认脚本跳过。

## 怎么跑

```bash
# 1) 起服务（VSCode F5 或 python main.py），确认 http://localhost:8000/docs 可访问
# 2) 拿 token（经隧道，绕开华为云 WAF）；无 jq 就用 python 取 data.token
TOKEN=$(curl -s -X POST http://127.0.0.1:9900/api/authenticate \
  -H 'Content-Type: application/json' \
  -d '{"username":"159****0206","password":"<密文>","rememberMe":false}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")

# 3) 跑全部只读请求（写操作自动跳过）
TOKEN=$TOKEN USER_ID=<你的userId> python3 run_probe.py

# 只跑某一类 / 指定 id
TOKEN=$TOKEN USER_ID=<userId> CATEGORY=knowledge_qa python3 run_probe.py
TOKEN=$TOKEN USER_ID=<userId> ONLY=TS-01,KB-03 python3 run_probe.py

# 管理员角色（跨用户/统计类）
TOKEN=$TOKEN USER_ID=<userId> ENTITY=deptAdmin python3 run_probe.py
```

输出每条会打印 **期望路径** + **命中工具（`result.tool_name`）+ 回复**，据此核对是否走了预期路径。

> 手动单发也行：直接在 Swagger `http://localhost:8000/docs` → `POST /api/ai/chat` 贴 query。
> 想看真实 SSE 事件流（含 `event:chart`）用 `/api/ai/chat/stream` + `curl -N`。

## 建议的上手顺序

1. `general_chat` → 确认服务通、LLM 可达
2. `knowledge_qa` → 验证 RAG（Milvus/Embedding 链路）
3. `query_timesheet` / `query_project` → 验证工具 + SpringBoot 隧道
4. `compute_statistics` → 看聚合与图表事件
5. `multi_step` / `permission` / `robustness` → 看编排、权限、鲁棒性边界
6. `write_DANGER` → **仅路B**，最后测填报闭环
