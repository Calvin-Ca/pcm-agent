# P1 任务：修复工具调用 user_id = anonymous

> 前置：CLAUDE.md 生产环境速查已加载。本任务涉及 **ai-service 代码修改 + 重新部署容器**。

## 背景

E2E 测试（2026-04-22）隧道直测响应片段：

```json
{
  "tool_name": "query_timesheet",
  "parameters": {
    "start_date": "2026-04-20",
    "end_date": "2026-04-22",
    "user_id": "anonymous"
  },
  "result": {"success": true, "total_hours": 0.0, "record_count": 0}
}
```

工具被正常调用，但 `user_id` 是 `"anonymous"` 而不是真实登录用户名（159****0206）。后果：查询返回 0 条，用户以为"没数据"，实际是没查到真正的用户工时。

## 已知的数据传递链路

```
JWT (auth header)
  ↓ AIPermissionInterceptor.preHandle()
    request.setAttribute("userId", userId)
  ↓ AIController.chat() 构造 body:
    {
      "message": "...",
      "session_id": "...",
      "stream": true,
      "user_context": {
        "user_id": userId,         ← 这里是真实 login（如 "159****0206"）
        "entity_type": "...",
        "department_id": "...",
        "is_admin": bool
      }
    }
  ↓ POST → ai-service /api/ai/chat/stream
  ↓ ???  ← 这里是本任务要查的位置
  ↓ param_resolver.resolve_user_id() 
    拿到的是 "anonymous" ❌
```

**怀疑点**：ai-service 在 `chat/stream` 入口读取 user_id 时，读的地方和 AIController 发的地方对不上（历史上 CLAUDE.md 写的是 `X-User-ID` header，但 AIController 现在放在 body.user_context 里）。

## Step 1：阅读关键文件，建立现状认知

**只读，不改**：

```
fastapi-service/app/api/chat.py            ← chat/stream 路由
fastapi-service/app/api/                   ← 其他入口（ls 看一下）
fastapi-service/app/services/langgraph_agent.py  ← Agent 主流程
fastapi-service/app/services/param_resolver.py   ← user_id 解析器
fastapi-service/app/services/task_executor.py    ← 工具调用执行
```

**要回答的问题**：
1. `/api/ai/chat/stream` 路由的 pydantic 入参模型是什么？有 `user_context` 字段吗？
2. 请求进来后，`user_id` 从哪里取？
   - 路径 A：从 header `X-User-ID`
   - 路径 B：从 body `user_context.user_id`
   - 路径 C：两者都试
3. `param_resolver.resolve_user_id()` 的 fallback 逻辑是什么？什么情况下返回 `"anonymous"`？
4. `task_executor` 在调用工具前，怎么把 user_id 注入参数？

---

## Step 2：打印排查日志（不修代码）

在不改逻辑的前提下，加一行 debug 日志确认假设：

```python
# 在 chat/stream 路由入口加
logger.info(f"[DEBUG] incoming request body: {request.json()}")  # 或等价写法
logger.info(f"[DEBUG] incoming headers: {dict(request.headers)}")

# 在 param_resolver 解析 user_id 的地方加
logger.info(f"[DEBUG] resolve_user_id input={input_value}, fallback triggered={...}")
```

推到 172 重建容器：
```bash
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"
```

从 116 用隧道再测一次（参考 e2e-test-plan.md Step 2）：
```bash
# 在 116 上
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查我本周工时","session_id":"debug","stream":false}' \
  http://127.0.0.1:9901/api/ai/chat/stream
```

**然后立即看日志**：
```bash
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 100"
```

---

## Step 3：根据日志做判断 + 修复

### 情况 A：body.user_context.user_id 根本没到 ai-service
→ SpringBoot AIController 没正确传值 → 本任务管不了，升级回 SpringBoot 侧排查

### 情况 B：body.user_context.user_id 到了（如 "159****0206"），但 ai-service 没读
→ **这是最可能的情况**。修复方向：

1. 在入口路由里解析 `user_context.user_id` 并注入到后续 Agent 执行上下文
2. 确保 `param_resolver` / `task_executor` 从这个上下文取 user_id，而不是从 header 取

**修改原则**：
- 优先从 `body.user_context.user_id` 读
- 向后兼容保留 header `X-User-ID` 读取（万一别的调用方还在用）
- fallback 到 `"anonymous"` 之前，先打 warning 日志（"user_id fallback to anonymous, request=..."），方便未来诊断

### 情况 C：user_id 正确传入，但工具调用时丢失
→ 是 `task_executor` 或 `param_resolver` 里上下文传递断了
→ 修这个链路，不动入口

---

## Step 4：验证修复

