# Function Calling 对话稳定性实验验证方案

> 验证对象：将“意图分类 → 参数提取”两步级联重构为单次 Function Calling  
> 测试结构：单轮对话测试 + 多轮对话测试

## 1. 总体验证目标

验证单次 Function Calling 相较“两步级联”是否能够：

- 减少意图分类错误向参数提取传播。
- 提升工具选择和结构化参数生成的一致性。
- 降低不应调用工具时的误调用率。
- 在复杂多轮对话中稳定继承、更新和清除参数。
- 正确处理用户纠正、指代切换和任务中断恢复。
- 降低缺参情况下的错误执行，尤其是写操作。

实验分为两个层次：

| 层次 | 主要问题 | 是否依赖会话记忆 |
|---|---|---:|
| 单轮对话测试 | 一次输入能否选对工具、提对参数、正确追问 | 否 |
| 多轮对话测试 | 跨轮参数能否正确继承、覆盖、切换和清除 | 是 |

延迟只作为伴随指标，不预设 Function Calling 一定更快。项目已有结果表明，本地 vLLM 下 tools schema 可能增加 prefill 时间。

## 2. 统一实验规范

- 对照组使用相同模型、模型版本、temperature、max_tokens、硬件和网络环境。
- 除路由架构外，System Prompt、工具集合、业务数据和下游执行链路保持一致。
- 数据按语义模板或会话划分为开发集 20%、验证集 20%、最终盲测集 60%。
- 单轮用例至少重复 3 次，多轮会话至少重复 5 次。
- 保存原始请求、响应、工具调用、参数、token、TTFT 和端到端耗时。
- 同一批用例使用配对比较，并通过 Bootstrap 给出 95% 置信区间。
- 写工具只验证调用决策和参数，默认使用 dry-run，禁止测试过程写入生产库。

---

## 3. 单轮对话测试

### 3.1 测试目标

单轮测试不加载历史会话，只验证当前用户输入是否能够：

1. 识别正确意图。
2. 选择正确工具。
3. 提取完整且正确的结构化参数。
4. 在缺少必填参数时追问。
5. 在闲聊、知识问答等场景中避免误调用业务工具。

### 3.2 对照组

| 组别 | 实验链路 | 验证目的 |
|---|---|---|
| A：两步级联 | `intent_classify → param_extract → execute` | 历史架构基线 |
| B：Function Calling | `llm_with_tools → execute` | 验证单次调用的工具选择和提参能力 |
| C：FC + Resolver | `llm_with_tools → param_resolver → execute` | 验证名称、日期到标准业务参数的完整链路 |

A、B 必须使用同一个模型，否则结果只能说明模型差异，不能证明架构收益。

### 3.3 测试数据

项目现有约 2,000 条单轮标注数据：

| 类别 | 数量 | 主要验证内容 |
|---|---:|---|
| 工时查询 | 约 700 | 人员、项目和日期范围提取 |
| 工时填报 | 约 500 | 项目、日期、时长、描述及缺参追问 |
| 闲聊/项目/知识/边界 | 约 800 | 路由、误调用和模糊表达 |

现有测试资产：

- `fastapi-service/tests/test_classification_accuracy.py`
- `fastapi-service/tests/test_param_extraction.py`
- `fastapi-service/tests/data/`

### 3.4 分层回归测试

`test_classification_accuracy.py`和`test_param_extraction.py`仅用于分别定位路由与参数问题，不再生成正式A/B结果。正式准确率、延迟和端到端结论统一由下一节的单次推理脚本产生。

### 3.5 统一单次推理评测（后续 Run 采用）

从 Run 2 起，准确率与延迟评测改用统一脚本：

`fastapi-service/tests/evaluation/run_unified_ab_evaluation.py`

评测规则如下：

1. 每个方案、每条样本只调用一次对应的完整架构。
2. A 组的一次架构调用内部仍执行“意图分类 → 参数提取”；B 组一次 Function Calling 同时返回工具和参数。
3. 意图、工具、参数、联合端到端和单请求延迟全部由同一次输出计算，不再跨 Layer 1、Layer 2 拼接。
4. 原始输出按样本保存，并移除 `auth_token`；报告同时记录模型地址、模型名、请求错误和降级审计。
5. 每 100 条写入一次检查点；长时间运行中断后可通过 `--resume` 从已有结果继续。

运行命令（在 `fastapi-service` 目录执行）：

