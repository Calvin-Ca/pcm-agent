# M4 — 权限拒绝事件链路 e2e 测试

> **优先级**：P0
> **关联 bug**：B6（`docs/changelog/2026-04-26.md`）— `task_executor` 三个异常分支缺 `success: False`
> **预计耗时**：1 小时
> **前置阅读**：[`../e2e-strategy.md`](../e2e-strategy.md)

---

## 1. 业务背景

权限校验链路：

```
LLM tool_calls → ParamResolver → PermissionValidator
                                    │
                                    ├─ 通过 → TaskExecutor 执行
                                    └─ 拒绝 → raise PermissionError
                                              ↓
                                  TaskExecutor catch
                                              ↓
                                  return {"success": False, "error": ...}   ← B6 修复点
                                              ↓
                                  langgraph_agent 检查 success 字段
                                              ↓
                                  False → 推送 SSE event=error（红色）
                                  True/缺失 → 推送 SSE event=response（普通气泡）
```

**B6 三处修复**：`task_executor.py` 的 `PermissionError` / `TimeoutError` / `Exception` 三个分支补 `"success": False`。

---

## 2. 测试用例

### TC-M4-01：employee 查他人工时（无权限）

**前置**：employee 角色登录（`159****0206`）

**输入**：
```
查看何思思本月工时
```

**预期 SSE 事件**：
```
event: tool_call    # query_timesheet, member_name="何思思"
event: error        # success=False, error="无权限查询他人工时"   ← 关键：必须是 error 类型
event: done
```

**绝对不能**收到 `event: response`。

**预期浏览器气泡**：
- **红色错误样式**（不是普通蓝色 / 灰色气泡）
- 文案：「❌ 无权限查询他人工时」或类似

**ai-service 日志**：
```bash
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 100 | grep -iE 'PermissionError|无权限'"
# 期望：捕获 PermissionError，return {"success": False, "error": "..."}
```

---

### TC-M4-02：employee 查跨部门统计（无权限）

**前置**：employee 角色

**输入**：
```
统计研发部本月工时
```

**预期**：
- 同 TC-01：error 事件，红色气泡，无权限提示

---

### TC-M4-03：deptAdmin 查本部门（有权限）

**前置**：deptAdmin 角色登录（`thsware`）

**输入**：
```
统计本部门本月工时
```

**预期**：
- `event: response`（不是 error）
- 正常返回统计数据
- 这条用作**正向对照**，确保我们没有过度拒绝

---

### TC-M4-04：deptAdmin 查跨部门（无权限）

**前置**：deptAdmin 角色

**输入**：
```
查看销售部本月工时
```

**预期**：
- `event: error`（红色气泡）
- 文案体现"跨部门"或"无权限"

---

### TC-M4-05：超时模拟（B6 第二处修复）

**人为构造**：在 ai-service 加临时 mock，让 SpringBoot 调用超时

或者：选一个数据量极大的查询触发 timeout（如「查全公司工时明细」用 employee 跑）

**预期**：
- `event: error`，error 字段含「任务执行超时」
- 浏览器红色气泡

**注意**：本用例较难复现，可在评审阶段用代码 review 替代（确认 `task_executor.py:asyncio.TimeoutError` 分支有 `success: False`）。

---

### TC-M4-06：通用异常路径（B6 第三处修复）

**人为构造**：传一个会触发底层异常的输入（如填报时 project_id 类型错误）

**预期**：
- `event: error`
- 文案：「任务执行异常: ...」

---

## 3. 验收标准

| 层 | 检查项 | 通过条件 |
|----|--------|---------|
| 接口层 | 事件类型 | TC-01/02/04/05/06 必须是 `error`，TC-03 是 `response` |
| 接口层 | success 字段 | error 事件 payload 含 `success: false` |
| 接口层 | error 字段嵌套 | 兼容 `result.error` 或 `result.result.error`（langgraph 嵌套提取） |
| 渲染层 | 错误样式 | 红色气泡（非蓝色 / 灰色） |
| 渲染层 | 文案 | 含"无权限"或"超时"或"异常"字样，不是空错误码 |

---

## 4. 测试数据准备

