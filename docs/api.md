# AI 智能助手服务 — API 文档

> 在线交互式文档（Swagger UI）：`http://localhost:8000/docs`
> OpenAPI JSON：`http://localhost:8000/openapi.json`
> 更新日期：2026-03-26

---

## 认证说明

所有接口通过 SpringBoot 网关代理，网关完成 JWT 验证后将以下信息注入请求头：

| 请求头 | 类型 | 说明 |
|--------|------|------|
| `X-User-ID` | string | 当前登录用户 ID |
| `X-Entity-Type` | string | 用户角色：`employee` / `deptAdmin` / `deptSubAdmin` / `regionAdmin` / `companyAdmin` / `superAdmin` |
| `X-Department-ID` | string | 用户所属部门 ID |
| `Authorization` | string | 原始 Bearer Token，工具调用时透传给 SpringBoot |

> 直接调试时（绕过 SpringBoot）需手动添加以上请求头。

---

## 接口列表

### 1. AI 对话（核心）

#### POST /api/ai/chat/stream — SSE 流式对话

发送用户消息，以 SSE 事件流返回 AI 响应。

**请求体**

```json
{
  "message": "查一下我本周的工时",
  "session_id": "session-uuid-optional"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | ✅ | 用户消息 |
| `session_id` | string | ❌ | 会话 ID，不传则自动生成 |

**响应格式（SSE 事件流）**

每个事件格式：
```
event: <事件类型>
data: <JSON 字符串>
```

| 事件类型 | 触发时机 | data 字段 |
|---------|---------|-----------|
| `start` | 请求开始 | `{message, session_id}` |
| `thinking` | 正在分析/搜索 | `{message}` |
| `tool_call` | 即将调用工具 | `{tool_name, message}` |
| `response` | 返回结果 | `{message}` 或 `{result, tool_name}` |
| `chart` | 工具结果可视化（仅当数据适合图表时） | `{echarts_option, chart_type}` 或 `{chart_type: "table", fallback_table: [...]}` |
| `error` | 发生错误 | `{message}` |
| `done` | 请求完成 | `{message, session_id}` |

**chart 事件详细说明**

`chart` 事件紧跟在 `response` 事件之后发送，当工具返回的数据满足以下条件时触发：

1. 数据为表格格式（≥2 行数据）
2. 至少包含一列数值类型（int/float）
3. 单值/纯文本结果不触发

**chart 事件 data 字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定值 `"chart"` |
| `chart_type` | string | `"bar"` / `"line"` / `"pie"` / `"table"` |
| `echarts_option` | object | ECharts 5.x 标准 option（bar/line/pie 时） |
| `fallback_table` | array | 行数 > 50 时降级，返回原始数据行 |

**chart 事件示例**

```json
{
  "type": "chart",
  "chart_type": "bar",
  "echarts_option": {
    "title": {"text": "各部门工时统计"},
    "tooltip": {},
    "xAxis": {"type": "category", "data": ["研发部", "产品部", "测试部"]},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "data": [120, 80, 60]}]
  }
}
```

**示例（curl）**

```bash
curl -N -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user-001" \
  -H "X-Entity-Type: employee" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "查一下我本周的工时", "session_id": "test-001"}'
```

---

### 2. 健康检查

#### GET /api/ai/health — 服务健康状态

**响应示例**

```json
{
  "status": "healthy",
  "components": {
    "llm": true,
    "redis": true,
    "milvus": true,
    "database": true
  },
  "timestamp": "2026-03-26T10:00:00"
}
```

| 组件 | 为 false 时影响 |
|------|----------------|
| `llm` | 无法进行 AI 对话 |
| `redis` | 无会话记忆，功能降级 |
| `milvus` | 知识库降级为 FAISS 内存检索 |
| `database` | 无法记录审计日志 |

---

### 3. 审计日志（管理员）

> 需要 `X-Entity-Type` 为管理员角色，否则返回 403。

#### GET /api/ai/audit — 查询审计日志

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_id` | string | - | 按用户 ID 过滤 |
| `session_id` | string | - | 按会话 ID 过滤 |
| `intent` | string | - | 按意图过滤：`tool_execution` / `knowledge_qa` / `general_chat` |
| `status` | string | - | 按状态过滤：`success` / `error` / `rejected` |
| `start_time` | string | - | 起始时间，格式 `YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM:SS` |
| `end_time` | string | - | 结束时间，同上 |
| `page` | int | 1 | 页码（从 1 开始） |
| `page_size` | int | 20 | 每页条数，最大 100 |
| `detail` | bool | false | 是否返回详细字段（ai_response、tools_called 等） |

