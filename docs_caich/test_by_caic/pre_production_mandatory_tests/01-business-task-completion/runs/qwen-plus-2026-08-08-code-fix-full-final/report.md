# 业务任务完成率测试报告

- 模型：`qwen-plus`
- 模式：`baseline`
- 执行进度：420 / 420
- 最终完成率：50.71%（213/420）
- 伪完成：0
- 重复工具调用：0
- 重复写调用：0
- 不存在的工具调用：0
- 未授权写调用：3
- 结论：**不通过**

## 分类别

| 类别 | 通过 | 总数 | 完成率 |
|---|---:|---:|---:|
| approve_workhour | 4 | 12 | 33.33% |
| batch_save_workhour | 30 | 30 | 100.00% |
| complex_task | 4 | 20 | 20.00% |
| compute_statistics | 16 | 30 | 53.33% |
| export_report | 6 | 12 | 50.00% |
| general_chat | 12 | 20 | 60.00% |
| generate_weekly_report | 23 | 25 | 92.00% |
| kb_navigation | 5 | 24 | 20.83% |
| knowledge_qa | 24 | 25 | 96.00% |
| multi_turn | 6 | 100 | 6.00% |
| query_project | 21 | 25 | 84.00% |
| query_timesheet | 20 | 30 | 66.67% |
| robustness | 7 | 20 | 35.00% |
| save_workhour | 25 | 35 | 71.43% |
| suggest_workhour | 10 | 12 | 83.33% |

## 说明

本报告调用真实 LLM 和 Agent 编排，但所有业务工具、RAG、会话存储和下游服务均使用进程内隔离 Mock。SpringBoot、Redis、MySQL、Milvus 未参与。
