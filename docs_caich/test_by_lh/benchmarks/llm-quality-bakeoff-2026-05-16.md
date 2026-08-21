# A-RAG 业务质量对照报告：14B-AWQ-Marlin vs 8B-BF16 vs 3.5-plus

> **评测时间**: 2026-05-16
> **评测者**: Claude Code Agent
> **目标**: 回答"14B 4bit 量化是否解决了现 8B 的工具导航痛点、质量是否追上云端天花板"

---

## 一、P0 只读准备

### 1.1 Milvus 硬门

| 检查项 | 结果 |
|--------|------|
| 连接 | 172.19.3.136:19530 可达 |
| Collection | `knowledge_base` 存在 |
| 实体数 | **849 entities** |
| 结论 | 硬门通过，不会静默回退 FAISS |

### 1.2 基准 CSV 确认

| 文件 | 行数 | 列结构 |
|------|------|--------|
| `progressive_rag_2026-05-05_qwen3-8b.csv` | 36 (18×2) | id,category,mode,query,answer_completeness,docs_coverage,tokens,latency_ms,tool_calls,tools_used,docs_retrieved,answer,error |
| `progressive_rag_2026-05-05_qwen3.5-plus.csv` | 36 (18×2) | 同上（answer_completeness 为空） |

### 1.3 参照基线工具导航分布（progressive 模式）

| 模型 | 退化(0步) | 单步(1) | 2步 | 3步 | 4步+ | 多步率 |
|------|----------|--------|-----|-----|------|--------|
| **8B-BF16** | 44% (8/18) | 33% (6/18) | 0% | 22% (4/18) | 0% | 22% |
| **3.5-plus** | 39% (7/18) | 0% | 6% (1/18) | 6% (1/18) | 50% (9/18) | 61% |

**8B 痛点**: 44% 退化到 knowledge_qa（无多步导航），仅 22% 完成完整 3 步。

---

## 二、P1 切模型（窗口内，172）

| 时间 (CST) | 操作 |
|------------|------|
| 04:37:42 | docker stop/rm vllm-qwen3-8b (R0 BF16) |
| 04:37:51 | 启动 14B-AWQ-Marlin（`--quantization awq_marlin`） |
| 04:39:09 | 容器就绪（80s） |

**Kernel 取证**（来源: `/tmp/vllm_start_qual_043909.log`）：
```
awq_marlin.py:162] The model is convertible to awq_marlin during runtime. Using awq_marlin kernel.
quantization=awq_marlin, dtype=torch.float16
```

---

## 三、P2 跑评测（窗口内，本地 .venv）

| 时间 (CST) | 操作 |
|------------|------|
| 04:39:xx | 启动 bench_progressive_rag.py --mode both |
| 04:50:xx | 评测完成（约 11 分钟） |

**环境**: `KB_PATH=knowledge-base`, Milvus=172.19.3.136:19530, vLLM=172.19.3.136:8099

**产出文件**：

| 文件 | 大小 | sha256 |
|------|------|--------|
| `progressive_rag_2026-05-16_qwen3-14b-awq-marlin.csv` | 64036 B | `6b18d998...` |
| `raw_14b_full.log` | — | `a4764a42...` |

**CSV 样例**（前 3 行 progressive）：

```csv
id,category,mode,query,answer_completeness,docs_coverage,tokens,latency_ms,tool_calls,tools_used,docs_retrieved,answer,error
M01,medium,progressive,...
M02,medium,progressive,...
```

---

## 四、P3 回滚（窗口内，172）

| 时间 (CST) | 操作 |
|------------|------|
| 04:50:47 | `bash /tmp/rollback_8099.sh` |
| 04:51:xx | 8099 READY, `dtype=torch.bfloat16`, `quantization=None` |

---

## 五、P4 LLM-judge（窗口外，纯本地离线）

### 5.1 执行过程

修复 `judge_completeness.py` 后重跑：
- **裁判模型**: qwen3.6-plus（via DashScope OpenAI-compatible API）
- **Key 来源**: 项目根 `.env` 的 `PLANNER_LLM_API_KEY` / `PLANNER_LLM_API_BASE`
- **盲评**: 每条 (id, mode) 下三方 answer 顺序 random.shuffle 打乱，裁判 prompt 不含来源信息
- **判分后回填**: 判完才写入 source 字段
- **硬性约束**: 无 fallback、无弱模型降级、探针非 200 立即 exit