```powershell
..\.venv\Scripts\python.exe tests/evaluation/run_unified_ab_evaluation.py --variant A --concurrency 8 --output ..\docs_caich\test_by_caic\function_calling_experiment_validation\A_two_stage_unified_vllm_qwen3_8b_run2.json

..\.venv\Scripts\python.exe tests/evaluation/run_unified_ab_evaluation.py --variant B --concurrency 8 --output ..\docs_caich\test_by_caic\function_calling_experiment_validation\B_function_calling_unified_vllm_qwen3_8b_run2.json
```

中断后在原命令末尾增加 `--resume`。仅当结果文件中的 `complete` 为 `true`，且 `summary.errors`、`fallback_audit` 已完成审查时，才可纳入正式 A/B 结论。

原有 `test_classification_accuracy.py` 和 `test_param_extraction.py` 继续保留，仅用于分层回归和问题定位；正式A/B结论统一使用单次推理评测结果。

完整统一口径的云端Run 2已经完成，详见：

- `function_calling_vs_two_stage_unified_cloud_run2.md`
- `A_two_stage_unified_cloud_qwen_plus_run2.json`
- `B_function_calling_unified_cloud_qwen_plus_run2.json`

### 3.6 单轮指标

| 指标 | 计算方式 |
|---|---|
| Intent Accuracy | 正确意图数 ÷ 全部单轮用例数 |
| Tool Selection Accuracy | 正确工具数 ÷ 期望调用工具的用例数 |
| 参数字段准确率 | 正确参数字段数 ÷ 应提取字段总数 |
| 参数整组完全匹配率 | 所有必核参数均正确的用例数 ÷ 参数用例数 |
| 端到端工具调用正确率 | 意图、工具和参数全部正确的用例数 ÷ 工具用例数 |
| 非工具场景误调用率 | 实际调用业务工具的非工具用例数 ÷ 非工具用例数 |
| 缺参正确追问率 | 正确指出缺失参数的用例数 ÷ 缺参用例数 |
| 重复一致率 | 3 次运行中意图、工具和参数完全一致的用例数 ÷ 全部用例数 |

参数统计必须区分：

- 精确字段：日期、时长、枚举等必须精确匹配。
- Resolver 字段：项目名、人员名先验证“已提取”，再由 Resolver 测试名称到 ID 的结果。
- 自然语言字段：描述文本使用要点覆盖或归一化匹配，不能要求逐字一致。

### 3.7 单轮验收标准

- B 的端到端工具调用正确率不得低于 A。
- 核心工具 `query_timesheet`、`save_workhour`、`query_project` 的选择准确率达到 90% 以上。
- 在意图和工具正确的条件下，参数有效准确率达到 95% 以上。
- 非工具场景误调用率低于 2%。
- 写操作缺少项目、日期或时长时，错误执行率为 0。
- 三次重复运行的意图、工具和参数一致率达到 95% 以上。
- 单独报告 TTFT、E2E P50/P95 和 token，不以调用次数推导延迟结论。

### 3.8 单轮现有结果边界

不同时间生成的报告可能对应不同模型、Prompt、工具集合和数据版本。正式测试必须记录 Git commit、数据集哈希、模型和环境配置，不得直接混合比较历史报告。

### 3.9 单轮实验产物

```text
docs_caich/test_by_caic/function_calling_experiment_validation/
  A_two_stage_unified_<model>_runN.json
  B_function_calling_unified_<model>_runN.json
  function_calling_vs_two_stage_unified_<model>_runN.md
```

---

## 4. 多轮对话测试

### 4.1 测试目标

多轮测试验证完整会话状态下：

1. 上一轮的有效人员、项目、日期和时长能否正确继承。
2. 用户纠正参数后，新值能否覆盖旧值。
3. 用户切换人员、项目或任务后，旧参数是否被正确清除。
4. “他、那个项目、上周呢”等指代能否正确解析。
5. 对话被闲聊或其他任务打断后能否恢复。
6. 缺参补齐后能否最终形成正确工具调用。
7. 写操作在确认前是否始终保持 dry-run 或追问状态。

### 4.2 对照组

| 组别 | 实验链路 | 验证目的 |
|---|---|---|
| A：两步 + 历史消息 | 两步级联读取相同会话历史 | 历史架构多轮基线 |
| B：FC + 历史消息 | Function Calling 读取原始对话历史 | 验证 FC 的上下文理解能力 |
| C：完整生产链路 | FC + Resolver + Session Memory | 验证实际会话存储、注入和执行链路 |

