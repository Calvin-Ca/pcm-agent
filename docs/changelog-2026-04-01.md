# 变更记录 — 2026-04-01

## 核心改动：统一参数解析层 + Bug 修复

### 背景

工具层存在两个已知 Bug：
1. `save_workhour.py`：LLM 可能把项目名（如"AI平台"）填入 `project_id` 字段，直接传给 SpringBoot 导致找不到项目
2. `query_timesheet.py`：用户说"查我的工时"时，`member_name` 为空且 `resolved_user_id` 为空，不传 `memberId`，SpringBoot 返回全员数据

### 改动文件

| 文件 | 改动内容 |
|------|---------|
| `fastapi-service/app/services/param_resolver.py` | **新建**：统一参数解析层，提供 `resolve_project_id()` / `resolve_member_id()`，含进程级内存缓存 |
| `fastapi-service/app/tools/save_workhour.py` | 在校验通过后、发 API 前调用 `resolve_project_id()`，将项目名自动转为数字 ID |
| `fastapi-service/app/tools/query_timesheet.py` | 第143行加 `elif params.user_id` fallback，防止不传 `memberId` 返回全员数据 |
| `docs/springboot-api-reference.md` | **新建**：SpringBoot 接口速查文档（工时/项目/用户，含字段说明和注意事项） |
| `CLAUDE.md` | 更新架构描述、核心服务表、已知问题状态 |

### param_resolver.py 设计说明

```
调用方（工具 handler）
    ↓
resolve_project_id(project_id_or_name, auth_token, base_url)
    ├─ 纯数字 → 直接返回，不发 HTTP
    └─ 名称字符串 → GET /api/project-infos?projectName.contains=xx&page=0&size=10
                    优先精确匹配 projectName，否则取第一条
                    结果写入进程级缓存（_resolve_cache）

resolve_member_id(member_name, auth_token, base_url)
    └─ GET /thsuaa/api/sys-users?entityName.contains=xx&page=0&size=10
       优先精确匹配 entityName，否则取第一条
       结果写入缓存
```

**返回格式**：`(resolved_id, error_message)`，失败时 `resolved_id=None`，调用方直接返回 error_message 给用户。

### Bug 修复详情

**Bug 1 修复（save_workhour.py）**：

```python
# 插入位置：base_url/request_headers 初始化之后，_get_daily_total 调用之前
resolved_project_id, project_err = await resolve_project_id(project_id, auth_token, base_url)
if project_err:
    return {"success": False, "error": f"项目解析失败：{project_err}"}
project_id = resolved_project_id
```

**Bug 2 修复（query_timesheet.py 第143行）**：

```python
if resolved_user_id:
    query_params["memberId"] = resolved_user_id
elif params.user_id:          # ← 新增：fallback 到当前登录用户
    query_params["memberId"] = params.user_id
```

### SpringBoot 接口发现（整理自源码扫描）

- 项目搜索：`GET /api/project-infos?projectName.contains=xx`（非 `/api/project/search`）
- 用户搜索：`GET /thsuaa/api/sys-users?entityName.contains=xx`（路径前缀 `/thsuaa/api`）
- 工时查询：`GET /api/workhour/by-date-range`（不传 `memberId` 返回全员数据，非当前用户）

### 已知问题（待后续处理）

- `param_resolver.py` 使用进程级全局缓存，多实例部署时各实例缓存独立（可接受）
- 项目搜索依赖 Function Calling 注入的 `auth_token`，无 token 时查询会返回 401
