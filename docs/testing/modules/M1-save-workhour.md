# M1 — 工时填报全链路 e2e 测试

> **优先级**：P0
> **关联 bug**：B1（`docs/changelog/2026-04-26.md`）— `workhourDate` 格式 + `description` 字段名
> **预计耗时**：1 小时
> **前置阅读**：[`../e2e-strategy.md`](../e2e-strategy.md) §2 测试前置检查

---

## 1. 业务背景

`save_workhour` 工具调用 SpringBoot `POST /api/workhour` 写入 `workhour` 表。链路涉及四个集成点：

```
浏览器输入「帮我填...」
  → vLLM tool_calls = save_workhour(project_name="预管理系统", duration=7.5, ...)
  → param_resolver: project_name → project_id（查 SpringBoot /api/project）
  → save_workhour: 构造 DTO payload，POST /api/workhour
  → SpringBoot WorkhourDTO 反序列化 → entity → MySQL workhour 表
```

**B1 修复点**：
- `workhourDate` 必须是 ISO Instant `2026-04-24T00:00:00.000Z`（不是 `2026-04-24`）
- 字段名是 `description` 不是 `workContent`（DTO 字段名 ≠ entity 字段名）
- HTTPStatusError 必须提取 response body，否则用户看不到具体错误

---

## 2. 测试用例

### TC-M1-01：基础填报（描述含中文）

**输入**：
```
帮我填今天的工时，项目预管理系统，3 小时，写了单元测试
```

**预期 SSE 事件序列**：
```
event: thinking      # LLM 思考
event: tool_call     # tool_name=save_workhour, arguments={project_name="预管理系统", duration=3, description="写了单元测试"}
event: response      # message="✅ 工时填报成功：2026-04-XX 3h"
event: done
```

**预期数据库变更**：
```sql
-- 测试前
SELECT COUNT(*) FROM workhour WHERE member_id = '<test_user_id>' AND workhour_date = CURDATE();
-- 假设 baseline = N

-- 测试后
SELECT COUNT(*) FROM workhour WHERE member_id = '<test_user_id>' AND workhour_date = CURDATE();
-- 期望 = N+1

-- 字段验证
SELECT project_id, workhour_date, workhour, description
FROM workhour
WHERE member_id = '<test_user_id>'
  AND workhour_date = CURDATE()
ORDER BY id DESC LIMIT 1;
-- project_id：非空，对应"预管理系统"项目 ID
-- workhour_date：当天日期
-- workhour：3.0
-- description："[E2E TEST] 写了单元测试"   ← 注意 description 字段必须有内容
```

**预期浏览器气泡**：
```
✅ 工时填报成功：2026-04-XX 3h
```
（自然语言，无 JSON 字符）

---

### TC-M1-02：填报描述含特殊字符

**输入**：
```
今天 8h，预管理系统，完成回弹检测的后端开发（含单元测试&集成测试）
```

**预期**：同 TC-M1-01，`description` 字段保留括号、`&` 等特殊字符。

**重点验证**：SQL 注入防御、特殊字符不被截断。

---

### TC-M1-03：填报指定日期（昨天）

**输入**：
```
帮我补一条昨天的工时，预管理系统，4h，需求评审
```

**预期**：
- `workhour_date` = 昨天日期
- payload 里 `workhourDate` 必须是 `<昨天>T00:00:00.000Z`（不是 `<昨天>`，否则触发 B1）

**SSE 字段验证**（172 直连 ai-service 看日志）：
```bash
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 50 | grep workhourDate"
# 期望日志输出：payload: {... 'workhourDate': '2026-04-25T00:00:00.000Z' ...}
```

---

### TC-M1-04：填报错误项目名（验证 param_resolver）

**输入**：
```
今天 5h，不存在的项目xyz，做了点东西
```

**预期**：
- param_resolver 找不到项目 → 返回错误
- 浏览器气泡：「❌ 未找到项目：不存在的项目xyz」（红色错误）
- 数据库无新增记录

---

### TC-M1-05：HTTPError body 透出（B1 第三处修复）

**人为构造**：让 SpringBoot 返回 400（如 workhour 字段超过最大值 24）

**输入**：
```
今天填 30 小时，预管理系统，加班
```

**预期**：
- 浏览器气泡：「❌ 服务调用失败: HTTP 400 — \<具体错误信息\>」
- **关键**：错误信息里**有 SpringBoot 返回的具体原因**（如「单日工时不得超过 24 小时」），不是空的 `HTTP 400`
- ai-service 日志：`logger.error` 输出完整 payload + body

---

## 3. 验收标准（三层）

