# 派单方案 ① ECharts 可视化

> 创建：2026-04-28
> 预估：1 天（后端 0.5 天 + 前端 0.5 天）
> 简历价值：AI 结构化输出（强类型 JSON Schema）+ 全栈联动 + SQL Agent 查询闭环

---

## 1. 目标

让用户问 "本月各部门工时占比"、"近 7 天工时趋势" 这类查询时，AI 不再只返回文字 + 表格，而是：

1. LLM 生成符合 **ECharts option Schema** 的结构化 JSON
2. 后端通过 SSE 推一个新事件类型 `event: chart`
3. 前端拿到 chart 事件 → 直接渲染 ECharts 图表

形成 **"自然语言 → SQL Agent → 数据 → LLM 摘要 + ECharts JSON → 图表"** 的查询闭环。

---

## 2. 验收标准（必须全部达成）

- [ ] **后端**：新增 `app/services/chart_builder.py`，输入 `(query, query_result_rows)` 返回 `{"chart_type": "...", "echarts_option": {...}} | None`（None 表示数据不适合可视化）
- [ ] **后端**：`chat.py` SSE 流新增 `event: chart`，data 为 `{"echarts_option": {...}, "title": "..."}`，紧跟在 `tool_call` 完成事件之后
- [ ] **后端**：仅当工具结果是表格类（≥ 2 行数据 + 至少一列数值）时才触发，单值/纯文本不触发
- [ ] **后端**：4 类图表必须支持 — 柱状图（bar）/ 折线图（line）/ 饼图（pie）/ 表格降级（table，行数 > 50 时）
- [ ] **后端**：单元测试 `tests/unit/test_chart_builder.py` 覆盖 4 类图表 + None 降级 + 异常输入
- [ ] **后端**：端到端测试，用真实 query "统计本月各部门工时占比" 跑通 SSE，能看到 chart 事件
- [ ] **文档**：`docs/api.md` 补 chart 事件协议（事件名 / data 字段 / 触发条件）
- [ ] **CLAUDE.md**：在 "请求处理流程" 章节加一行说明 chart 事件
- [ ] **前端不在本派单范围**（前端代码在另一个仓库，单独派单）

---

## 3. 数据契约（强约束，前后端约定）

```typescript
// SSE 事件
event: chart
data: {
  "type": "chart",                      // 事件 type 字段（与现有 SSE 风格一致）
  "echarts_option": {                   // ECharts 5.x 标准 option，前端直接 setOption()
    "title": { "text": "..." },
    "tooltip": {...},
    "xAxis": {...},                     // bar/line 必须
    "yAxis": {...},                     // bar/line 必须
    "series": [{...}]
  },
  "chart_type": "bar" | "line" | "pie" | "table",
  "fallback_table": [...]               // 可选；行数 > 50 时降级为表格，前端渲染表
}
```

**LLM 输出 Schema**（用 OpenAI tools 风格 JSON Schema，作为 LLM 调用约束）：

```python
ECHARTS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "render_chart",
        "description": "把表格数据转成 ECharts option，仅当数据适合可视化时返回",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {"enum": ["bar", "line", "pie", "table"]},
                "echarts_option": {"type": "object"},
                "should_render": {"type": "boolean", "description": "数据不适合可视化时为 false"},
            },
            "required": ["chart_type", "should_render"],
        },
    },
}
```

---

## 4. 实施步骤

### 4.1 `chart_builder.py`（核心）

```python
# app/services/chart_builder.py
async def build_chart_option(
    user_query: str,
    tool_result: dict,           # 来自 task_executor 的工具返回
    llm_client: LLMClient,
) -> Optional[dict]:
    """
    返回 {"echarts_option": {...}, "chart_type": "...", "fallback_table": [...]?} 或 None
    """
    rows = _extract_rows(tool_result)
    if not _is_chartable(rows):       # 非表格 / 单行 / 全文本 → None
        return None
    if len(rows) > 50:
        return {"chart_type": "table", "fallback_table": rows[:200]}

    # 调 LLM，强制结构化输出（tools schema + tool_choice="render_chart"）
    option = await llm_client.call_with_tool_choice(...)
    return option
```