```bash
# 1. 容器重建
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"

# 2. 等 10 秒健康检查
ssh caic@172.19.3.136 "docker ps --filter name=ai-assistant-service --format '{{.Status}}'"

# 3. 隧道直测（stream=false 容易看结果）
# 在 116 上用同样 token：
curl -s --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查我本周工时","session_id":"verify_fix","stream":false}' \
  http://127.0.0.1:9901/api/ai/chat/stream | python -m json.tool
```

**通过标准**：
- `parameters.user_id` 不再是 `"anonymous"`，而是真实登录名（如 `"159****0206"`）
- `record_count` 反映真实工时记录数（>0 如果本周确实填过工时）

---

## Step 5：提交代码 + 记录

```bash
# 提交改动（仅 ai-service 代码，不要误提交其他目录）
git add fastapi-service/app/...
git commit -m "fix: correctly extract user_id from body.user_context instead of fallback to anonymous"
# 不要 push，等规划窗口确认
```

在本文档末尾追加：

```
## 修复记录（{{日期}}）

### 根因
（根据 Step 2 日志写 1-2 句）

## 修复记录（2026-04-22）

### 根因
`chat.py` 的两个接口都从 `header X-User-ID` 读取 `user_id`，没有优先使用 `body.user_context.user_id`。非流式接口更有 `"anonymous"` fallback，会直接覆盖 body 里的正确值。即使流式接口，如果 Spring Boot AIController 把 user_id 放在 body 里但未设置 header，body 值虽保留，但历史代码逻辑给人误导（注释写"从请求头中提取"）。

### 改动文件
- `fastapi-service/app/api/chat.py`：
  - `/chat/stream` 和 `/chat` 两个接口统一修复：优先从 `body.user_context` 读取 user_id/entity_type/department_id/auth_token，header 作为兜底
  - 当 user_id 最终为 None 时，fallback 到 `"anonymous"` 并打 `WARNING` 日志，记录 body.user_id 和 header.X-User-ID 的值，便于未来诊断
- `fastapi-service/app/services/langgraph_agent.py`：
  - `stream_agent_response` 中 user_id fallback 到 anonymous 时也打 `WARNING` 日志，记录 user_context 的 keys

### 验证结果
- 部署到 172 后，直接 curl 到 `localhost:8000/api/ai/chat/stream`：
  - **带 user_context.user_id="159****0206"**：工具参数中 `user_id: "159****0206"` ✓（不再是 anonymous）
  - **不带 user_context**：`WARNING` 日志正确输出：`[DEBUG] user_id fallback to anonymous in /chat/stream, body.user_id=None, header.X-User-ID=None`
- 代码已 push（`b63863f`），172 已 `git pull` 并重建容器

### 部署状态
- ✅ fastapi-service (`chat.py` + `langgraph_agent.py`)：代码已 push，172 容器已重建
- ✅ 公网端到端验证：POST /api/ai/chat 中文消息触发 `query_timesheet`，user_id 为真实用户名

---

## 评审后补修记录（2026-04-23）

| 评审项 | 状态 | 说明 |
|--------|------|------|
| 同步更新 CLAUDE.md | ✅ | "请求处理流程"从 `header 注入` 改为 `body.user_context 优先，header 兜底` |
| WARNING 日志加 session_id | ✅ | `chat.py` 两处 + `langgraph_agent.py` 一处，共三处 |
| 提取 `_resolve_user_identity` 函数 | ✅ | 从两个路由函数中提取，消除重复代码，两个路由复用 |
| user_id 解析单元测试 | ✅ | 新增 `tests/test_chat_user_id_resolution.py`，10 个用例全部通过 |

### 单元测试覆盖

```
test_read_user_id_from_body                          PASSED
test_read_all_fields_from_body                       PASSED
test_fallback_to_header_when_body_missing            PASSED
test_header_fallback_for_all_fields                  PASSED
test_body_wins_over_header                           PASSED
test_fallback_to_anonymous                           PASSED
test_fallback_to_anonymous_logs_warning              PASSED
test_empty_string_body_user_id_treated_as_falsey     PASSED
test_none_body_user_id_treated_as_missing            PASSED
test_partial_body_with_header_complement             PASSED
```

---

## 任务状态：✅ 已完成（2026-04-23）
```

## 不要做的事

- ❌ 不要动 SpringBoot 侧（AIController.java / AIPermissionInterceptor.java）
- ❌ 不要 push 代码，等规划窗口 review
- ❌ 不要修 vLLM 配置（那是 P2 任务）
- ❌ fallback 到 "anonymous" 的逻辑不要完全删除，改为带 warning 日志的兜底
