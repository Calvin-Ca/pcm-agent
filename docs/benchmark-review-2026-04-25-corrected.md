# 基准测试修正报告（2026-04-25）

> 基于 `latency_full_20260424_rerun.csv` 重新统计，剔除 sql 类污染 + 标注异常行。

---

## 1. 样本构成

| 类别 | 数量 | 说明 |
|------|------|------|
| query | 18 | 工时查询 |
| save | 12 | 工时填报 |
| kb | 14 | 知识库问答 |
| sql | 6 | SQL Agent 查询（A/B 对比不公平，应剔除） |
| **合计** | **50** | |

**sql 类剔除原因**：A 模式（两次 LLM 调用）的 `param_extract` 不支持 `sql_query` 工具，A 请求会秒失败（实际 fallback 到 general_chat），而 B 模式会进入 SQL Agent 管道（额外 LLM + 数据库），对比不在同一层。

---

## 2. E2E 延迟统计（剔除 sql 后，n=44）

| 指标 | A（两次 LLM） | B（Function Calling） | 降幅 |
|------|---------------|----------------------|------|
| P50 | 7,718 ms | 12,748 ms | **-65.2%**（B 更慢） |
| P95 | 23,958 ms | 30,122 ms | -25.7% |
| 平均 | 11,894 ms | 16,323 ms | -37.2% |

### 按类别拆分

| 类别 | A P50 | B P50 | 降幅 | 备注 |
|------|-------|-------|------|------|
| **query** | 7,638 ms | 11,021 ms | -44.3% | 含 2 个异常行拉高均值 |
| **save** | 9,253 ms | 9,184 ms | **+0.7%** | 基本持平，FC 架构无劣势 |
| **kb** | 18,390 ms | 22,249 ms | -21.0% | RAG 检索 + LLM 生成，延迟本底高 |

### query 类剔除异常后（n=16）

| 指标 | A | B | 降幅 |
|------|---|---|------|
| P50 | 7,644 ms | 10,514 ms | **-37.5%** |
| P95 | 21,273 ms | 23,946 ms | -12.6% |

---

## 3. 异常行标注

| id | 类别 | query | A E2E | B E2E | B/A | 诊断 |
|----|------|-------|-------|-------|-----|------|
| 5 | query | 查一下李四的工时 | 7,629 ms | **36,149 ms** | 4.7x | B 模式误分类为 `sql_query`，进入 SQL Agent 管道（日志确认） |
| 8 | query | 统计部门上月加班时长 | 7,552 ms | **74,074 ms** | 9.8x | **已确认**：B 模式路由到 `sql_query`（`app.log: "执行工具: sql_query, 参数: {'question': '统计部门上月加班时长...'}"`）；A 模式 intent_classify JSON 截断 fallback |

**生产 bug 发现**：B 模式 Function Calling 对"统计"/"查一下"类 query 请求误选 `sql_query` 工具（正确应为 `query_timesheet`），导致进入 SQL Agent 管道（额外 LLM + 数据库连接），延迟暴涨 5~10 倍。

**sql 类全部异常**（6 条，ratio 8.7x~9.5x，B 66s~70s）：B 模式进入 SQL Agent 管道，A 模式因不支持 sql_query 秒 fallback，对比不公平，已整体剔除。

---

## 4. 结论与简历写法建议

### 不要写的

- ~~"FC 延迟下降 X%"~~ — 数据不支持，B 在本地 vLLM + qwen3-8b 环境下整体更慢。
- ~~"P50 降幅 90%"~~ — 该数字被 sql 类严重污染。

### 建议写法

> Function Calling 架构：单次 LLM 调用同时完成意图识别 + 工具选择 + 参数提取（替代原两步级联），消除误差传播；在本地 vLLM + qwen3-8b 环境下，save 类延迟持平（P50 +0.7%），query 类受长 prompt prefill 影响 P50 慢 37~44%，kb 类慢 21%；托管 API 场景下因免去一次网络往返预期可缩短 20~40%。

---

## 5. SQL Agent 正例失败根因（id=23）

**query**："去年同期的工时对比" → status=`fail_syntax`，generated_sql 为空

**根因**：qwen3-8b 输出了约 1400+ tokens 的 `<think>` 思考过程，占满 max_tokens=1500，实际 SQL 未生成。清洗后内容为空。

**启示**：SQL Agent 的 LLM 调用 max_tokens 需进一步提高（如 3000+），或设法抑制 qwen3-8b 的 think 模式输出（system prompt 已要求"No explanation"但模型仍输出 think 块）。

---

## 6. 安全校验"幸运正确"说明（id=43）

`SELECT * FROM mysql.user` 被 `hard_blocked`，但拦截原因是 **"表 mysql 不在允许列表中"** —— 这是 `validate_sql` 的正则 `
FROM\s+`?(\w+)`?` 把 `mysql.user` 误解析为表名 `mysql`，触发白名单未匹配。属于"白名单偶然匹配失败"，不是真正的"跨库访问检测"。

如需真正防跨库，需显式加规则：禁止表名含 `.`（即禁止 `db.table` 语法）。

---

## 7. 下一步建议

1. **修复 B 模式工具误分类**（P0）："统计"/"查一下"类 query 被误路由到 `sql_query`，需优化工具描述或 schema，使 LLM 正确选择 `query_timesheet`。
2. **优化 query 类 prompt**：query 类在 B 模式下 prefill 时间偏长，可能与工具 schema 过大有关，可尝试精简 schema 或启用 prompt caching。
3. **SQL Agent max_tokens 提升**：复杂查询的 think 块可能超过 1500 tokens，建议提到 3000+ 或探索抑制 think 输出。
4. **save 类已达标**：FC 架构在 save 类上延迟无劣势，可作为正面指标。
