---
name: Layer1/Layer2精度提升路线图
description: Layer1/Layer2意图识别与参数提取精度测试结果、失败根因分析、当前状态
type: project
updated: 2026-04-07
---

## Layer 1 精度历史

| 版本 | 整体精度 | 核心改动 |
|------|---------|---------|
| v1 | 70.2% | 基线 |
| v2 | 70.9% | 关键词扩展 + 工具描述消歧 |
| v3 | 63.5% | 引入完整 system.yaml（含决策树）→ 反而退步 |
| v4 | 74.9% | 精简 system.yaml 为 5 条核心规则 |
| **v5** | **82.6%** | knowledge_qa 注册为工具 + compute_statistics 描述修正 |

**最重要结论**：给 LLM 加规则约束（决策树）会抑制 Function Calling 原生能力；精简 prompt + 精准工具描述是正确方向。

### v5 各类别精度

| 类别 | 精度 | 状态 |
|------|------|------|
| `general_chat` | 99.5% | ✅ 超目标 |
| `query_project` | 96.0% | ✅ 超目标 |
| `query_timesheet` | 93.0% | ✅ 超目标 |
| `edge_cases` | 74.5% | 接近目标 |
| `knowledge_qa` | 63.0% | ⚠️ 差距大，需架构改动 |
| `save_workhour` | 67.0% | ⚠️ 差距大，需 PlannerAgent |

### v5 剩余 348 条失败根因

- save_workhour 165 条：142 条是 clarify vs tool_execution 设计权衡，需 PlannerAgent 解决
- knowledge_qa 74 条：含人名时 LLM 倾向直接回答而不调用工具
- query_timesheet 49 条：口语化查询 + compute_statistics 歧义残留
- edge_cases 51 条：模糊/混合意图

---

## Layer 2 精度历史（参数提取）

| 版本 | 整体精度 | 有效精度 | 核心改动 |
|------|---------|---------|---------|
| v1 | 55.7% | — | 基线（含测试数据 bug）|
| v2 | 71.7% | — | 修复测试数据 bug |
| v3 | 79.5% | — | description 模糊 + date_resolver 补全 |
| **v4** | **86.9%** | **99.7%** | 本月日期变量注入 + save_workhour date 默认今天 |

**结论**：Layer 1 意图分类正确时，参数提取准确率 99.7%，已生产可用。

---

## 并行测试命令

```bash
cd fastapi-service
../.venv/Scripts/python -m pytest tests/test_classification_accuracy.py \
  -n 4 --dist=load --tb=short \
  --json-report --json-report-file=reports/layer1_vN.json -q
../.venv/Scripts/python tests/utils/accuracy_reporter.py reports/layer1_vN.json
```

---

## 下一步（第二阶段剩余）

- SQL Agent（只读连接 + SQL 白名单，1.5d）
- 导出报表 Tool export_report（2h）
- Layer 1 收尾：knowledge_qa 含人名 few-shot（+2~3%，约 1h）
- Layer 3 端到端：接 SpringBoot 完整链路测试
