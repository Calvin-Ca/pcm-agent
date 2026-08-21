# 06 执行与发布检查表

## 1. 冻结测试配置

- [ ] 记录 Git commit。
- [ ] 记录 CHAT/INTENT/PLANNER/SQL 模型名称、版本和 API 地址类型。
- [ ] 记录 temperature、max_tokens、上下文限制和 tools schema 版本。
- [ ] 记录 Prompt 文件版本或哈希。
- [ ] 记录知识库版本、文档数、chunk 数、Embedding 模型和 Milvus collection。
- [ ] 记录所有 Feature Flag：SQL Agent、A-RAG、Reranker、MultiQuery、dry-run、Tracing。
- [ ] 记录测试数据集版本和哈希。
- [ ] 确认测试使用生产等价配置，且不会写入生产数据。

## 2. 第一阶段：确定性回归

```powershell
cd fastapi-service
..\.venv\Scripts\python.exe -m pytest tests -v --tb=short
```

- [ ] 全量 pytest 通过。
- [ ] 无静默跳过关键测试。
- [ ] 工具注册完整性通过。
- [ ] 权限、write gate、重试、SSE 和会话测试通过。
- [ ] 失败修复后加入永久回归测试。

## 3. 第二阶段：真实模型任务完成率

- [ ] 使用生产模型运行冻结单轮集。
- [ ] 使用生产模型运行冻结多轮集。
- [ ] P0 写场景每条重复 10 次。
- [ ] 统计整体和分业务任务完成率。
- [ ] 统计伪完成率、误执行率和重复调用率。
- [ ] 人工复核全部失败案例。
- [ ] 保存原始模型输出和脱敏工具参数。
- [ ] 零容忍指标全部为 0。

正式 Function Calling 评测可以复用：

```powershell
cd fastapi-service
..\.venv\Scripts\python.exe tests/evaluation/run_unified_ab_evaluation.py `
  --variant B --concurrency 8 `
  --output ..\docs_caich\test_by_caic\pre_production_mandatory_tests\artifacts\fc_release_candidate.json
```

只有结果中的 `complete=true`，且错误和 fallback 审计完成，才可签字。

## 4. 第三阶段：安全与故障

- [ ] 六级角色正向和反向权限矩阵通过。
- [ ] 缺失或伪造身份上下文时 fail closed。
- [ ] write gate 无法被模型参数绕过。
- [ ] Prompt Injection 重复攻击无成功案例。
- [ ] 用户可见内容无 `<think>`、内部 JSON、Token 和系统信息。
- [ ] LLM、Embedding、Milvus、Redis、SpringBoot 故障演练通过。
- [ ] 下游超时不会导致写工具自动重复调用。
- [ ] SSE 错误路径完整结束。
- [ ] 如启用 SQL Agent，SQL 安全专项全部达标。

## 5. 第四阶段：RAG

- [ ] 生产规模知识库加载完整。
- [ ] Recall@5、Recall@10 达标。
- [ ] 答案正确率、忠实度和拒答率达标。
- [ ] 人工复核全部失败和至少 20% 通过案例。
- [ ] A-RAG 无死循环，重复调用和撞顶可解释。
- [ ] Milvus/Embedding 故障行为符合预期。

## 6. 第五阶段：性能和容量

- [ ] 采集分场景 TTFT、E2E、LLM、RAG、Tool 和 Agent 开销。
- [ ] 记录 P50/P95/P99，而非只看平均值。
- [ ] 50 并发或预计峰值 1.5 倍压测通过。
- [ ] 30 分钟容量测试通过。
- [ ] 4～8 小时稳定性测试通过。
- [ ] 无 OOM、进程崩溃、连接泄漏和持续内存增长。
- [ ] Prometheus/Grafana 指标有真实流量数据。
- [ ] 关键告警已经通过故障注入验证。

## 7. 第六阶段：联合最小 E2E

Agent 单方测试通过后，与其他团队只做最小接口验收，不承担其内部实现：

- [ ] 一个只读查询：Agent 请求与 SpringBoot 契约一致。
- [ ] 一个权限拒绝：双方错误码和 SSE 映射一致。
- [ ] 一个写操作 dry-run：不落库。
- [ ] 一个隔离环境真实写：Agent 只调用一次，SpringBoot 团队确认最终状态。
- [ ] 一个下游超时：Agent 不输出成功、不重复写。
- [ ] 双方确认错误码、超时、重试和幂等责任边界。

## 8. 发布结论模板

```text
版本/Commit：
测试时间：
生产模型与配置：
数据集版本：

整体最终任务完成率：
P0 通过率：
写操作误执行率：
写操作伪完成率：
越权执行成功率：
RAG Recall@5 / 忠实度 / 拒答率：
有效 TTFT P50/P95：
E2E P50/P95：
50 并发错误率：
稳定性测试时长与结果：

未解决问题：
已接受风险及负责人：
证据目录：

Agent 后端结论：GO / NO-GO
Agent 负责人：
接口联合验收负责人：
```

## 9. NO-GO 条件

出现以下任意一项直接判定 `NO-GO`：

- 任意越权、敏感信息泄露或错误写操作。
- Agent 声称写成功但工具未成功。
- P0 用例失败。
- 任务完成率、RAG 或性能未达到已批准门槛。
- 存在不可控死循环、OOM、进程崩溃或重试风暴。
- 生产配置与测试配置不一致且未重新验证。
- 缺少原始证据，结果无法复现。
