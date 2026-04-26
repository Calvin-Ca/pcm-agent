# M3 — 加班工时查询 e2e 测试

> **优先级**：P0
> **关联 bug**：B3（`docs/changelog/2026-04-26.md`）— 工具误路由 + SQL 模板缺加班场景
> **预计耗时**：1 小时
> **前置阅读**：[`../e2e-strategy.md`](../e2e-strategy.md)

---

## 1. 业务背景

加班数据存在 `workhour_attendance` 表（不是 `workhour` 表），关键字段：
- `member_id` — 用户
- `work_date` — 日期
- `overtime_hours` — 加班时长（decimal）
- `overtime_type` — 加班类型
- `check_in_time` / `check_out_time` — 打卡

**B3 三处修复**：
1. `compute_statistics` 工具描述追加「加班不适用本工具」
2. `sql_query` 工具描述追加「加班必须用本工具」
3. `sql_generation.yaml` 新增加班查询模板

**期望路由**：`查加班` → `sql_query`（不能进 `compute_statistics`）

---

## 2. 测试用例

### TC-M3-01：本周加班时长查询

**前置数据**：
```sql
SELECT work_date, overtime_hours, overtime_type
FROM workhour_attendance
WHERE member_id = '<test_user_id>'
  AND work_date BETWEEN DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND CURDATE()
  AND overtime_hours > 0;
-- 期望：≥ 1 行
```

**输入**：
```
查看我这周的加班时长
```

**预期 SSE**：
```
event: tool_call    # tool_name=sql_query   ← 必须是 sql_query
event: response     # message="本周共加班 X 小时，明细：周一 2h、周三 1.5h..."
event: done
```

**预期 SQL**：
```sql
SELECT wa.work_date AS 日期, wa.overtime_hours AS 加班时长, wa.overtime_type AS 加班类型
FROM workhour_attendance wa
WHERE wa.member_id='<test_user_id>'
  AND wa.work_date BETWEEN '<本周一>' AND '<今天>'
  AND wa.overtime_hours > 0
ORDER BY wa.work_date LIMIT 100
```

---

### TC-M3-02：本月加班统计

**输入**：
```
统计本月每天的加班时长
```

**预期 SQL**：日期范围 `BETWEEN <月初> AND <今天>`
**预期摘要**：每日明细 + 总计（`SUM(overtime_hours)`）

---

### TC-M3-03：加班次数统计

**输入**：
```
我这个月加了几天班？
```

**预期**：
- 路由仍是 `sql_query`
- SQL 用 `COUNT(*)` 或 `COUNT(DISTINCT work_date)`
- 摘要：「本月共加班 N 天，总计 X 小时」

---

### TC-M3-04：负向验证 — 不能命中 compute_statistics

**输入**：
```
统计本部门本周加班排名
```

**预期**：
- 工具路由必须是 `sql_query`，**不能**是 `compute_statistics`
- 如果命中 `compute_statistics`，说明 B3 第一处修复（工具描述排除声明）失效，需要回滚或重写工具描述

**日志验证**：
```bash
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 100 | grep '执行工具:'"
# 期望：执行工具: sql_query, ...
# 不期望：执行工具: compute_statistics, ...
```

---

### TC-M3-05：无加班场景

**前置**：选一个本周无加班的用户

**输入**：
```
查看我这周的加班时长
```

**预期**：
- `row_count` = 0
- 摘要：「查询完成，未找到符合条件的数据。」（或类似兜底）
- **不**报错、**不**显示原始 JSON

---

## 3. 验收标准

| 层 | 检查项 | 通过条件 |
|----|--------|---------|
| 接口层 | 工具路由 | 5 条用例全部命中 `sql_query`，0 条命中 `compute_statistics` 或 `query_timesheet` |
| 接口层 | SQL 内容 | 来源是 `workhour_attendance` 表，包含 `overtime_hours` 字段 |
| 数据层 | row_count 与字段 | 与§"前置数据"独立查询一致；列名「日期」「加班时长」「加班类型」 |
| 数据层 | overtime_hours 类型 | decimal 字段在 JSON 里转字符串（`_sanitize_row` 处理） |
| 渲染层 | 摘要 | 自然语言列出明细 + 总计；空结果用兜底文案 |
| 渲染层 | 无 JSON | 不出现 `{` `}` 字符 |

