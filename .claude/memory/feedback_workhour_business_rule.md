---
name: 加班工时业务规则
description: 工时表(workhour)中加班工时的判定标准，影响统计工具和SQL Agent的查询逻辑
type: project
---

## 规则

**加班工时 ≠ `workhour - 8`**（按天超出标准工时的计算方式是错误的）

**加班工时 = `work_type = "其他工时"` 的工时记录**

- 数据表：`workhour` 表中有 `work_type` 字段
- 判定条件：`work_type == "其他工时"`
- 这意味着：同一条记录要么是正常的项目工时，要么是加班工时，由 `work_type` 字段区分

**Why:** 2026-04-25 基准测试修复时，最初假设加班工时 = 总工时 - 8（标准工时），但用户纠正这是错误的。`work_type` 字段才是业务上区分正常工时和加班工时的依据。

**How to apply:**
1. `compute_statistics` 统计加班时长时，必须按 `work_type = "其他工时"` 筛选，不能简单做减法
2. SQL Agent 生成涉及加班的查询时，prompt/schema 要包含这个条件
3. 测试数据构造时要考虑 `work_type` 字段的存在