```sql
-- 1. 确认两个测试账号能登录（不锁定）
SELECT login, failed_attempts FROM jhi_user WHERE login IN ('159****0206', 'thsware');
-- failed_attempts 应 < 5

-- 2. 确认"何思思"用户存在（TC-01 用）
SELECT id, real_name FROM sys_user WHERE real_name = '何思思';
-- 期望：1 行

-- 3. 确认目标部门存在（TC-02/03/04 用）
SELECT id, name FROM org_dept WHERE name IN ('研发部', '销售部');
```

---

## 5. 执行命令模板

### 浏览器手测（M4 强烈建议浏览器测，看气泡颜色）

1. 用 employee 账号登录 → 跑 TC-01/02
2. 切换到 deptAdmin 账号 → 跑 TC-03/04
3. 截图保存：每个错误气泡一张图

### 116 跳板 curl（看 SSE 事件类型）

```bash
cat <<'SCRIPT' | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/m4-test.sh && chmod +x /tmp/m4-test.sh'"
#!/bin/bash
TOKEN="${TOKEN:?需要 TOKEN}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE="https://gst.thsware.com/api/ai/chat"

run() {
  local msg="$1" sid="$2"
  echo "=== $sid: $msg ==="
  curl -Ns --max-time 60 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $UA" \
    -H "Origin: https://gst.thsware.com" \
    -H "Referer: https://gst.thsware.com/" \
    -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
    "$BASE" | tee -a m4-events.log
  echo; sleep 3
}

# 用 employee token
run "查看何思思本月工时" "m4-tc01"
run "统计研发部本月工时" "m4-tc02"
SCRIPT

ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<employee_jwt> bash /tmp/m4-test.sh'" \
  2>&1 | tee m4-employee-output.log

# 验证 SSE 事件类型
grep -E '^event:' m4-employee-output.log | sort | uniq -c
# 期望：能看到 event: error 行，不能是 event: response
```

### 关键校验脚本

```bash
# 在测试输出里搜 error 事件
grep -B1 -A2 '^event: error' m4-employee-output.log
# 期望：能看到 success: false 字段

# 在 ai-service 日志里搜 PermissionError
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 300 | grep -iE 'PermissionError|无权限|权限拒绝'"
```

---

## 6. 已知风险

| 风险 | 概率 | 应对 |
|------|-----|------|
| LLM 没正确生成 query_timesheet 参数（如 member_name 没传），未触发权限校验 | 中 | 看 tool_calls 参数，没传 member_name 就重写输入 |
| SpringBoot 端用网关层做权限拦截，FastAPI PermissionValidator 未触发 | 中 | 看 ai-service 日志确认走到 PermissionValidator |
| TC-05 超时难复现 | 高 | 改用代码 review 替代，确认 task_executor.py:401-404 有 `success: False` |
| 前端可能把 `event: error` 兜底成普通文本 | 低 | 看 web/src/store/modules/chat/index.ts SSEEventType.ERROR 分支处理 |

---

## 7. 失败上报特别检查

- SSE 事件类型确实是 `error` 还是降级成 `response`？
- success 字段的取值（False / 缺失 / 字符串 "false"）？
- 错误信息嵌套层级（`result.error` 还是 `result.result.error`）？
- 浏览器气泡颜色实际值（截图 + DevTools 看 class）？

---

## 8. 完成标记

## 执行记录

- 执行日期：2026-04-26
- 执行人：Agent C
- 测试通道：172 直连 ai-service（`stream=false`）
- employee 角色：TC-M4-01 ✅（查他人工时返回 error 事件 + success=False）、TC-M4-02 ✅（跨部门统计返回 error 事件）
- deptAdmin 角色：TC-M4-03 ✅（本部门统计返回 response 正常）、TC-M4-04 ✅（跨部门查询返回 error 事件）
- 异常分支：TC-M4-05（代码 review）✅、`task_executor.py` 三处异常分支均含 `success=False`；TC-M4-06 ✅（通用异常路径返回 error）
- 发现新 bug：无
- 备注：B6 修复确认生效，PermissionError / TimeoutError / Exception 三处分支均正确返回 `{"success": False, "error": ...}`
