# M2 — 漏填工时查询 e2e 测试

> **优先级**：P0
> **关联 bug**：B2（`docs/changelog/2026-04-26.md`）— `is_work_day='1'` vs `'Y'` + 缺 `user_id`/`today` 变量 + `datetime.date` 序列化
> **预计耗时**：1 小时
> **前置阅读**：[`../e2e-strategy.md`](../e2e-strategy.md)

---

## 1. 业务背景

「漏填工时查询」走 `sql_query` 工具（不是 `query_timesheet`）。链路：

```
浏览器输入「我这月有没有漏填」
  → vLLM tool_calls = sql_query(question="...")
  → SQL Agent 加载 sql_generation.yaml 模板
  → 模板用 user_id + today + start/end 变量生成 SQL
  → SQL 执行 → JSON 序列化（date 字段需 strftime）
  → SQLAgentLLMClient 生成自然语言摘要
  → SSE response 事件 message=摘要
```

**B2 三处修复**：
1. SQL 模板里 `wc.is_work_day='1'`（VARCHAR），不是 `='Y'`
2. `sql_generation.yaml` 必须接收 `user_id` 和 `today` 变量
3. `_sanitize_row()` 必须处理 `datetime.date` 类型

---

## 2. 测试用例

### TC-M2-01：本月漏填查询

**前置数据**：
```sql
-- 必须确认本月有工作日且测试用户漏填
SELECT DATE(wc.date_value) AS d, wc.work_hour AS expect
FROM work_calendar wc
LEFT JOIN workhour wh
  ON DATE(wc.date_value) = DATE(wh.workhour_date)
 AND wh.member_id = '<test_user_id>'
WHERE wc.is_work_day = '1'
  AND DATE(wc.date_value) BETWEEN DATE_FORMAT(CURDATE(), '%Y-%m-01') AND CURDATE()
  AND wh.id IS NULL
ORDER BY wc.date_value;
-- 期望：≥ 1 行（如果没有，需要先制造漏填场景）
```

**输入**：
```
我这个月有没有漏填工时？
```

**预期 SSE 事件**：
```
event: tool_call    # tool_name=sql_query, arguments={question="..."}
event: response     # message="本月共有 X 天工作日漏填工时，分别是 2026-04-08（应填 8h）、2026-04-15（应填 8h）..."
event: done
```

**预期 SQL（看 ai-service 日志）**：
```sql
-- 必须包含 wc.is_work_day='1' （不是 ='Y'）
-- 必须包含 wh.member_id='<具体 user_id>'（不是 'unknown'）
-- 必须包含 BETWEEN '<本月初>' AND '<today>'
SELECT DATE(wc.date_value) AS 日期, wc.work_hour AS 应填工时
FROM work_calendar wc
LEFT JOIN workhour wh ON DATE(wc.date_value)=DATE(wh.workhour_date)
  AND wh.member_id='<test_user_id>'
WHERE wc.is_work_day='1'
  AND DATE(wc.date_value) BETWEEN '2026-04-01' AND '2026-04-26'
  AND wh.id IS NULL
ORDER BY wc.date_value LIMIT 100
```

**预期返回结果**：
- `row_count` > 0（与 §"前置数据"查询结果行数一致）
- `rows` 里每行有 `日期`（`YYYY-MM-DD` 字符串，不是 ISO 时间）和 `应填工时`（数字）

---

### TC-M2-02：本周漏填查询

**输入**：
```
我这周哪天没填工时？
```

**预期 SQL**：日期范围是本周一到今天（不是本月）。
**预期摘要**：「本周共有 N 天工作日漏填工时，分别是...」

**LLM 路由判定**：必须命中 `sql_query`，不能误路由到 `query_timesheet`。

---

### TC-M2-03：上周漏填查询

**输入**：
```
上周我有几天漏填？
```

**预期 SQL**：日期范围是上周一到上周日。
**预期摘要**：「上周共有 N 天工作日漏填...」

---

### TC-M2-04：无漏填场景

**前置**：选一个**填齐了所有工作日工时**的用户（或临时把 baseline 填满）

**输入**：
```
我这个月有没有漏填工时？
```

**预期**：
- `row_count` = 0
- 摘要：「查询完成，未找到符合条件的数据。」（前端 `formatToolResult` 兜底）
- **不能**显示「row_count: 0」原始 JSON

---

### TC-M2-05：date 字段序列化（B2 第三处修复回归）

**输入**：（同 TC-M2-01）

**关键检查**：响应**不报** `Object of type date is not JSON serializable`，且日期字段是 `2026-04-08` 形式字符串。

**日志验证**：
```bash
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 200 | grep -E 'sanitize|JSON serializable'"
# 期望：无 'JSON serializable' 报错
```

---

## 3. 验收标准

| 层 | 检查项 | 通过条件 |
|----|--------|---------|
| 接口层 | 工具路由 | 5 条用例全部命中 `sql_query`，0 条命中 `query_timesheet` |
| 接口层 | SQL 内容 | 含 `is_work_day='1'`、具体 user_id、正确日期范围 |
| 接口层 | 序列化 | rows 里 `日期` 字段是字符串，无 datetime 报错 |
| 数据层 | row_count | 与§"前置数据"独立查询结果行数一致（误差 ≤ 0） |
| 数据层 | 字段名 | 列名是中文「日期」「应填工时」，与 prompt 模板一致 |
| 渲染层 | 摘要 | 自然语言，列出具体日期；空结果用兜底文案 |
| 渲染层 | 无 JSON | 不出现 `{"row_count":...}` 原始字符 |

