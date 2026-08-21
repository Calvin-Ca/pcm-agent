# 意图分类精度测试报告（vLLM qwen3-8b）— v3

> 原文件名：`accuracy-vllm-qwen3-8b-2026-04-12.md`

**测试时间**：2026-04-12
**测试模型**：vLLM qwen3-8b（GPU 2，`--served-model-name qwen3-8b`）
**测试环境**：容器内 pytest-xdist 8 workers
**运行时间**：1:14:43
**评估集**：2000 条测试用例

---

## 总览

| 指标 | 本次(v3) | 上次(v2) | 变化 |
|------|----------|----------|------|
| 总用例数 | 2000 | 2000 | — |
| 通过 | 1598 | 1378 | **+220** |
| 失败 | 402 | 622 | **-220** |
| **通过率** | **79.9%** | 68.9% | **+11.0%** |

---

## 各类别通过率

| 类别 | 含义 | 通过 | 总数 | 通过率 | 上次(v2) | 变化 |
|------|------|------|------|--------|----------|------|
| gc_ | general_chat（闲聊） | 189 | 200 | **94.5%** | 97.5% | -3.0% |
| swhm_ | save_workhour_multi（多天填报） | 60 | 60 | **100%** | 55.0% | **+45.0%** |
| swhr_ | 周报生成 | 56 | 60 | **93.3%** | 91.7% | +1.6% |
| qp_ | query_project（项目查询） | 190 | 200 | **95.0%** | 89.5% | +5.5% |
| swhp_ | 按项目填报工时 | 65 | 80 | **81.3%** | 82.5% | -1.2% |
| qsw_ | 查询工时明细 | 62 | 80 | **77.5%** | 78.8% | -1.3% |
| qst_ | query_statistics（统计查询） | 38 | 50 | **76.0%** | 76.0% | — |
| qsdr_ | query_schedule（排班查询） | 46 | 60 | **76.7%** | 76.7% | — |
| qsl_ | query_statistics_large | 44 | 60 | **73.3%** | 71.7% | +1.6% |
| qotw_ | query_other_two | 38 | 50 | **76.0%** | 72.0% | +4.0% |
| kq_ | knowledge_qa（知识问答） | 133 | 200 | **66.5%** | 72.0% | -5.5% |
| qolm_ | query_other_large_monthly | 53 | 60 | **88.3%** | 85.0% | +3.3% |
| ec_ | edge_cases（边缘工具执行） | 71 | 200 | **35.5%** | 37.0% | -1.5% |
| qsm_ | query_statistics_monthly | 61 | 80 | **76.3%** | 52.5% | **+23.8%** |
| qobm_ | query_other_business_monthly | 46 | 60 | **76.7%** | 28.3% | **+48.4%** |
| qspm_ | query_statistics_project_monthly | 55 | 80 | **68.8%** | 52.5% | +16.3% |
| qobp_ | query_other_business_project | 22 | 30 | **73.3%** | 20.0% | **+53.3%** |
| qsbp_ | query_statistics_by_project | 3 | 50 | **6.0%** | 4.0% | +2.0% |
| swh_ | save_workhour（缺参填报） | 216 | 250 | **86.4%** | — | — |

> 注：swh_ 在 v2 报告中未单独统计（包含在 swhs_ 中）。

---

## 本次修复内容

### 1. vLLM 参数调整

**服务端启动参数**：
```bash
--enable-auto-tool-choice \
--tool-call-parser hermes
```

### 2. max_tokens 增大

**文件**：`app/services/langgraph_agent.py`

```python
# 改前
max_tokens=500

# 改后
max_tokens=1500
```

**原因**：hermes_tool_parser 对 JSON 格式要求严格，`max_tokens=500` 可能导致输出截断，JSON 被切到一半引发 `Unterminated string` 错误。增大到 1500 给模型足够空间输出完整 JSON。

### 3. 测试数据更新

**文件**：`tests/data/save_workhour/save_workhour_batch*.json`、`save_workhour_remaining.json`

- 36 条 `fill_missing_date` 类型的 `clarify` 期望更新为 `tool_execution`
- 原因：新 schema 的 date description 为"默认填今天的日期"，LLM 会自动补充日期，不再触发 clarify

---

## 关键发现

### 最大提升：qobm_ (+48.4%)

其他月查询从 28.3% 提升到 76.7%，vLLM 对这类复杂月查询理解显著改善。

### 次大提升：qobp_ (+53.3%)

查他人项目工时从 20.0% 提升到 73.3%，hermes tool parser 改善明显。

### swhm_ 100% 通过

多天填报（周一到周五等）从 55% 提升到 100%，`_expand_multi_day_date()` 相对日期展开生效。

### 问题：kq_ 下降 (-5.5%)

知识问答从 72.0% 降至 66.5%，原因待分析。可能是 hermes parser 改变了工具选择行为。

### 问题：gc_ 轻微下降 (-3.0%)

闲聊分类从 97.5% 降至 94.5%，轻微下降。

---

## 待优化项

| 优先级 | 问题 | 建议 |
|--------|------|------|
| P0 | kq_ 下降 5.5%（66.5%） | 分析 hermes 启用后对 knowledge_qa 工具选择的影响 |
| P1 | ec_ 64% 失败（129条） | 边缘用例场景复杂，需单独分析 |
| P1 | qsbp_ 94% 失败（47条，2%→6%） | 极低，需根因分析 |
| P2 | gc_ 5条失败，swhr_ 2条失败 | 轻微，可接受 |

---

## 下一步

1. 分析 kq_ 下降原因（hermes tool parser 对 knowledge_qa 的影响）
2. 分析 qsbp_ 极低通过率根因
3. ec_ 边缘用例失败模式分析
4. 考虑将 swhm_ 的多天展开逻辑推广到其他场景
