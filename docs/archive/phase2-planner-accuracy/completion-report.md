# Phase 2 剩余任务 — 实施报告

> 日期：2026-04-07 ~ 2026-04-08
> 执行人：Claude Code
> 审查人：已审查（见 impl-phase2-review.md）

---

## 执行摘要

| 任务 | 状态 | 关键产出 |
|------|------|---------|
| Task 1：knowledge_qa few-shot | ✅ 完成 | system.yaml 增加含人名政策问题示例 |
| Task 2：export_report Tool | ✅ 完成 | 新建 export_report.py，注册到 ToolRegistry |
| Task 3：Layer 3 集成测试 | ✅ 完成 | 新建 test_layer3_integration.py |

---

## Task 1：knowledge_qa few-shot

### 改动文件

`fastapi-service/app/prompts/system.yaml`

### 改动内容

在 `## 多工具调用引导` 段落之前，插入：

```yaml
  ## knowledge_qa 工具调用说明

  当用户询问工时制度、政策、规则、流程时，必须调用 knowledge_qa 工具，即使问题中包含人名：
  - "周建国，请问工时截止还剩几天" → 调用 knowledge_qa（询问截止日期规则）
  - "李明本周工时截止几号" → 调用 knowledge_qa（询问截止日期）
  - "陈经工时截止日期到了吗" → 调用 knowledge_qa（询问截止规则）
  - "张三问我加班算不算工时" → 调用 knowledge_qa（询问加班政策）
  - "帮我查一下请假期间要填工时吗" → 调用 knowledge_qa（询问请假政策）

  判断标准：问题的**核心是在询问规则/制度/政策**，人名只是上下文，不改变意图。
```

### 验证结果（Docker 环境）

```
intent: knowledge_qa
msg: 是的，加班算工时。
根据制度：加班工时必须在工时管理系统中如实填报...
📚 来源：工时填报管理制度.md | 假期与加班政策.md | 常见问题FAQ.md
```

RAG 正常工作，知识库检索正常（Milvus + BM25）。

---

## Task 2：export_report Tool

### 新建文件

`fastapi-service/app/tools/export_report.py`

### 核心设计

| 项目 | 值 |
|------|------|
| 工具名 | `export_report` |
| 调用 API | `GET /api/workhour/export/project-simple` |
| 文件存储 | `/tmp/workhour_exports/` |
| 必填参数 | start_date, end_date |
| 权限 | deptAdmin+（AI 服务层 + SpringBoot API 层双重校验） |

### 权限校验实现

AI 服务层 `task_executor.py` 中的 `export_report` 分支：

```python
elif task.tool_name == 'export_report':
    ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
    if permission_context.entity_type not in ADMIN_ROLES:
        raise PermissionError(
            "权限不足：导出报表需要部门管理员及以上角色"
        )
```

验证（employee 角色）：
```
tool: export_report
result.error: "权限不足：导出报表需要部门管理员及以上角色"
```

---

## Task 3：Layer 3 集成测试

### 新建文件

`fastapi-service/tests/test_layer3_integration.py`

### 测试结果（Docker FastAPI :8000，真实 SpringBoot）

```
Layer 3 集成测试结果：
======================================================================
[PASS] general_chat          intent=general_chat  tool=None
[PASS] knowledge_qa          intent=knowledge_qa  tool=None
[PASS] query_self_week       intent=tool_execution  tool=query_timesheet
[PASS] query_self_month      intent=tool_execution  tool=query_timesheet
[PASS] save_with_project     intent=tool_execution  tool=save_workhour
[PASS] save_missing_project  intent=general_chat  tool=None
[PASS] query_project         intent=tool_execution  tool=query_project
[PASS] query_two_members     intent=tool_execution  tool=query_timesheet
======================================================================
通过: 8/8
```

### 权限测试

| 测试 | 角色 | 工具 | 结果 |
|------|------|------|------|
| 审核工时记录 12345 | employee | approve_workhour | ✅ 正确拒绝：`error: "工时审核权限不足：需要部门管理员角色..."` |
| 导出本月工时报表 | employee | export_report | ✅ 正确拒绝：`error: "权限不足：导出报表需要部门管理员及以上角色"` |

---

## 审查修复（审查期间完成）

| 问题 | 修复内容 | 文件 |
|------|---------|------|
| P1 export_report AI 服务层权限校验缺失 | `task_executor.py` 新增 `export_report` deptAdmin+ 角色检查分支 | task_executor.py |
| P2 export_report LLM 工具选择偏差 | `system.yaml` 新增 export_report few-shot 示例，区分 export_report vs compute_statistics | system.yaml |
| P2b approve_workhour LLM 工具选择偏差 | `system.yaml` 新增 approve_workhour few-shot 示例，区分 approve_workhour vs query_timesheet | system.yaml |
| httpx proxy 导致 pytest 无法直连 | `chat()` 添加 `trust_env=False` | test_layer3_integration.py |

---

## 环境说明（最终状态）

| 组件 | 状态 | 说明 |
|------|------|------|
| FastAPI | ✅ Docker :8000 | ai-assistant-service 容器 |
| Redis | ✅ Docker :6379 | healthy |
| Milvus | ✅ Docker :19530 | healthy，RAG 正常 |
| MySQL | ⚠️ 降级 | host.docker.internal 不可达，不影响核心功能 |
| RAG | ✅ 正常 | Milvus + BM25（jieba 分词），加载 4 文档 / 36 chunks |
| Session Memory | ✅ Docker 内 Redis | 正常 |
| SpringBoot | ✅ 在线 | gst.thsware.com |

---

## 修复的 Bug（本次发现并修复）

| Bug | 根因 | 修复 |
|-----|------|------|
| RAG 初始化失败（知识库目录不存在） | `main.py` 中 `_kb_path = os.path.join(os.path.dirname(__file__), "..", "knowledge-base")` — 路径多了一层 `..` | 改为 `os.path.join(os.path.dirname(__file__), "knowledge-base")` |
| BM25 初始化失败（jieba 缺失） | Docker 镜像使用旧版本（未包含 jieba） | `docker-compose build` 重建镜像 |
| knowledge-base 未挂载到 Docker | docker-compose.yml 已正确配置 `./knowledge-base:/app/knowledge-base`，但旧容器未重建 | `docker-compose up -d` 重建容器 |

---

## Git 提交记录

| Commit | 内容 |
|--------|------|
| `d2e3928` | test(layer2): 归档 Layer2 精度测试报告（v1/v2/v3） |
| `25d44a9` | docs: 更新 roadmap — 标记 Phase1/Phase2 已完成 |
| `fde6f92` | docs: 新增 Phase 2 PlannerAgent 激活设计文档 |
| `4b71e05` | feat(phase2): 激活 PlannerAgent — 多步规划 + 并行执行 + 结果汇总 |
| `2b22812` | feat(phase1): approve_workhour Tool + jieba 中文分词接入 BM25 |
| `???` | feat(system): knowledge_qa/export_report few-shot + export_report Tool + Layer3 测试 + RAG path fix |

---

## 下一步建议

1. **Layer 1 精度测试**：Milvus 已就绪，运行 Layer 1 分类精度测试，对比 v5 基线（82.6%）验证 knowledge_qa few-shot 效果
2. **Phase 3 规划**：根据 roadmap 继续下一阶段功能开发
3. **Layer 3 测试常态化**：test_layer3_integration.py 已可 pytest 直接运行（trust_env=False 已修复）