三组必须使用相同的历史消息、用户上下文和最大上下文预算。

### 4.3 多轮数据集

现有数据以单轮为主，需要派生至少 150 段、每段 3～6 轮的复杂对话：

| 场景 | 示例 | 建议数量 |
|---|---|---:|
| 缺参逐轮补齐 | “帮我填工时”→“AI 助手项目”→“昨天 8 小时” | 30 |
| 指代继承 | “查张三本周工时”→“那上周呢” | 25 |
| 参数纠正 | “今天 8 小时”→“不对，是昨天 6 小时” | 25 |
| 主体切换 | “查我的”→“再查李四”→“还是看我的” | 20 |
| 混合意图 | 查询后继续统计、填报或生成周报 | 20 |
| 中断恢复 | 任务中插入闲聊，再继续原任务 | 15 |
| 模糊与冲突 | 前后日期、人员或项目存在冲突 | 15 |

每段会话必须标注：

- 每轮期望意图和工具。
- 每轮新增、继承、覆盖、清除的参数。
- 每轮是否应该追问或确认。
- 最终应执行的工具及完整参数。
- 写操作是否允许进入真实执行阶段。

### 4.4 多轮执行方式

- 每段会话使用唯一 `session_id`。
- 同一段的所有轮次顺序发送，不得并发。
- 不同会话之间可以并行，但不能共享 `session_id`。
- 每次重复运行使用新的 `session_id`，避免缓存和历史污染。
- 同时保存模型看到的 `conversation_history`，验证记忆注入是否符合预期。
- 写操作统一启用 `WRITE_DRY_RUN_DEFAULT=true`。

### 4.5 多轮指标

| 指标 | 定义 |
|---|---|
| 会话级任务成功率 | 整段对话最终是否执行正确任务 |
| 逐轮工具准确率 | 每轮正确工具数 ÷ 应调用工具轮次 |
| 参数继承准确率 | 上一轮有效参数在后续轮次正确保留的比例 |
| 参数覆盖准确率 | 用户纠正后新值正确替换旧值的比例 |
| 参数清除准确率 | 任务或主体切换后旧参数正确移除的比例 |
| 指代消解准确率 | 指代对象被正确映射的比例 |
| 上下文污染率 | 旧任务参数错误进入新任务的比例 |
| 错误传播率 | 某轮错误导致后续任务最终失败的比例 |
| 写操作误执行率 | 缺参、冲突或未确认时仍执行写工具的比例 |
| 中断恢复成功率 | 插入其他话题后仍能继续原任务的比例 |
| 重复一致率 | 同一会话重复 5 次得到相同最终工具和参数的比例 |

### 4.6 多轮验收标准

- B 相对 A 的会话级任务成功率提升至少 10 个百分点。
- 参数继承、覆盖和指代消解准确率达到 90% 以上。
- 参数清除准确率达到 95% 以上。
- 上下文污染率低于 2%。
- 中断恢复成功率达到 90% 以上。
- 写操作缺参、冲突或未确认时误执行率为 0。
- 同一会话重复 5 次，最终工具和参数一致率达到 95% 以上。
- C 在真实 Session Memory 链路下不得低于 B 超过 2 个百分点，否则需单独诊断记忆存储或注入问题。

### 4.7 多轮实验产物

```text
docs_caich/test_by_caic/function_calling_experiment_validation/
  fc_multi_turn_dataset.jsonl
  fc_multi_turn_ab_run1.json
  fc_multi_turn_ab_run2.json
  fc_multi_turn_ab_run3.json
  fc_multi_turn_ab_run4.json
  fc_multi_turn_ab_run5.json
  fc_multi_turn_results.csv
  fc_multi_turn_summary.md
```

## 5. 结果记录要求

单轮和多轮逐条结果至少包含：

- case_id、数据集版本和 Git commit。
- 模型、temperature、max_tokens、工具 Schema 版本。
- 用户上下文与完整会话历史。
- 期望和实际意图、工具、参数。
- 是否追问、是否 dry-run、是否发生错误执行。
- 各阶段耗时、输入/输出 token 和缓存命中情况。
- 是否通过及标准化失败类型。

最终报告应分别给出单轮和多轮结论。单轮准确率高不能替代多轮稳定性证据；只有多轮 A/B 达到对应门槛，才能得出“Function Calling 提升复杂对话稳定性”的结论。
