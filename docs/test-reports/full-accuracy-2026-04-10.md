# 意图分类精度测试报告（2026-04-10）

**测试环境**：Ollama qwen3:4b（意图分类）+ qwen3:8b（主对话，Function Calling）
**运行时间**：2h30m
**并发**：4 worker（pytest-xdist）

---

## 总览

| 指标 | 数值 |
|------|------|
| 总用例数 | 2000 |
| 通过 | 1133 |
| 失败 | 867 |
| **通过率** | **56.7%** |

---

## 各类别通过率

| 类别 | 含义 | 通过 | 总数 | 通过率 |
|------|------|------|------|--------|
| gc_ | general_chat（闲聊） | 196 | 204 | **96.1%** |
| qp_ | query_project（项目查询） | 168 | 232 | **72.4%** |
| qsw_ | query_software（软件查询） | 65 | 95 | 68.4% |
| qsdr_ | query_schedule（排班查询） | 47 | 73 | 64.4% |
| qst_ | query_statistics（统计查询） | 38 | 62 | 61.3% |
| swhp_ | save_workhour_project（按项目填报） | 61 | 99 | 61.6% |
| qsl_ | query_statistics_large（统计大） | 44 | 76 | 57.9% |
| kq_ | knowledge_qa（知识问答） | 142 | 258 | **55.0%** |
| ec_ | edge_cases（边缘工具执行） | 71 | 329 | **21.6%** |
| qspm_ | query_statistics_project_monthly | 42 | 118 | 35.6% |
| swh_ | save_workhour（填报工时） | 127 | 373 | **34.0%** |
| qsm_ | query_statistics_monthly | 38 | 122 | 31.1% |
| swhs_ | save_workhour_single（单条填报） | 15 | 85 | **17.6%** |
| qot_ | query_other（其他查询） | 4 | 76 | 5.3% |
| qotw_ | query_other_two | 7 | 93 | 7.5% |
| qobm_ | query_other_business_monthly | 6 | 114 | 5.3% |
| qsbp_ | query_statistics_by_project | 4 | 96 | 4.2% |
| qolm_ | query_other_large_monthly | 3 | 117 | 2.6% |
| qobp_ | query_other_business_project | 0 | 60 | **0.0%** |
| swhm_ | save_workhour_multi（多天填报） | 0 | 120 | **0.0%** |

---

## 关键发现

### 最差类别（需优先改进）

1. **swhm_** (多天填报): 0% — 模型无法正确识别连续多天填报意图
2. **qobp_** (查他人项目工时): 0% — "查张三在A项目填了多少工时" 全军覆没
3. **qolm_** / **qot_** / **qotw_**: <8% — 查询类意图识别极差

### 最好类别

1. **gc_** (闲聊): 96.1% — 闲聊意图识别优秀
2. **qp_** (项目查询): 72.4% — 项目查询意图识别较好

---

## 结论

- **整体通过率 56.7%**，比之前基线（53.6%）略有提升，但不显著
- **qobp / swhm** 是之前已知的 100% 失败场景，仍未解决
- **P1 规则兜底路由**是必经之路：短 keyword query（"工时数据"、"查张三"）不能依赖 LLM Function Calling
