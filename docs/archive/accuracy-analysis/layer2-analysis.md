# Layer 2 参数提取精度分析报告

> 创建日期：2026-04-03
> 当前最优版本：v4（86.9%，有效精度 99.7%）

---

## 一、Layer 2 概述

Layer 2 在 Layer 1（意图分类正确）的基础上，验证 LLM 提取的工具参数是否正确：
- `params` 中的字段：精确匹配（经 date_resolver 动态替换后）
- `params_exists` 中的字段：只检查"存在且非空"
- `params_fuzzy` 中的字段：跳过值校验
- `description`、`project_id`、`member_id`：自动归入模糊匹配（自然语言字段）

**重要说明**：Layer 2 仅在 Layer 1 通过（intent=tool_execution 且 tool_name 匹配）时才运行。
- `skipped`（180 条）= Layer 1 意图/工具名分类错误，由 Layer 1 负责
- `failed`（4 条）= Layer 2 真正的参数提取错误

---

## 二、版本迭代历史

| 版本 | 整体精度 | PASSED | FAILED | SKIPPED | 核心改动 |
|------|---------|--------|--------|---------|---------|
| v1 | 55.7% | 717 | 288 | 400 | 基线（含测试数据 bug） |
| v2 | 71.7% | 1008 | 288 | 109 | 修复3类测试数据 bug |
| v3 | 79.5% | 1117 | 176 | 112 | description 模糊、date_resolver 补全、edge_cases 修复 |
| **v4** | **86.9%** | **1221** | **4** | **180** | 本月日期变量注入、save_workhour date 默认今天、tool_name 双重校验 |

**有效精度（排除 Layer 1 失败的 skipped）：1221/1225 = 99.7%**

---

## 三、v4 各类别精度

| 类别 | 总数 | 通过 | 精度 | 说明 |
|------|------|------|------|------|
| `query_project` | 200 | 190 | **95.0%** | 剩余 10 条为 Layer 1 问题（skipped） |
| `query_timesheet` | 700 | 653 | **93.3%** | 日期提取、member_name 基本正确 |
| `save_workhour` | 315 | 229 | **72.7%** | save_workhour 类 Layer 1 通过率仍偏低 |
| `edge_cases` | 190 | 149 | **78.4%** | 模糊/混合意图导致 Layer 1 失败较多 |
| **总体** | **1405** | **1221** | **86.9%** | — |

---

## 四、测试数据修复历史（v1 → v4 的基础设施改进）

Layer 2 初始精度 55.7% 中，大量失败源于测试数据本身的 bug，而非 LLM 错误。

### 4.1 测试数据字段名错误（v1 → v2，修复 ~573 条）

| Bug | 影响条数 | 修复方式 |
|-----|---------|---------|
| `save_workhour` 测试数据用 `work_date`/`work_hours`，实际 schema 用 `date`/`duration` | 366 | 重命名字段 |
| `query_project` 测试数据期望 `project_name`（不存在），实际 schema 用 `project_id` | 140 | 改为 `project_id` |
| `query_others` 测试数据要求 `user_id` 存在，但提供 `member_name` 时不注入 `user_id` | 180 | 去除 `user_id` 存在性要求 |

### 4.2 日期解析遗漏（v2 → v3，修复 ~34 条）

`date_resolver.py` 的 `_SUB_TYPE_TO_RANGE` 缺少 `query_others_today` 和 `query_others_by_project` 映射，导致测试运行时"今天"仍用数据生成时的日期。

### 4.3 自然语言字段精确匹配（v2 → v3，修复 ~50 条）

`description`（工作备注）是自然语言字段，LLM 的措辞和测试数据的期望值天然存在偏差（如"周例会"vs"例会"）。将 `description` 加入 auto-fuzzy 集合，只验证存在性而非精确值。

### 4.4 本月日期变量缺失（v3 → v4，修复 ~51 条）

`system.yaml` 未注入 `{month_start}` / `{month_end}` 变量，LLM 不知道"本月"的结束日期，对 `end_date` 给出"今天"而非月末。同步更新了 `tests/test_classification_accuracy.py` 的 `build_state` 函数。

### 4.5 save_workhour 日期默认提示（v3 → v4，修复 ~112 条）

- `save_workhour` 的 `date` 字段 description 更新为"未提及时默认今天"
- `system.yaml` 规则 3 增加"未提及日期时 date 默认填今天（{today}）"

### 4.6 tool_name 双重校验（v3 → v4）

Layer 2 测试增加 `tool_name` 校验：若 Layer 1 把工具名分类错误，Layer 2 也跳过（归 Layer 1 负责），避免污染 Layer 2 精度数字。

---

## 五、剩余 4 条 FAILED（真正的 LLM 参数提取错误）

| 测试 ID | sub_type | 错误类型 | 说明 |
|---------|----------|---------|------|
| qolm_008 | query_others_last_month | member_name 缺失 | LLM 未提取姓名，回退到当前用户 |
| qolm_040 | query_others_last_month | member_name 缺失 | 同上 |
| qolm_046 | query_others_last_month | member_name 缺失 | 同上 |
| qsbp_023 | query_self_by_project | user_id 缺失 | 测试数据 bug：LLM 正确提取了 member_name，但测试仍要求 user_id |

**qolm 根因**：query_others_last_month 部分输入（如"帮我查一下李四上个月工时"）中，LLM 未能识别出姓名并提取为 member_name，而是当作自查（user_id 注入）。这是 LLM 对隐含姓名的识别能力问题。

**qsbp_023 根因**：测试数据 bug — 输入含人名，LLM 正确提取了 member_name，导致 user_id 不注入，但 params_exists 仍要求 user_id。此测试数据期望与实际行为不符。

---

## 六、180 条 SKIPPED 分析（Layer 1 问题）

| 前缀 | 条数 | 根因 |
|------|------|------|
| swhs | 50 | save_workhour 缺参时 LLM 倾向 clarify 而非 tool_execution |
| ec | 41 | 模糊/混合意图，LLM 保守处理为 general_chat |
| swh | 15 | 同 swhs |
| swhp | 13 | 同 swhs |
| qp | 10 | query_project Layer 1 误分类 |
| qobm | 9 | query_others_by_member 部分误判 |
| 其他 | 42 | 分散 |

---

## 七、Layer 2 精度评估

| 指标 | 当前 | 说明 |
|------|------|------|
| 整体精度（含 skipped） | 86.9% | skipped = Layer 1 失败，计入分母 |
| 有效精度（排除 skipped） | **99.7%** | Layer 1 正确时，参数提取几乎完美 |
| 剩余真实 LLM 错误 | 4 条 | 均为 member_name 识别问题 |

**结论**：Layer 2 参数提取能力已达到生产可用水准。当 Layer 1 意图分类正确时，参数提取的准确率高达 99.7%。86.9% 的整体数字反映的主要是 Layer 1（180 条 skipped）的问题，而非参数提取能力本身。

---

## 八、技术债与后续工作

1. **qsbp_023 测试数据 bug**：修复 `params_exists` 中的 `user_id` 要求，改为验证 `member_name`。
2. **qolm member_name 缺失 3 条**：可通过 save_workhour 描述或 few-shot 示例改善，但属于边缘案例，优先级低。
3. **Layer 1 中 180 条 skipped**：提高 Layer 1 精度是下一步重点（参见 `layer1-accuracy-analysis.md`）。
4. **Layer 3 端到端测试**：接入 SpringBoot 做完整链路测试（参数解析 → API 调用 → 结果返回）。