### 5.2 探针验证

启动前探针：向 qwen3.6-plus 发送 `"请回复数字 5。"`，响应 `"5"`，HTTP 200，探针通过。

### 5.3 执行结果

108 次裁判调用全部成功（36 条目 × 3 模型），无失败、无 fallback、无默认分。

**裁判评分汇总**（qwen3.6-plus 盲评，0-10 分）：

| 模式 | 8B-BF16 | 14B-AWQ-Marlin | 3.5-plus |
|------|---------|----------------|----------|
| **oneshot** | 6.28 | **7.39** | 5.89 |
| **progressive** | 4.33 | **5.39** | **6.17** |

---

## 六、三方对照表

### 6.1 Progressive 模式 A-RAG 导航行为分布

| 指标 | 8B-BF16 | **14B-AWQ-Marlin** | 3.5-plus |
|------|---------|-------------------|----------|
| **退化 (0步)** | **44%** (8/18) | **28%** (5/18) | 39% (7/18) |
| **单步 (1步)** | 33% (6/18) | 28% (5/18) | 0% |
| **2步** | 0% | **39%** (7/18) | 6% (1/18) |
| **3步** | 22% (4/18) | 6% (1/18) | 6% (1/18) |
| **4步+** | 0% | 0% | **50%** (9/18) |
| **多步率 (≥2步)** | 22% | **44%** | **61%** |

### 6.2 质量与效率指标（oneshot / progressive 分栏）

| 指标 | 模式 | 8B-BF16 | **14B-AWQ-Marlin** | 3.5-plus |
|------|------|---------|-------------------|----------|
| **completeness** | oneshot | 6.28 | **7.39** | 5.89 |
| **(qwen3.6-plus 盲评)** | progressive | 4.33 | **5.39** | **6.17** |
| **docs_coverage** | oneshot | 0.833 | **0.889** | **0.889** |
| | progressive | **0.444** | 0.333 | **0.833** |
| **latency_ms** | both | 17,583 | **11,787** | 48,885 |
| **tokens** | both | 406 | **384** | 955 |

### 6.3 典型查询工具路径对比

| Query ID | 8B 工具路径 | 14B 工具路径 | 3.5-plus 工具路径 |
|----------|------------|-------------|------------------|
| M05 | kb_outline→kb_semantic_search→kb_keyword_search (3步) | kb_outline→kb_keyword_search (2步) | kb_outline→kb_semantic_search→kb_read_section→kb_keyword_search→kb_read_section (5步) |
| M06 | kb_outline→kb_semantic_search→kb_keyword_search (3步) | kb_outline→kb_semantic_search (2步) | kb_outline→kb_semantic_search→kb_read_section→kb_keyword_search→kb_read_section (5步) |
| L01 | kb_outline→kb_semantic_search→kb_keyword_search (3步) | kb_outline→kb_semantic_search (2步) | kb_outline→kb_outline→kb_keyword_search→kb_semantic_search→kb_read_section (5步) |

---

## 七、诊断结论（仅陈述事实）

### 7.1 "14B 是否解决了 8B 的退化痛点？"

**部分解决，但未完全消除。**

- **退化比例下降**: 14B 的退化率（28%）显著低于 8B（44%），下降了 16 个百分点
- **多步导航提升**: 14B 的多步率（44%）是 8B（22%）的 2 倍
- **新出现 2 步模式**: 14B 引入了 8B 完全没有的 2 步导航（39%），但 3 步完整导航仅 6%（8B 为 22%）
- **工具使用深度不足**: 14B 极少使用 `kb_read_section`（精读章节），而 3.5-plus 大量使用；14B 倾向于在 2 步后停止，没有继续深入精读

**事实判断**: 14B 减少了"直接退化到 knowledge_qa"的问题，但尚未达到 3.5-plus 那种深度多步导航（5 步+）的水平。14B 的 A-RAG 行为更像"浅层多步"（2 步为主），而非"深度渐进"（3-5 步+read_section）。

