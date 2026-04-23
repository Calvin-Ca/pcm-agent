# E2E 测试计划（2026-04-22）

> 执行前提：gongshi-ht.war 已重新打包部署（去掉了 @PreAuthorize 注解），ai-service 运行在 172，隧道已通。

## 前置：获取 JWT Token

```bash
# 用户账号（159****0206）密码已加密，直接用 encrypt 接口或前端拿
# 加密后的密码(LgnKid+U/IadlXsIJyUNRr5HrttYeyM3SfoUmiNzyUmvwIGBDIYunt+mECJseWO2ElFA79/yQLn1kYcDPZHx0Xf+9b02M9jEg5KnPTAK0RK3wKIWcWsTF2SXfxbRsGmZEXbMXC6Lvdr6ur4uHvLTqy6NqzfESwPF1jwCYd8AcPhwRBUSls3w1Dn1aQArVaJpqv9xP9ghSMqN8xiub2iDjAkdEsMSqhJKPRgetDsUv1zl4uo0tz0zBgyQN//0UwQZLyd5bSQ1X1GHhQNnCyHwEXQr1V8p4v+gfUCl8wW0OzEneehy8GsTPwKvJSXbWZCWjxO1AKZZCwU7CbY7bEIBIQ==)
# 接口结构：data.token（不是 id_token）
RESP=$(curl -s -X POST https://gst.thsware.com/api/authenticate \
  -H "Content-Type: application/json" \
  -d '{"username":"159****0206","password":"<加密后的密码>","rememberMe":false}')
TOKEN=$(echo $RESP | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
echo "TOKEN: ${TOKEN:0:50}..."
```

如果无法获取，从浏览器 DevTools → Network → authenticate 请求 → Response 里拿 `data.token`。

---

## Step 1：健康检查

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://gst.thsware.com/api/ai/health
```

**期望**：`{"status":"UP",...,"components":{"status":"healthy",...}}`

**失败处理**：
- 401 → token 过期，重新获取
- DOWN → 检查 172 ai-service：`docker logs ai-assistant-service --tail 20`
- 502 → SpringBoot 未启动或 AI_SERVICE_URL 未生效，检查 116

---

## Step 2：工具调用（查本周工时）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查我本周工时","session_id":"smoke_tool","stream":true}' \
  https://gst.thsware.com/api/ai/chat
```

**期望**：SSE 流，能看到：
1. `event: thinking` 或 `event: tool_call` — LLM 调用工具
2. 工时数据（真实数字，来自 SpringBoot API）
3. `event: done` 结束

**失败处理**：
- 无输出 → 检查 SpringBoot 日志：`ssh useryzk@116... 'journalctl -u gongshi-ht --tail 30'`（或 nohup 日志）
- `event: error` → 看 172 ai-service 日志：`docker logs ai-assistant-service --tail 30`
- 工具调用返回 401 → AIController 透传 Authorization 是否有效（P2-1 检查）

---

## Step 3：RAG 问答（规则查询）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"工时填报的截止时间是什么时候","session_id":"smoke_rag","stream":true}' \
  https://gst.thsware.com/api/ai/chat
```

**期望**：
1. LLM 回答来自知识库（不是工具调用）
2. 内容包含截止时间相关信息（knowledge-base/*.md 里有的）
3. 不出现"我不知道"类答复

**失败处理**：
- 回答内容空洞 → RAG 索引可能是 FAISS 降级模式（Milvus 有 nodeID 不匹配 bug），这是已知问题，FAISS 降级后知识库仍可用

---

## Step 4：工具列表

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://gst.thsware.com/api/ai/tools
```

**期望**：JSON，包含工具列表（query_timesheet、save_workhour 等）

---

## 结果记录（2026-04-22 复测）

| Step | 状态 | 备注 |
|------|------|------|
| Step 1 health | ✅ | GET /api/ai/health 正常，ai-service 各组件 UP |
| Step 2 工具调用 | ❌（CDN）/✅（隧道） | CDN 阻断所有 POST /chat 返回 401；隧道直测非流式返回 200，query_timesheet 被调用 |
| Step 3 RAG | ❌（CDN）/✅（预期） | 同 Step 2 CDN 层阻断；stream 参数被忽略（实际返回 JSON 非 SSE） |
| Step 4 工具列表 | ❌（CDN）/❌（服务） | CDN GET /tools 返回 500（ai-service 内部错误，vLLM 配置问题） |

### 2026-04-22 测试细节

**CDN 层（gst.thsware.com）**：
- GET /health → 200 ✅
- POST /chat stream=true → 401 ❌（CDN/WAF 拦截，无响应体）
- POST /chat stream=false → 401 ❌（CDN/WAF 拦截）
- GET /api/ai/tools → 500 ❌（CDN 放行，ai-service 内部错误）

**隧道直接（172↔116，绕过 CDN）**：
- POST /chat stream=false → 200 ✅（工具 query_timesheet 被调用，返回 0 条记录）
- POST /chat stream=true → 200 ✅（返回 JSON 而非 SSE，stream 参数被忽略）

**Step 2 隧道响应片段**：
```json
{"success":true,"message":"请求处理完成","session_id":"smoke_tool_s",
 "result":{"route_info":{"target":"tool_executor","intent_type":"tool_execution","confidence":0.9},
           "tool_name":"query_timesheet",
           "result":{"tool_name":"query_timesheet","parameters":{
             "start_date":"2026-04-20","end_date":"2026-04-22",
             "user_id":"anonymous"  // ⚠️ param_resolver 未正确解析 user_id
           },"result":{"success":true,"total_hours":0.0,"record_count":0}}
}}
```

---

## 已知遗留问题

| # | 问题 | 影响 | 状态 |
|---|------|------|------|
| CDN 阻断所有 POST /api/ai/chat | WAF 对 POST 请求体特殊处理，返回 401 | **P0 - 阻断生产 E2E** | 待处理 |
| vLLM Function Calling 配置缺失 | `enable-auto-tool-choice` 和 `tool-call-parser` 未设置，降级规则路由 | 中（功能仍可用） | 待处理 |
| user_id 为 anonymous | param_resolver 未正确解析 user_id，工具拿到的是 anonymous | 中（数据查询不正确） | 待处理 |
| Milvus nodeID 不匹配 | RAG 自动降级 FAISS，知识库正常使用 | 低 | 已知 |
| nginx `/api/ai/` 无专用 SSE location | 走通用 `/api/` location，proxy_buffering 未知 | 低 | 已知 |
| autossh 无持久化 | 服务器重启后隧道断，需手动重启 autossh | 低 | 已知 |