**响应示例**

```json
{
  "status": "success",
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 150,
    "total_pages": 8
  },
  "data": [
    {
      "id": 1,
      "session_id": "sess-abc",
      "user_id": "user-001",
      "user_message": "查一下我本周的工时",
      "intent": "tool_execution",
      "route_type": "tool_executor",
      "tool_count": 1,
      "duration_ms": 1230,
      "model_name": "qwen-turbo",
      "status": "success",
      "error_message": null,
      "request_time": "2026-03-26T09:30:00"
    }
  ]
}
```

#### GET /api/ai/audit/stats — 审计统计摘要

**查询参数**

| 参数 | 说明 |
|------|------|
| `start_time` | 统计起始时间 |
| `end_time` | 统计结束时间 |

**响应示例**

```json
{
  "status": "success",
  "data": {
    "total_requests": 1500,
    "avg_duration_ms": 1850.5,
    "by_status": {
      "success": 1450,
      "error": 35,
      "rejected": 15
    },
    "by_intent": {
      "tool_execution": 900,
      "knowledge_qa": 400,
      "general_chat": 200
    }
  }
}
```

---

### 4. 记忆管理

#### GET /api/memory/user/{user_id} — 查询用户长期记忆

```bash
curl http://localhost:8000/api/memory/user/user-001 \
  -H "X-User-ID: user-001"
```

#### DELETE /api/memory/user/{user_id} — 清除用户长期记忆

```bash
curl -X DELETE http://localhost:8000/api/memory/user/user-001 \
  -H "X-User-ID: user-001"
```

#### DELETE /api/memory/session/{session_id} — 清除会话短期记忆

```bash
curl -X DELETE http://localhost:8000/api/memory/session/sess-abc \
  -H "X-User-ID: user-001"
```

---

### 5. 数据库初始化

#### POST /api/db/init — 初始化数据库表

首次部署时执行一次，自动创建 `conversation_logs` 和 `ai_sessions` 表。

```bash
curl -X POST http://localhost:8000/api/db/init
```

**响应**

```json
{"status": "ok", "message": "数据库表初始化完成"}
```

---

### 6. RAG 知识库

#### POST /api/rag/reload — 重新加载知识库

知识库文档更新后调用，无需重启服务。

```bash
curl -X POST http://localhost:8000/api/rag/reload
```

---

## 错误码说明

| HTTP 状态码 | 含义 |
|------------|------|
| 200 | 成功 |
| 400 | 请求参数错误（如时间格式不对） |
| 403 | 权限不足（非管理员访问管理接口） |
| 500 | 服务器内部错误 |

SSE 流中的 `error` 事件对应业务错误（如 LLM 超时、工具调用失败、权限拒绝），HTTP 状态码仍为 200。

---

## 工具列表

AI 助手内置以下工具，由 LangGraph 根据用户意图自动选择调用：

| 工具名 | 说明 | 关键参数 |
|--------|------|---------|
| `query_timesheet` | 查询工时记录 | user_id, start_date, end_date |
| `query_project` | 查询项目信息 | project_id（可选） |
| `compute_statistics` | 统计分析工时 | filters, statistics_type |
| `generate_weekly_report` | 生成周报 | user_id, week |
| `save_workhour` | 填报工时 | project_id, date, duration, description |