| 层 | 检查项 | 通过条件 |
|----|--------|---------|
| 接口层 | SSE 事件类型 | TC-01~03 看到 `tool_call` + `response`；TC-04~05 看到 `error`（success=false） |
| 接口层 | tool_calls 参数 | `project_name` / `duration` / `description` 字段名正确，无 `workContent` 别名 |
| 数据层 | DB SELECT 比对 | TC-01~03 工时记录数 +1，字段值与输入一致；TC-04~05 无新增 |
| 数据层 | description 持久化 | `[E2E TEST]` 前缀完整保留 |
| 渲染层 | 气泡内容 | 成功用 ✅ + 自然语言；失败用 ❌ + 红色 |
| 渲染层 | 无原始 JSON | 不出现 `{` `}` 字符 |

---

## 4. 测试数据准备 / 清理

### 准备

```sql
-- 1. 确认测试用户存在且未锁定
SELECT login, failed_attempts, locked_date FROM jhi_user WHERE login = '159****0206';

-- 2. 确认"预管理系统"项目存在
SELECT id, name FROM project WHERE name = '预管理系统';

-- 3. 记录当天 baseline
SELECT COUNT(*) AS baseline FROM workhour
WHERE member_id = '<test_user_id>' AND workhour_date = CURDATE();
```

### 清理

```sql
-- 测试后清理所有 [E2E TEST] 标记的记录
DELETE FROM workhour
WHERE member_id = '<test_user_id>'
  AND description LIKE '[E2E TEST]%';

-- 验证清理
SELECT COUNT(*) FROM workhour
WHERE member_id = '<test_user_id>'
  AND description LIKE '[E2E TEST]%';
-- 期望 = 0
```

---

## 5. 执行命令模板

### 浏览器手测

1. 打开 `https://gst.thsware.com/`，用 `159****0206` 登录
2. 打开 AI 助手对话框
3. 逐条输入 TC-M1-01~05 的输入，截图保存
4. 每次填报后切到工时列表页，确认记录出现 / 不出现

### 116 跳板 curl 测试（绕 WAF）

```bash
# 把脚本推到 116
cat <<'SCRIPT' | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/m1-test.sh && chmod +x /tmp/m1-test.sh'"
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
    "$BASE"
  echo
  sleep 3
}

run "帮我填今天的工时，项目预管理系统，3 小时，[E2E TEST] 写了单元测试" "m1-tc01"
run "今天 8h，预管理系统，[E2E TEST] 完成回弹检测的后端开发（含单元测试&集成测试）" "m1-tc02"
run "帮我补一条昨天的工时，预管理系统，4h，[E2E TEST] 需求评审" "m1-tc03"
run "今天 5h，不存在的项目xyz，[E2E TEST] 做了点东西" "m1-tc04"
run "今天填 30 小时，预管理系统，[E2E TEST] 加班" "m1-tc05"
SCRIPT

# 执行
ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/m1-test.sh'" 2>&1 | tee m1-test-output.log

# 取 ai-service 日志
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 200 | grep -E 'workhourDate|save_workhour|HTTP'" > m1-server-log.txt
```

### 数据库比对

```bash
# 在 192.168.0.94 数据库（或通过 SpringBoot 跳转）执行清理脚本前后的 COUNT
# （需要 DB 凭证，按团队约定方式连接）
```

---

## 6. 已知风险

| 风险 | 概率 | 应对 |
|------|-----|------|
| TC-04 项目名解析依赖 SpringBoot /api/project，接口慢可能超时 | 低 | 看日志确认 param_resolver 调用耗时 |
| TC-05 SpringBoot 单日工时限制可能不在 24h，需先确认业务规则 | 中 | 测试前查一下业务校验，调整测试值 |
| 多个 TC 连跑，账号被锁 | 低 | 见 §4 清理 |

---

## 7. 失败记录模板

如发现新 bug，按 [`../e2e-strategy.md`](../e2e-strategy.md) §5 模板写到 `docs/changelog/2026-04-XX.md`。

特别检查：
- payload 里 `workhourDate` 是否带时间后缀
- payload 字段名是 `description` 不是 `workContent`
- HTTP 4xx 时是否带 body

---

## 8. 完成标记

测试通过后，在本文档底部追加：

```markdown
## 执行记录

- 执行日期：YYYY-MM-DD
- 执行人：<name>
- 通过用例：TC-M1-01 ✅ TC-M1-02 ✅ TC-M1-03 ✅ TC-M1-04 ✅ TC-M1-05 ✅
- 发现新 bug：<列表 / 无>
- 截图与日志：<路径或链接>
```
