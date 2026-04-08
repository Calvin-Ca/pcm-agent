# Phase 2 剩余任务 — 审查报告

> 审查日期：2026-04-08
> 审查人：Claude Code（自动审查 agent）

---

## 审查结果

### 总体评价

Phase 2 三个任务的文件均已创建，代码结构完整。审查发现的问题均已修复并验证通过。

---

## 修复状态（最终）

| 问题 | 优先级 | 状态 | 修复内容 |
|------|--------|------|---------|
| P1 export_report AI 服务层权限校验缺失 | P1 | ✅ 已修复并验证 | `task_executor.py` 新增 `export_report` deptAdmin+ 角色检查分支 |
| P2 export_report LLM 工具选择偏差 | P2 | ✅ 已修复并验证 | `system.yaml` 新增 export_report few-shot，Docker 重启后生效 |
| P2b approve_workhour LLM 工具选择偏差 | P2 | ✅ 已修复并验证 | `system.yaml` 新增 approve_workhour few-shot |
| P3 httpx proxy 影响 pytest | P3 | ✅ 已修复并验证 | `test_layer3_integration.py` 的 `chat()` 添加 `trust_env=False` |
| P3 Milvus 不可用（RAG 失败） | P3 | ✅ 已修复并验证 | Docker Desktop 启动后 + `docker-compose up -d` 重建容器 |

---

## 发现的问题及修复详情

### P1 — export_report 权限校验未实现（✅ 已修复）

**问题**：计划要求 employee 调用 export_report 时被 AI 服务层主动拒绝，实际只有 SpringBoot API 层 403。

**修复**：在 `task_executor.py` `_execute_tool_call` 方法中新增分支：

```python
elif task.tool_name == 'export_report':
    ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
    if permission_context.entity_type not in ADMIN_ROLES:
        raise PermissionError(
            "权限不足：导出报表需要部门管理员及以上角色"
        )
```

**验证**：employee 角色调用"导出本月工时报表"→ `error: "权限不足：导出报表需要部门管理员及以上角色"` ✅

---

### P2 / P2b — LLM 工具选择偏差（✅ 已修复）

**问题**：LLM 将"审核工时"路由到 `query_timesheet`，将"导出报表"路由到 `compute_statistics`。

**修复**：在 `system.yaml` 新增 few-shot 示例，明确区分：

```yaml
## export_report 工具调用说明
当用户要求导出工时报表...必须调用 export_report 工具（不是 compute_statistics）

## approve_workhour 工具调用说明
当用户要求审核（通过/批准）工时记录时，必须调用 approve_workhour 工具（不是 query_timesheet）
```

**验证**：
- "审核工时记录 12345" → `approve_workhour` ✅
- "导出本月工时报表" → `export_report` ✅

**注意**：few-shot 需服务重启（prompt 非热加载）才能生效。

---

### P3 — httpx proxy 影响 pytest（✅ 已修复）

**问题**：httpx 默认读取 `HTTP_PROXY` 环境变量，导致测试请求被路由到代理（502）。

**修复**：`test_layer3_integration.py` 的 `chat()` 函数：

```python
with httpx.Client(timeout=60.0, trust_env=False) as client:
```

**验证**：pytest 直接运行，无需手动设置 `no_proxy`。

---

### P3 — Milvus 不可用（✅ 已修复）

**问题**：RAG 初始化失败，日志显示 `No module named 'jieba'`。

**根因**：Docker 镜像为旧版本（未包含 jieba）。

**修复**：
1. `docker-compose build ai-service` 重建镜像（包含 jieba）
2. `docker-compose up -d` 重建容器

**额外修复**：`main.py` 中 RAG 路径构造 bug（`os.path.join(os.path.dirname(__file__), "..", "knowledge-base")` 应为 `"knowledge-base"`），已在重建后生效。

**验证**：
```
✅ LangChain RAG 服务初始化完成（向量后端: Milvus，文档块: 36）
✅ "加班算工时吗" → 返回真实知识库内容
```

---

## 下一步建议

1. **Layer 1 精度测试**：Milvus 已就绪，运行分类精度测试验证 knowledge_qa few-shot 效果
2. **Phase 3 规划**：参考 `docs/roadmap.md` 继续下一阶段
3. **Layer 3 测试常态化**：test_layer3_integration.py 可直接用 pytest 运行