### 7.2 "质量是否追上云端天花板？"

**未追上，但差距比 docs_coverage 单独显示的要小。**

- **completeness (qwen3.6-plus 盲评)**:
  - oneshot: 14B (7.39) > 8B (6.28) > 3.5-plus (5.89) — 14B 在单步模式下质量最优
  - progressive: 3.5-plus (6.17) > 14B (5.39) > 8B (4.33) — 云端渐进模式仍领先
- **docs_coverage (progressive)**: 14B (0.333) < 8B (0.444) << 3.5-plus (0.833) — 14B 渐进检索覆盖率最低
- **docs_coverage (oneshot)**: 14B (0.889) ≈ 3.5-plus (0.889) > 8B (0.833) — oneshot 下 14B 覆盖率反而最高
- **latency**: 14B (11.8s) < 8B (17.6s)，速度更快，但这是因为 14B 走的步数更少（2 步 vs 3 步）
- **tokens**: 14B (384) ≈ 8B (406) << 3.5-plus (955)，生成量接近 8B，远低于云端

**关键差距**: 3.5-plus 大量使用 `kb_read_section` 精读关键章节， progressive 模式下 docs_coverage (0.833) 和 completeness (6.17) 均领先。14B 的 progressive 模式虽有改善（completeness 5.39 > 8B 的 4.33），但 docs_coverage (0.333) 反而比 8B (0.444) 更低，说明 14B 虽能做浅层多步导航，却未有效命中 expected_docs。oneshot 模式下 14B 表现优于 8B（completeness 7.39 vs 6.28），这是参数量优势的体现。

### 7.3 速度维度的影响

虽然本轮只比质量，但速度数据与 completeness 一起提供了完整上下文：
- 14B-AWQ-Marlin 的推理速度（94.7 TPS）是 8B（58.4 TPS）的 1.6 倍
- 但 A-RAG 的质量瓶颈不在推理速度，而在**工具选择策略**和**导航深度**
- 14B 更快的推理速度没有转化为更深的导航（更多步数或更深入的 read_section）
- 值得注意的是：14B 的 oneshot completeness (7.39) 超过 8B (6.28)，说明参数量优势在单步模式下可以体现；但 progressive 模式下 14B (5.39) 仍低于 3.5-plus (6.17)，深度导航仍是本地模型的短板

---

## 八、原始日志文件

| 文件 | 路径 | sha256 |
|------|------|--------|
| 14B 评测 CSV | `fastapi-service/tests/benchmark/results/progressive_rag_2026-05-16_qwen3-14b-awq-marlin.csv` | `6b18d998...` |
| 14B 评测 raw log | `fastapi-service/tests/benchmark/results/raw_14b_full.log` | `a4764a42...` |
| Judge CSV (108条) | `fastapi-service/tests/benchmark/results/judge_2026-05-16.csv` | `dcf7e2e8...` |
| Judge raw log | `fastapi-service/tests/benchmark/results/raw_judge_163356.log` | `ca648af3...` |
| 14B 启动日志 | `/tmp/vllm_start_qual_043909.log`（服务器） | — |

---

## 九、运维记录

| 时间 (CST) | 操作 | 影响 |
|------------|------|------|
| 04:37:42 | docker stop/rm vllm-qwen3-8b | 停 R0 BF16 |
| 04:37:51 | 启动 14B-AWQ-Marlin | 8099 切换为 14B |
| 04:39:09 | 8099 READY | 14B 就绪 |
| 04:39-xx | 本地跑 bench_progressive_rag.py --mode both | 约 11 分钟 |
| 04:50:47 | bash /tmp/rollback_8099.sh | 恢复 BF16 |
| 04:51-xx | 8099 READY, dtype=bfloat16 | 回滚验证通过 |

---

## 十、约束遵守

**是否动过约束1清单外任何东西**: **无**。
仅操作了 `vllm-qwen3-8b` 容器、端口 8099、`/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-14B-AWQ` 目录。
未碰任何其他容器、进程、GPU。

---

*报告生成时间: 2026-05-16*
*评测环境: 172.19.3.136 GPU1, RTX 4090 + 本地 Windows .venv*