---

## 4. 数据准备

```sql
-- 准备 1：确认 workhour_attendance 表结构
DESCRIBE workhour_attendance;
-- 必须包含 member_id, work_date, overtime_hours, overtime_type 字段

-- 准备 2：本周加班数据
SELECT work_date, overtime_hours, overtime_type
FROM workhour_attendance
WHERE member_id = '<test_user_id>'
  AND work_date BETWEEN DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND CURDATE();

-- 如果 cnt = 0，临时插入测试数据
INSERT INTO workhour_attendance (member_id, work_date, overtime_hours, overtime_type, ...)
VALUES ('<test_user_id>', DATE_SUB(CURDATE(), INTERVAL 1 DAY), 2.0, '工作日加班', ...);
-- 测试后清理
DELETE FROM workhour_attendance WHERE ... AND overtime_type = '工作日加班' AND overtime_hours = 2.0;
```

> ⚠️ **谨慎**：插入测试数据前先 SELECT 看清表结构所有字段，避免破坏生产数据。

---

## 5. 执行命令模板

### 116 跳板 curl

```bash
cat <<'SCRIPT' | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/m3-test.sh && chmod +x /tmp/m3-test.sh'"
#!/bin/bash
TOKEN="${TOKEN:?需要 TOKEN}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE="https://gst.thsware.com/api/ai/chat"

run() {
  local msg="$1" sid="$2"
  echo "=== $sid: $msg ==="
  curl -Ns --max-time 90 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $UA" \
    -H "Origin: https://gst.thsware.com" \
    -H "Referer: https://gst.thsware.com/" \
    -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
    "$BASE"
  echo; sleep 3
}

run "查看我这周的加班时长" "m3-tc01"
run "统计本月每天的加班时长" "m3-tc02"
run "我这个月加了几天班？" "m3-tc03"
run "统计本部门本周加班排名" "m3-tc04"
SCRIPT

ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/m3-test.sh'" 2>&1 | tee m3-test-output.log

# 工具路由日志
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 300 | grep -E '执行工具|generated_sql|workhour_attendance'" > m3-server-log.txt
```

---

## 6. 已知风险

| 风险 | 概率 | 应对 |
|------|-----|------|
| 测试用户无加班记录 | 高 | 先选有加班数据的用户，或临时插入数据（§4） |
| LLM 把 TC-04 路由到 `compute_statistics` | 中 | 这是 B3 修复点，看日志确认；如失效需要在工具描述里强化排除规则 |
| `overtime_type` 字段值约定不明 | 中 | 先 SELECT DISTINCT 看实际值，调整摘要预期 |
| TC-04 涉及部门数据，可能触发权限校验 | 低 | 用 deptAdmin 账号跑 |

---

## 7. 失败上报特别检查

- 工具路由是否正确（执行工具日志）？
- SQL 是否查的 `workhour_attendance`（不是 `workhour`）？
- `overtime_hours` 值是 decimal 还是被截断为整数？
- 摘要是否包含具体加班时长（不是「加班 1 次」这种聚合不明）？

---

## 8. 完成标记

## 执行记录

- 执行日期：2026-04-26
- 执行人：Agent C
- 测试通道：172 直连 ai-service（`stream=false`）
- 通过用例：TC-M3-02 ✅（本月加班统计 SQL 正确）、TC-M3-03 ✅（加班次数统计 SQL 正确）、TC-M3-05 ✅（无加班场景兜底文案正确）
- 失败用例：
  - TC-M3-01 ❌：误路由到 `rag_engine`（knowledge_qa），未命中 `sql_query`
  - TC-M3-04 ❌：SQL 中 `member_id` 范围未扩展到部门，仅查当前用户
- 发现新 bug：
  - B8：M3 加班查询误路由到 RAG（见 `docs/changelog/2026-04-26.md §B8`）
  - B11：M3 部门加班排名 SQL 范围错误（见 `docs/changelog/2026-04-26.md §B11`）