---

## 4. 测试数据准备

```sql
-- 准备 1：选定测试用户
SET @uid = '<test_user_id>';
SET @today = CURDATE();
SET @month_start = DATE_FORMAT(@today, '%Y-%m-01');

-- 准备 2：确认本月至少有 1 天工作日漏填
SELECT '本月漏填天数' AS metric, COUNT(*) AS cnt FROM work_calendar wc
LEFT JOIN workhour wh ON DATE(wc.date_value)=DATE(wh.workhour_date) AND wh.member_id=@uid
WHERE wc.is_work_day='1' AND DATE(wc.date_value) BETWEEN @month_start AND @today AND wh.id IS NULL;
-- cnt 应 ≥ 1，如果 = 0 则人为删除一天的填报记录（仅测试用，记得测试后恢复）

-- 准备 3：work_calendar 数据完整性（必须存在本月工作日数据）
SELECT COUNT(*) FROM work_calendar
WHERE is_work_day='1' AND DATE(date_value) BETWEEN @month_start AND @today;
-- cnt 应 ≥ 当月工作日数（约 18-22）
```

如果 `work_calendar` 缺数据，**先排查日历表是否被维护**，再做测试。

---

## 5. 执行命令模板

### 116 跳板 curl

```bash
cat <<'SCRIPT' | ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/m2-test.sh && chmod +x /tmp/m2-test.sh'"
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

run "我这个月有没有漏填工时？" "m2-tc01"
run "我这周哪天没填工时？" "m2-tc02"
run "上周我有几天漏填？" "m2-tc03"
SCRIPT

ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/m2-test.sh'" 2>&1 | tee m2-test-output.log

# 关键日志（看实际生成的 SQL）
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 300 | grep -E 'generated_sql|is_work_day|sql_query'" > m2-server-log.txt
```

### 数据层比对

```bash
# 用 m2-test-output.log 里 SSE response 事件的 row_count
# 对比 §"前置数据" 独立 SQL 查询结果行数
# 必须一致，否则模板有问题
```

---

## 6. 已知风险

| 风险 | 概率 | 应对 |
|------|-----|------|
| 测试用户本月没漏填 → TC-01 row_count=0，无法验证主路径 | 中 | 用 §4 准备步骤人为制造漏填，测完恢复 |
| `work_calendar` 表本月数据未维护 | 中 | 先 SELECT 确认，缺数据找 DBA 补 |
| LLM 路由可能把 TC-02 误判到 `query_timesheet` | 低 | 看日志确认 `tool_name`，错了在工具描述里加排除规则 |
| date 字段在某些客户端展示成 ISO 格式 | 低 | 看摘要文本是 `2026-04-08` 不是 `2026-04-08T00:00:00` |

---

## 7. 失败上报特别检查

发现 bug 时，特别检查这几点：

- 日志里实际生成的 SQL 是 `='1'` 还是 `='Y'`？
- payload 里 `user_id` 是具体 ID 还是 `unknown`？
- response 是否报 `JSON serializable` 错误？
- 摘要是否包含 `<think>` 字符（如有，串到 M5 vLLM 稳定性）？

---

## 8. 完成标记

## 执行记录

- 执行日期：2026-04-26
- 执行人：Agent C
- 测试通道：172 直连 ai-service（`stream=false`）
- 通过用例：TC-M2-04 ✅（空结果兜底文案正确）、TC-M2-05 ✅（无 JSON 序列化错误）
- 失败用例：
  - TC-M2-01 ❌：SQL 生成错误，查了 `workhour` 表而非 `work_calendar` 表
  - TC-M2-02 ❌：summary 与 row_count 矛盾（row_count=5，summary 却说「没有漏填」）
  - TC-M2-03 ❌：日期范围错误（用了本周 4/19-4/26，而非上周 4/13-4/19）
- 发现新 bug：
  - B9：M2 漏填查询 SQL 生成不一致 + summary 矛盾 + 日期范围错误（见 `docs/changelog/2026-04-26.md §B9`）
- row_count 比对：
  - TC-01：app=0（错误 SQL），db=5（正确值），不一致
  - TC-02：app=5，db=5，一致（但 summary 错误）
  - TC-03：app=5（错误范围），db=0（正确值），不一致

### 第二轮修复验证（2026-04-26，Agent C Round 3）

- B9 修复后重新验证：
  - TC-M2-01（本月漏填）：✅ SQL 正确使用漏填模板，`BETWEEN '2026-04-01' AND '2026-04-26'`，`is_work_day='1'`，`wh.id IS NULL`，row_count=2
  - TC-M2-03（上周漏填）：⚠️ 日期范围正确（`2026-04-13` 至 `2026-04-19`），但 LLM 自行加了 `GROUP BY` + `COUNT` 导致语法错误（乱码别名），未严格遵循模板约束
- 日志关键摘录：`workType (user×project) 众数: '研发工作'`（save_workhour 已接入 resolver）
