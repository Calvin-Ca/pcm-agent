# 意图分类精度测试报告（vLLM qwen3-8b）

**测试时间**：2026-04-11
**测试模型**：vLLM qwen3-8b（GPU 2，`--served-model-name qwen3-8b`）
**测试环境**：容器内 pytest-xdist 4 workers
**运行时间**：3826.93s (1:03:46)
**评估集**：2000 条测试用例

---

## 总览

| 指标 | 数值 |
|------|------|
| 总用例数 | 2000 |
| 通过 | 1259 |
| 失败 | 741 |
| **通过率** | **62.9%** |

---

## 各类别通过率

| 类别 | 含义 | 通过 | 总数 | 通过率 | 变化 |
|------|------|------|------|--------|------|
| gc_ | general_chat（闲聊） | 195 | 200 | **97.5%** | +1.4% |
| swhr_ | 周报生成 | 59 | 60 | **98.3%** | — |
| qp_ | query_project（项目查询） | 180 | 200 | **90.0%** | +17.6% |
| swhp_ | 按项目填报工时 | 66 | 80 | **82.5%** | +20.9% |
| qsw_ | 查询工时明细 | 64 | 80 | **80.0%** | — |
| qst_ | query_statistics（统计查询） | 40 | 50 | **80.0%** | +18.7% |
| qot_ | query_other（其他查询） | 32 | 40 | **80.0%** | +74.7% |
| qsdr_ | query_schedule（排班查询） | 46 | 60 | **76.7%** | +12.3% |
| qsl_ | query_statistics_large | 46 | 60 | **76.7%** | +18.8% |
| qotw_ | query_other_two | 35 | 50 | **70.0%** | +62.5% |
| kq_ | knowledge_qa（知识问答） | 142 | 200 | **71.0%** | +16.0% |
| qolm_ | query_other_large_monthly | 43 | 60 | **71.7%** | +69.1% |
| ec_ | edge_cases（边缘工具执行） | 84 | 200 | **42.0%** | +20.4% |
| qsm_ | query_statistics_monthly | 36 | 80 | **45.0%** | +13.9% |
| qobm_ | query_other_business_monthly | 19 | 60 | **31.7%** | +26.4% |
| qspm_ | query_statistics_project_monthly | 41 | 80 | **51.2%** | +15.6% |
| qobp_ | query_other_business_project（查他人项目工时） | 7 | 30 | **23.3%** | **+23.3%** |
| swhs_ | save_workhour_single（单条填报） | 2 | 50 | **4.0%** | -13.6% |
| qsbp_ | query_statistics_by_project | 1 | 50 | **2.0%** | -2.2% |
| swhm_ | save_workhour_multi（多天填报） | 0 | 60 | **0.0%** | — |

*变化 = 与 2026-04-10 Ollama qwen3:8b 测试结果的差值*

---

## 关键发现

### 最差类别（需优先改进）

1. **swhm_** (多天填报): 0% — 多工具并行调用编排仍完全失败
2. **qsbp_** (按项目统计): 2% — 极低
3. **swhs_** (单条填报): 4% — 比 Ollama 更差

### 提升最大的类别（vLLM vs Ollama）

1. **qot_** (其他查询): +74.7%（32/40）
2. **qotw_** (其他查询2): +62.5%（35/50）
3. **qolm_** (其他大月查询): +69.1%（43/60）
4. **swhp_** (按项目填报): +20.9%（66/80）
5. **qst_** (统计查询): +18.7%（40/50）

### 比 Ollama 下降的类别

1. **swhs_** (单条填报): -13.6%（2/50 vs 15/85 Ollama）
2. **qsbp_** (按项目统计): -2.2%（1/50 vs 4/96 Ollama）

---

## 结论

- **整体通过率 62.9%**，比 Ollama qwen3:8b（56.7%）提升 **+6.2%**
- **qobp_ 从 0% 提升到 23.3%**，prompt 修复生效
- **swhm_ 仍然是 0%**，多工具并行编排问题未解决
- **qot/qotw/qolm** 大幅提升（+60~75%），vLLM 对复杂查询理解更好
- **swhs_/qsbp_** 反而下降，vLLM 和 Ollama 能力分布不同

---

## 已知问题

- swhm_ (多天填报): 需要模型学会连续 N 天 → 并行 N 次 save_workhour 调用
- swhs_ (单条填报): vLLM 表现比 Ollama 差，需分析原因
- qobp_ (查他人项目): 23.3% 仍低，但比 0% 有进步

---

## 本次修复内容

1. **Bearer token 重复前缀** — `auth_token` 带 "Bearer " 前缀时重复，导致 401
2. **vLLM model 名** — 修正为 `qwen3-8b`（`--served-model-name`）
3. **Ollama 专属参数** — `think=False`/`num_ctx` 只对 Ollama 发送，vLLM 不再产生警告
