# E2E 全量回归测试 — 2026-04-23

> 目的：验证生产环境（gst.thsware.com）所有 AI 工具公网可用。
> 执行位置：**任意能访问 `https://gst.thsware.com` 的机器**（走完整公网链路）。

---

## 前置准备

### 1. 获取 Token

在浏览器中登录 https://gst.thsware.com，从 DevTools → Network → authenticate → Response 中复制 `id_token`。

```bash
# 或在命令行（替换 <密码>）
TOKEN=$(curl -s -X POST https://gst.thsware.com/api/authenticate \
  -H "Content-Type: application/json" \
  -d '{"username":"159****0206","password":"<密码>","rememberMe":false}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['id_token'])")
echo "Token: ${TOKEN:0:50}..."
```

### 2. 快速连通性检查

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  https://gst.thsware.com/api/ai/health | python -m json.tool
```

**期望**：`{"status": "healthy", ...}`

---

## 测试用例清单

### T1: 工具调用 — 工时查询（query_timesheet）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查我本周工时","session_id":"e2e-t1","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t1.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流中出现 `event: tool_call`，且 `tool_name` 包含 `query_timesheet` 或 `timesheet`
- [ ] `parameters.user_id` 为真实用户名（非 `anonymous`）
- [ ] `result.record_count` >= 0（即使本周没填，也应返回 0 而不是报错）

---

### T2: 工具调用 — 项目查询（query_project）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查一下我参与的项目","session_id":"e2e-t2","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t2.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流中出现工具调用，返回项目列表
- [ ] 项目名称、ID 格式正确

---

### T3: 工具调用 — 统计分析（compute_statistics）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"统计我上周的工时汇总","session_id":"e2e-t3","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t3.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流中出现工具调用，返回统计结果
- [ ] 包含总工时、项目分布等数据

---

### T4: 工具调用 — 周报生成（generate_weekly_report）

```bash
curl -Ns --max-time 90 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我生成本周周报","session_id":"e2e-t4","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t4.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流中出现工具调用，返回周报内容
- [ ] 周报格式包含：本周工作、项目进展、下周计划
- [ ] 内容基于真实工时数据（非空/模板）

> 注：此工具耗时较长，timeout 设为 90 秒。

---

### T5: 工具调用 — 工时填报（save_workhour）⚠️ 写操作

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我填报今天在测试项目工作了2小时","session_id":"e2e-t5","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t5.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流最终返回填报成功确认
- [ ] 或 AI 询问确认（缺少项目名时会引导补充，也视为正常）

> ⚠️ **注意**：这是写操作，测试后可能需要手动删除测试数据。

---

### T6: RAG 问答 — 知识库查询

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"工时填报的截止时间是什么时候","session_id":"e2e-t6","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t6.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流返回知识库内容（如"每月最后一个工作日"等）
- [ ] 内容引用了知识库来源

---

### T7: SQL Agent — 自然语言查询（sql_query）

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"统计所有员工上周的总工时","session_id":"e2e-t7","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t7.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流中出现 SQL 查询执行
- [ ] 返回统计结果（数字或表格）
- [ ] 无 SQL 语法错误提示

---

### T8: 通用对话 — 非工具调用

```bash
curl -Ns --max-time 30 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"你好，请介绍一下你自己","session_id":"e2e-t8","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t8.log
```

**判定**：
- [ ] HTTP 200
- [ ] SSE 流返回自然语言回复
- [ ] 回复包含"工时管理助手"等角色介绍
- [ ] 无 tool_call 事件（纯 LLM 回复）

---

### T9: 中文 POST 稳定性

```bash
curl -Ns --max-time 60 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查我本周工时","session_id":"e2e-t9","stream":true}' \
  https://gst.thsware.com/api/ai/chat | tee /tmp/e2e-t9.log
```

**判定**：
- [ ] HTTP 200（非 401/403/500）
- [ ] 中文消息能正常处理

---

## 批量执行脚本

将以下内容保存为 `e2e-regression.sh`：

```bash
#!/bin/bash
set -e

TOKEN="${TOKEN:?请设置 TOKEN 环境变量}"
BASE="https://gst.thsware.com"

run_test() {
    local name="$1"
    local msg="$2"
    local sid="$3"
    local timeout="${4:-60}"

    echo "=== $name ==="
    curl -Ns --max-time "$timeout" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
        "$BASE/api/ai/chat" | tee "/tmp/e2e-$sid.log" | tail -5
    echo ""
}

run_test "T1-工时查询" "查我本周工时" "t1"
run_test "T2-项目查询" "查一下我参与的项目" "t2"
run_test "T3-统计分析" "统计我上周的工时汇总" "t3"
run_test "T4-周报生成" "帮我生成本周周报" "t4" 90
run_test "T5-工时填报" "帮我填报今天在测试项目工作了2小时" "t5"
run_test "T6-RAG问答" "工时填报的截止时间是什么时候" "t6"
run_test "T7-SQL查询" "统计所有员工上周的总工时" "t7"
run_test "T8-通用对话" "你好，请介绍一下你自己" "t8"
run_test "T9-中文稳定性" "查我本周工时" "t9"

echo "=== 全部测试完成，日志在 /tmp/e2e-*.log ==="
```

执行：
```bash
chmod +x e2e-regression.sh
TOKEN=<your-token> ./e2e-regression.sh
```

---

## 结果记录模板

测试完成后，在下方表格记录结果：

| 用例 | 工具/场景 | HTTP 状态 | SSE 正常 | 数据正确 | 备注 |
|------|-----------|-----------|----------|----------|------|
| T1 | query_timesheet | | | | |
| T2 | query_project | | | | |
| T3 | compute_statistics | | | | |
| T4 | generate_weekly_report | | | | |
| T5 | save_workhour | | | | |
| T6 | RAG 知识库 | | | | |
| T7 | sql_query | | | | |
| T8 | 通用对话 | | | | |
| T9 | 中文稳定性 | | | | |

---

## 故障排查

| 现象 | 排查方向 |
|------|----------|
| 401 | Token 过期，重新获取 |
| 403 | WAF 拦截，检查 nginx SSE 配置（参考 P0 修复记录） |
| 500 | ai-service 内部错误，查看 `docker logs ai-assistant-service --tail 50` |
| SSE 流中断 | nginx `proxy_read_timeout` 是否 >= 300s |
| 中文乱码 | curl 加 `-H "Accept-Charset: utf-8"` |
| user_id = anonymous | P1 修复未部署，检查 chat.py user_context 解析 |

---

## 当前状态

- [ ] 测试执行中
- [ ] 结果已记录
- [ ] 问题已修复（如有）