### 4.2 `chat.py` SSE 接入

在 `generate_stream()` 内、tool_call 事件之后插入：

```python
if _intent == "tool_execution" and tool_result:
    chart = await build_chart_option(request.message, tool_result, llm_client)
    if chart:
        yield f"event: chart\ndata: {json.dumps({'type': 'chart', **chart}, ensure_ascii=False)}\n\n"
```

注意：必须放在 `yield event` 之后（不阻塞主回复流），失败时静默降级（`logger.warning` + 不发 chart 事件，不影响主流程）。

### 4.3 单元测试

`fastapi-service/tests/unit/test_chart_builder.py`：

- `test_bar_chart_for_department_hours()` — 部门工时数据 → bar
- `test_line_chart_for_daily_trend()` — 日维度时间序列 → line
- `test_pie_chart_for_proportion()` — 占比类查询 → pie
- `test_table_fallback_when_too_many_rows()` — 60 行 → table
- `test_none_when_single_value()` — 单值 → None
- `test_none_when_text_only()` — 纯文本结果 → None
- `test_llm_failure_returns_none()` — LLM 调用失败 → None（不抛异常）

### 4.4 端到端测试

```bash
# 改完后必须重启容器并端到端验证（feedback_agent_commit_discipline 第 2 条）
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"

# 跑一条真实 query，确认 SSE 流里有 event: chart
curl -N -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"统计本月各部门工时占比","session_id":"test-chart-001","user_context":{...}}'
# 期望：能看到 event: chart 行，data 是合法 JSON
```

---

## 5. 工作域边界（**不许动**）

- ❌ 不许动 `docs/benchmarks/` 任何文件（基准测试归档区）
- ❌ 不许动 `docs/interview/` 任何文件（简历定稿区）
- ❌ 不许动 `fastapi-service/tests/benchmark/`（基准代码归档）
- ❌ 不许做 git mv（除非本方案明确写了文件移动）
- ❌ 不许动前端代码（前端在另一个仓库，单独派单）
- ❌ 不许动其他派单方案文件 `docs/changelog/plan-smart-fill-suggestions.md`

---

## 6. Commit 纪律（必读）

使用 conventional commits 格式，**名实相符**：

| 实际改动 | commit 前缀 |
|---------|------------|
| 新增 `chart_builder.py` + chat.py 接入 | `feat(chart): ...` |
| 修 `chart_builder.py` 的 bug | `fix(chart): ...` |
| 加单元测试 | `test(chart): ...` |
| 改 `docs/api.md` / `CLAUDE.md` | `docs: ...` |

**反例（不要这样写）**：
- ❌ `fix(chart): 加 chart_builder 单元测试` → 应该是 `test(chart):`
- ❌ `feat: ECharts 完成` → 没改代码只起草方案时不能写 feat

期望 commit 数量：**3~5 个**（核心实现 1 + 单元测试 1 + e2e 验证 1 + 文档 1 + 可能的修复 1）。

---

## 7. 被迫修复条款

如果在 e2e 验证阶段发现需要修改本方案范围外的代码（例如 `task_executor.py` 没透出 tool_result 的格式），允许：

- 改一处源代码
- 重启容器 + 端到端验证生效
- commit 用 `fix()` 前缀
- 在最终汇报中**显式标注 "为打通 chart 链路被迫修复了 X"**

绝对禁止：隐瞒越界、用 docs/chore 前缀掩盖代码修改。

---

## 8. 完成标志

agent 提交最终汇报时必须包含：

1. 所有 commit 列表（`git log --oneline <分支起点>..HEAD`）
2. 每个 commit 的 stat 一行（`git show --stat <hash> | head -3`）
3. e2e 验证 SSE 输出截图或文本（必须能看到 `event: chart` 行）
4. 单元测试通过截图（`pytest tests/unit/test_chart_builder.py -v`）
5. 是否触发"被迫修复"，触发了改了什么

用户会用 `git show --stat <hash>` 验证每个 commit 是否名实相符（feedback 第 5 条）。
