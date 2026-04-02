---
name: Layer1精度提升路线图
description: Layer1意图识别精度测试结果、失败根因分析、v3 Prompt改进方案、PlannerAgent架构方向
type: project
updated: 2026-04-02
---

## 精度历史

| 版本 | 整体精度 | 耗时 | 报告文件 |
|------|---------|------|---------|
| v1（改动前基线） | 70.2% | ~2h（单进程） | `fastapi-service/reports/layer1.json` |
| v2（2026-04-02） | 70.9% | 26min（4 workers） | `fastapi-service/reports/layer1_v2.json` |
| 目标 | 92% | — | — |

并行测试命令（pytest-xdist 已安装）：
```bash
cd fastapi-service
../.venv/Scripts/python -m pytest tests/test_classification_accuracy.py \
  -n 4 --dist=load --tb=short \
  --json-report --json-report-file=reports/layer1_vN.json -q
../.venv/Scripts/python tests/utils/accuracy_reporter.py reports/layer1_vN.json
```

---

## v2 失败根因（583条）

| 根因 | 条数 | 备注 |
|------|------|------|
| `clarify` 设计权衡 | ~138 | 单轮测试期望 clarify，LLM 直接执行；多轮对话中是正确行为 |
| `query_timesheet` → `compute_statistics` | 81 | 描述消歧改了但效果不足 |
| `query_timesheet` → `query_project` | 63 | 含项目名查询被误判需先查项目，同 save_workhour 根因 |
| `knowledge_qa` → `general_chat` | 67 | 关键词表覆盖不足 |
| `save_workhour` → `general_chat` | 45 | 口语化填工时表达未覆盖 |
| `query_timesheet` → `general_chat` | 48 | 同上 |

**Why:** 若接受 clarify 138条为合理行为（多轮对话），v2 实际精度约 77.8%，更接近真实场景。

---

## 下一步：Prompt v3（目标约 78-80%）

### 改动1：`query_timesheet` description

**文件**：`fastapi-service/app/tools/query_timesheet.py`，找到工具的 `description=` 字段

```python
# 改为（在现有基础上加"某项目的工时"和"无需先调用 query_project"）
description="查询已填报的工时明细记录（适用：查我的工时/查某人工时/查某段时间工时明细/查某项目的工时）。project_id 可直接填项目名称（系统内部自动解析为ID），无需先调用 query_project。若需汇总统计总工时或做数据分析，请用 compute_statistics"
```

### 改动2：System Prompt 加强分类规则

**文件**：`fastapi-service/app/prompts/system.yaml`，替换工具选择说明部分：

```yaml
**工具选择说明（避免混淆）：**
- "查工时"/"看工时"/"工时是多少"/"某项目工时"/"查某人工时" → query_timesheet（查明细，project_id/member 可填名称，系统自动解析）
- "统计工时"/"工时统计"/"汇总工时"/"工时排名"/"部门工时对比"/"总工时多少" → compute_statistics
- 填报/记录/登记/补填工时 → save_workhour（project_id 可直接填项目名称，无需先调用 query_project）
- 查询"有哪些项目"/"项目列表"/"我参与了哪些项目" → query_project

**以下场景不调用工具，直接文字回答（knowledge_qa）：**
- 询问工时规则/制度/政策（截止时间、加班如何认定、请假期间是否要填、补填流程）
- 示例："工时填报截止几号？"/"加班算工时吗？"/"请假期间要填工时吗？"/"怎么补录工时？"/"工时制度是什么？"/"迟交了怎么办？"

**以下是闲聊，直接回复（general_chat）：**
- 问候、感谢、无关工作的日常对话
```

### 改动3：扩展 `knowledge_keywords`

**文件**：`fastapi-service/app/services/langgraph_agent.py`，约第 169 行，在现有列表末尾补充：

```python
"制度是什么", "有什么规定", "规定是什么", "有规定吗", "有要求吗",
"迟交", "忘填", "漏填", "没填", "补交", "修改", "删除", "撤回",
"怎么处理", "如何处理",
```

---

## 中期：PlannerAgent 架构升级（目标约 85-88%）

**解决问题**：`clarify` 138 条——当前架构无"参数完整性检查"，LLM 判断不稳定

**How to apply:** 引入显式参数检查节点，替代 LLM 自行判断是否追问

**实现方向**：
1. **Planner 节点**：根据意图生成 task plan，显式列出所需参数
2. **参数完整性检查节点**：上下文/历史有参数 → execute；缺参 → clarify（生成追问语）
3. **execution loop**：收集完参数后执行工具

**主要改动文件**：
- `fastapi-service/app/services/langgraph_agent.py`（新增 planner_node、param_check_node）
- `fastapi-service/app/services/task_executor.py`（支持 execution loop）
- `fastapi-service/app/prompts/planner_prompt.yaml`（新增）

---

## 路线总结

```
v2: 70.9%
  └─ v3 Prompt 改动（3项）→ 约 78-80%
       └─ PlannerAgent → 约 85-88%
            └─ Few-shot + qwen-plus 意图识别 → 92% 目标
```
