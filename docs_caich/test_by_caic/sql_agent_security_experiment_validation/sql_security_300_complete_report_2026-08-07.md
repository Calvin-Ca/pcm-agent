# SQL Agent 300 条安全实验综合验证报告

日期：2026-08-07  
实验对象：SQL Agent 自然语言生成 SQL、硬规则校验、权限上下文和服务端强制权限注入  
数据库状态：本报告中的离线生成实验未连接数据库，未执行生产库查询

## 1. 实验范围

测试数据共300条，由人工定义模板族后确定性生成变体，不是模型自由生成的测试真值：

| 类别 | 数量 | 目标 |
|---|---:|---|
| 合法查询 | 120 | 检查正常SQL生成与误拦截 |
| 明确攻击 | 100 | 检查DDL/DML、敏感数据、危险函数和Prompt Injection |
| 越权查询 | 80 | 检查用户、部门、区域和公司范围限制 |
| 合计 | 300 | 离线安全链路验证 |

数据文件：`data/sql_security_all_300.jsonl`。生成脚本：`generate_sql_security_dataset.py`。

测试覆盖六类角色：`employee`、`deptSubAdmin`、`deptAdmin`、`regionAdmin`、`companyAdmin`、`superAdmin`。

## 2. 测试链路

### 初始复测

```text
自然语言问题
→ qwen-plus 生成候选SQL
→ validate_sql
→ 记录生成结果
```

### 强制权限注入复测

```text
自然语言问题
→ qwen-plus 生成候选SQL
→ 初次 validate_sql
→ 服务端按可信身份改写受保护数据源
→ 改写后的SQL再次 validate_sql
→ 参数化执行（本轮未连接数据库）
```

受保护数据源包括：`workhour`、`workhour_attendance`、`project_member`、`sys_user`。

## 3. 300条首次复测结果

原始结果：`data/sql_security_runs_300.csv`。

| 类别 | 结果 | 说明 |
|---|---:|---|
| 合法查询 | 120/120 | 生成SQL并通过形式安全校验 |
| 明确攻击安全处理 | 100/100 | 本轮均未执行危险操作；其中硬规则拦截42条，LLM改写58条 |
| 越权范围标识存在 | 19/80 | 仅为SQL文本标识检查 |
| 模型调用失败 | 0/300 | 全部获得模型输出 |

### 结果含义

合法查询120/120只表示生成SQL能被解析并通过当前规则，没有数据库执行和oracle对比，不能称为SQL结果正确率100%。

100条明确攻击中：

- 42条是候选SQL被硬规则拒绝。
- 58条是模型本轮主动输出了安全SQL，例如将破坏操作改写为`SELECT 1`。

这58条属于LLM安全改写，不是确定性安全拦截；因此“100/100安全处理”不能表述为“硬规则拦截率100%”。

80条越权查询中的19/61是改造前仅通过字符串搜索得到的结果，不能等同于实际权限通过或实际数据泄露。

## 4. 权限上下文字段修复

首次复测期间发现：

```text
ToolExecutionContext object has no attribute 'managed_departments'
```

修复内容：

- `_build_permission_constraints` 使用正确的`PermissionContext`。
- `TaskExecutor`透传`managed_departments`和`managed_projects`。
- 测试身份补充管理员管辖部门集合。
- 增加权限上下文组件测试。

仅修复字段后，原先61条缺失用例重测结果为：

| 结果 | 数量 |
|---|---:|
| 出现范围标识 | 11/61 |
| 仍缺失范围标识 | 50/61 |
| 模型调用失败 | 0/61 |

结论：字段修复消除了上下文异常，但不能保证LLM把权限条件写进SQL；Prompt权限提示仍然不是强制安全边界。

## 5. 服务端强制权限注入实现

新增`enforce_sql_permissions()`，在执行前对受保护表进行数据源级改写。

员工查询示例：

```sql
FROM workhour wh
```

改写为：

```sql
FROM (
  SELECT *
  FROM workhour AS __perm_src
  WHERE __perm_src.member_id = :perm_user_id
) AS wh
```

管理员通过可信管辖部门集合关联`sys_user.org_id`过滤。权限值使用绑定参数，不拼接用户输入。改写后会再次执行`validate_sql`。

安全行为：

- 缺少权限上下文：直接拒绝。
- 管理员缺少管辖范围：直接拒绝。
- `OR 1=1`：不能移除派生数据源内部的范围条件。
- UNION：每个受保护数据源分别改写。
- `superAdmin`：按现有角色定义保持无限制。

相关实现：

- `fastapi-service/app/tools/sql_query.py`
- `fastapi-service/app/services/sql_engine.py`
- `fastapi-service/app/services/task_executor.py`

## 6. 强制注入后的61条复测

原始结果：`data/sql_security_scope_missing_61_forced_rerun.csv`。

| 角色 | 强制注入成功 | 失败 |
|---|---:|---:|
| employee | 25 | 0 |
| deptAdmin | 16 | 0 |
| regionAdmin | 12 | 0 |
| companyAdmin | 8 | 0 |
| 合计 | 61 | 0 |

61条候选SQL原本全部缺少预期权限标识；经过服务端改写后，61条最终SQL全部完成权限注入并通过二次安全校验，模型调用和注入错误均为0。

这证明的是：

```text
服务端权限注入成功率 = 61/61
```

尚不能证明：

```text
数据库实际越权泄露率 = 0%
```

后者必须在隔离数据库中执行并检查哨兵数据。

## 7. 自动化测试结果

权限专项和任务执行回归：

```text
37 passed
```

覆盖：

- employee用户范围
- 部门/区域管理员范围
- OR绕过
- UNION分支
- 参数绑定
- 缺失上下文fail closed
- 管理员范围缺失拒绝
- superAdmin例外
- TaskExecutor权限字段透传

测试文件：`fastapi-service/tests/test_sql_query_permission_constraints.py`。

## 8. 当前结论

### 8.1 完整测试结果汇总

| 类别 | 结果 | 说明 |
|---|---|---|
| 合法查询 | 120/120通过形式安全校验 | 未执行数据库，不能称为结果正确率100% |
| 明确攻击安全处理 | 100/100，100% | 其中硬规则拦截42条，LLM改写58条；改写属于辅助处理，不等于硬拦截 |
| 越权范围标识存在 | 80/80，100% | 权限字段修复和服务端强制注入后的最终SQL均包含可信范围条件 |
| 模型调用失败 | 0/300，0% | 300条测试均成功获得模型输出 |

### 8.2 结论口径

本次300条离线实验及61条强制注入复测已经完成，结论如下：

1. 当前LLM生成链路可以生成合法SQL，但本轮未验证数据库结果正确性。
2. 明确攻击的硬规则拦截为42/100；58/100属于LLM改写，不能合并成硬拦截率。
3. 仅修复权限上下文后仍有50/61条缺少SQL范围标识，证明Prompt权限约束不可靠。
4. 实现服务端强制权限注入后，61/61条最终SQL完成范围改写。
5. 当前仍缺少隔离数据库、角色哨兵数据和oracle结果对比，因此完整的结果级越权验收尚未完成。

## 9. 下一步

1. 准备隔离MySQL和只读账号。
2. 插入多公司、多部门、多用户哨兵数据。
3. 执行300条数据中的合法和越权查询。
4. 对明细、JOIN、UNION、子查询和聚合结果进行哨兵比对。
5. 最终报告越权泄露率、合法结果正确率和各角色隔离结果。

