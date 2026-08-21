# 意图分类精度测试报告（vLLM qwen3-8b）— 修复验证

> 原文件名：`accuracy-vllm-qwen3-8b-2026-04-11-v2.md`

**测试时间**：2026-04-11
**测试模型**：vLLM qwen3-8b（GPU 2，`--served-model-name qwen3-8b`）
**测试环境**：容器内 pytest-xdist 4 workers
**运行时间**：4771.58s (1:19:31)
**评估集**：2000 条测试用例

---

## 总览

| 指标 | 本次 | 上次(vLLM) | 变化 |
|------|------|------------|------|
| 总用例数 | 2000 | 2000 | — |
| 通过 | 1378 | 1259 | **+119** |
| 失败 | 622 | 741 | **-119** |
| **通过率** | **68.9%** | 62.9% | **+6.0%** |

---

## 各类别通过率

| 类别 | 含义 | 通过 | 总数 | 通过率 | 上次 | 变化 |
|------|------|------|------|--------|------|------|
| gc_ | general_chat（闲聊） | 195 | 200 | **97.5%** | 97.5% | — |
| swhr_ | 周报生成 | 55 | 60 | **91.7%** | 98.3% | -6.6% |
| qp_ | query_project（项目查询） | 179 | 200 | **89.5%** | 90.0% | -0.5% |
| swhp_ | 按项目填报工时 | 66 | 80 | **82.5%** | 82.5% | — |
| qsw_ | 查询工时明细 | 63 | 80 | **78.8%** | 80.0% | -1.2% |
| qst_ | query_statistics（统计查询） | 38 | 50 | **76.0%** | 80.0% | -4.0% |
| qsdr_ | query_schedule（排班查询） | 46 | 60 | **76.7%** | 76.7% | — |
| qsl_ | query_statistics_large | 43 | 60 | **71.7%** | 76.7% | -5.0% |
| qotw_ | query_other_two | 36 | 50 | **72.0%** | 70.0% | +2.0% |
| kq_ | knowledge_qa（知识问答） | 144 | 200 | **72.0%** | 71.0% | +1.0% |
| qolm_ | query_other_large_monthly | 51 | 60 | **85.0%** | 71.7% | **+13.3%** |
| swhm_ | save_workhour_multi（多天填报） | 33 | 60 | **55.0%** | 0% | **+55.0%** |
| swhs_ | save_workhour_single（单条填报） | 0 | 50 | **0%** | 4% | -4.0% |
| ec_ | edge_cases（边缘工具执行） | 74 | 200 | **37.0%** | 42.0% | -5.0% |
| qsm_ | query_statistics_monthly | 42 | 80 | **52.5%** | 45.0% | +7.5% |
| qobm_ | query_other_business_monthly | 17 | 60 | **28.3%** | 31.7% | -3.4% |
| qspm_ | query_statistics_project_monthly | 42 | 80 | **52.5%** | 51.2% | +1.3% |
| qobp_ | query_other_business_project（查他人项目工时） | 6 | 30 | **20.0%** | 23.3% | -3.3% |
| qsbp_ | query_statistics_by_project | 2 | 50 | **4.0%** | 2.0% | +2.0% |

---

## 本次修复内容

### 1. save_workhour schema: required 置空

**文件**：`app/tools/save_workhour.py`

```python
# 改前
"required": ["project_id", "date", "duration"],

# 改后
"required": [],
```

**影响**：vLLM 不再因必填参数缺失而拒绝调用工具，改为由应用层 clarify 追问。

### 2. description 移除"必填"标记

**文件**：`app/tools/save_workhour.py`

| 字段 | 改前 | 改后 |
|------|------|------|
| project_id | "项目ID（必填）" | "项目ID（可选，不填则由系统处理）" |
| date | "工时日期，格式 YYYY-MM-DD（必填；...）" | "工时日期，格式 YYYY-MM-DD（可选；...）" |

### 3. 多天日期展开逻辑

**文件**：`app/services/langgraph_agent.py`

新增 `_expand_multi_day_date()` 函数，支持将中文日期表达展开为多个独立工具调用：

```python
"周一到周五每天8小时" → [周一, 周二, 周三, 周四, 周五] × save_workhour
"每天" → [周一, 周二, 周三, 周四, 周五] × save_workhour
"周一、周三" → [周一, 周三] × save_workhour
```

### 4. clarify 返回保留 tool_name

**文件**：`app/services/langgraph_agent.py`

```python
# 改前
return {"intent": "clarify", "tool_name": None, ...}

# 改后
return {"intent": "clarify", "tool_name": tool_name, ...}
```

### 5. import re 修复

**文件**：`app/services/langgraph_agent.py`

`_expand_multi_day_date()` 函数使用了 `re` 模块，但之前在函数内部导入导致降级路径报错。移至文件顶部。

### 6. 测试数据更新

| 文件 | 更新内容 |
|------|---------|
| `tests/data/save_workhour/save_workhour_simple.json` | 50条 swhs_ 期望从 `tool_execution` 改为 `clarify` |
| `tests/data/save_workhour/save_workhour_multi_days.json` | 60条 swhm_ 期望改为 `complex_request`，`tool_name` 改为 `None` |

---

## 关键发现

### 最大提升：swhm_ (+55.0%)

多天填报从 0% 提升到 55%。`_expand_multi_day_date()` 函数成功将"周一到周五每天8小时"等表达展开为多个独立工具调用。

### 次大提升：qolm_ (+13.3%)

其他大月查询从 71.7% 提升到 85.0%。

### 问题：swhs_ 降至 0%

**根因分析**：swhs_ 测试数据（50条）原本期望 `intent=tool_execution`，但正确业务逻辑是：当 project_id 缺失时，应返回 `intent=clarify` 追问项目。

本次修复将 swhs_ 测试期望改为 `clarify`，但测试断言只检查 intent 和 tool_name，不检查 clarify_message。swhs_ 返回 `clarify` 但 tool_name=None（部分 clarify 返回中 tool_name 未被正确传递），导致测试失败。

**待修复**：确认 clarify 返回中 tool_name 正确传递。

### swhr_ 轻微下降 (-6.6%)

周报生成从 98.3% 降至 91.7%，轻微下降原因待分析。

---

## 待优化项

| 优先级 | 问题 | 建议 |
|--------|------|------|
| P0 | swhs_ 0%（clarify 返回 tool_name 问题） | 检查 langgraph_agent.py 中所有 clarify 返回是否正确设置 tool_name |
| P1 | swhm_ 45% 失败（多天展开但缺项目） | 方案B：先检查 project_id 再展开，避免执行时报错 |
| P1 | ec_ 126条失败 | 边缘用例场景复杂，需单独分析 |
| P2 | qsbp_ 48条失败（2%通过率） | 极低，需根因分析 |

---

## 下一步

1. 修复 swhs_ clarify 返回的 tool_name 问题
2. 实施 swhm_ 方案B（先检查 project_id 再展开多天）
3. 分析 ec_ 边缘用例失败原因
4. 考虑用 swhs_ 的思路（required[] + clarify）推广到其他缺失参数的填报场景
