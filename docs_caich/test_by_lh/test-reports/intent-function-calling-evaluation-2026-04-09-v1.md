# Layer 1 意图分类精度测试报告

> 原文件名：`layer1-ollama-qwen3-8b-2026-04-09.md`

**测试时间**: 2026-04-09
**测试模型**: Ollama qwen3:8b（本地部署，Q4_K_M 量化）
**对比基准**: DashScope qwen-plus（云端）
**运行命令**: `pytest tests/test_classification_accuracy.py -v --tb=short -n auto`
**总耗时**: 1h40m（2000 条用例，多进程并行）

---

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例 | 2000 |
| 通过 | 1072 |
| 失败 | 928 |
| **失败率** | **46.4%** |

---

## 失败类型分布

| 失败类型 | 数量 | 说明 |
|---------|------|------|
| intent 错误 | 836 | 意图被误判为 general_chat |
| tool_name 错误 | 92 | 工具选错（如 query_timesheet → compute_statistics）|

### Intent 错误细分

| 期望 Intent | 实际 Intent | 数量 |
|-------------|------------|------|
| tool_execution | general_chat | 565 |
| clarify | general_chat | 129 |
| knowledge_qa | general_chat | 51 |
| clarify | tool_execution | 44 |
| tool_execution | complex_request | 18 |
| tool_execution | clarify | 14 |

### Tool_name 错误细分（92 条）

| 期望工具 | 实际工具 | 数量 |
|---------|---------|------|
| query_timesheet | compute_statistics | 75 |
| query_timesheet | export_report | 7 |
| query_timesheet | query_project | 5 |
| query_timesheet | generate_weekly_report | 3 |
| save_workhour | query_timesheet | 2 |

---

## 各数据文件失败率

| 数据文件 | 失败数/总数 | 失败率 | 分类 |
|---------|-----------|--------|------|
| qobp (查询他人项目工时) | 30/30 | **100%** | 🔴 |
| swhm (多天填报工时) | 60/60 | **100%** | 🔴 |
| qot (查询他人今日工时) | 39/40 | 97.5% | 🔴 |
| qobm (查询他人按成员) | 57/60 | 95.0% | 🔴 |
| qolm (查询他人上月工时) | 56/60 | 93.3% | 🔴 |
| swhs (简单填报) | 46/50 | 92.0% | 🔴 |
| qsbp (按项目查自己工时) | 45/50 | 90.0% | 🔴 |
| qotw (查询他人本周工时) | 44/50 | 88.0% | 🔴 |
| ec (边缘case) | 143/200 | 71.5% | 🟡 |
| qsm (查自己本月工时) | 42/80 | 52.5% | 🟡 |
| qspm (查自己上月工时) | 43/80 | 53.8% | 🟡 |
| swh (工时填报汇总) | 119/250 | 47.6% | 🟡 |
| qsl (查自己上周工时) | 20/60 | 33.3% | 🟢 |
| qsw (查自己本周工时) | 28/80 | 35.0% | 🟢 |
| qsdr (按日期范围查询) | 16/60 | 26.7% | 🟢 |
| qst (查自己今日工时) | 14/50 | 28.0% | 🟢 |
| kq (知识问答) | 55/200 | 27.5% | 🟢 |
| qp (项目查询) | 26/200 | 13.0% | 🟢 |
| swhr (带备注填报) | 8/60 | 13.3% | 🟢 |
| gc (通用对话) | 6/200 | 3.0% | 🟢 |

---

## 典型失败用例

### 1. 意图误判（应为 tool_execution → 实际 general_chat）

| ID | 输入文本 | 子类型 | 期望 Intent |
|----|---------|--------|-------------|
| ec_001 | 查工时 | ambiguous_query_timesheet | tool_execution |
| ec_084 | 查查我最近这段时间的工时录入 | implicit_self | tool_execution |
| ec_086 | 统计一下填报了多少工时 | implicit_self | tool_execution |
| ec_167 | 查下何工录入的工时 | name_with_title | tool_execution |
| qotw_023 | 检查王五本周的填报情况 | query_others_this_week | tool_execution |
| qotw_048 | 看一下吴九本周的工时 | query_others_this_week | tool_execution |

### 2. Tool_name 混淆

| ID | 输入文本 | 期望工具 | 实际工具 |
|----|---------|---------|---------|
| ec_086 | 统计一下填报了多少工时 | query_timesheet | compute_statistics |
| ec_094 | 周报和工时 | query_timesheet | generate_weekly_report |
| ec_095 | 工时统计周报 | query_timesheet | generate_weekly_report |
| ec_020 | 工时报了多少 | query_timesheet | compute_statistics |

### 3. 其他意图错误

| ID | 输入文本 | 期望 Intent | 实际 Intent |
|----|---------|------------|------------|
| gc_142 | 几点下班啊 | general_chat | knowledge_qa |
| kq_118 | 孙涛，请问岗位津贴多少 | clarify | general_chat |

---

## 根因分析

### 1. Ollama qwen3:8b vs DashScope qwen-plus 能力差距

qwen3:8b（Q4_K_M 量化）在以下场景表现明显弱于 qwen-plus：

- **短文本意图识别**：`"查工时"`、`"查查"` 等极短输入，模型倾向于保守回复 `general_chat`
- **隐式意图理解**：`"统计一下填报了多少工时"` 这类 implicit_self 表达，qwen3 无法推断出要查工时
- **tool_name 区分**：`compute_statistics` vs `query_timesheet` 的语义边界模糊

### 2. 100% 失败文件分析

**query_others_by_project (qobp)**：期望行为是查询他人项目工时，但测试用例中大量短句/模糊表达导致模型无法识别为工具调用。

**save_workhour_multi_days (swhm)**：多天填报工时的表达方式多样，模型对复杂参数场景的识别率低。

### 3. 量化精度损失

Q4_K_M 量化导致 4-bit 精度损失，可能是短文本理解能力下降的技术根因。

---

## 对比参考（历史结果）

> 以下为参考数据（未在同一测试环境同时运行，仅作趋势参考）

| 模型 | 失败率 | 备注 |
|------|--------|------|
| DashScope qwen-plus | ~20% | 2026-04-01 测试 |
| Ollama qwen3:8b (Q4_K_M) | **46.4%** | 本次测试 |

---

## 下一步建议

1. **短期**：调整 System Prompt，增加对短文本的意图引导规则，降低保守回复倾向
2. **中期**：对 100% 失败的文件（qobp、swhm）单独优化测试用例或调整期望标签
3. **长期**：考虑 FP16 或更高精度的模型量化，或使用 qwen3:14b（更大参数量的非量化版本）

---

*报告生成时间: 2026-04-09*
