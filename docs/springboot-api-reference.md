# SpringBoot 后端 API 参考（AI 服务开发用）

> 路径前缀：`http://localhost:8080`（本地开发）/ `http://host.docker.internal:8080`（Docker 内）
> 所有接口均需携带 `Authorization: Bearer <token>` 请求头

> **标注图例**（两个独立维度：① agent 是否调用　② Java 后端是否存在）：
> - `[✅实调]` = AI agent 代码当前实际调用（后端必然存在）
> - `[⬜预留·后端已验证]` = agent 未调用，但 Java 后端**已实测存在**（经隧道用 JWT 探测，200 或权限 4xx）
> - `[⬜预留·未探测]` = agent 未调用，后端是否存在**未验证**（多为写接口，不便实测以免污染生产）
>
> 截至最近一次核实：agent 实调 9 个端点；预留只读端点 14 个已全部实测后端存在（探测日期见文末）。

---

## 一、用户/人员接口（`/thsuaa/api/sys-users`）

> Controller：`SysUserResource.java`，路径前缀 `/thsuaa/api`

### 核心字段（SysUserDTO）

| 字段 | 说明 |
|------|------|
| `id` | 用户账号ID（工时表中的 `memberId` 对应此字段） |
| `entityId` | 员工实体ID（与 `id` 不同） |
| `entityName` | 员工姓名（搜索时使用此字段） |
| `entityType` | 角色类型（`employee`/`deptAdmin`/`companyAdmin` 等） |
| `orgId` | 所属机构ID |
| `deptId` | 所属部门ID |
| `userName` | 登录账号（返回时做脱敏处理） |

### 接口列表

```
GET  /thsuaa/api/sys-users                          [✅实调]
     Query: entityName.contains=姓名, entityType.equals=xx, orgId.equals=xx, deptId.equals=xx
            page=0, size=10
     返回：SysUserDTO 列表（分页，Header 含 X-Total-Count）
     调用方：param_resolver.py、query_timesheet.py（按姓名解析 memberId）

GET  /thsuaa/api/sys-users/{id}                     [⬜预留·后端已验证]
     返回：单个 SysUserDTO

GET  /thsuaa/api/sys-users-manager                  [⬜预留·后端已验证]
     Query: 同上（含项目负责人视角的权限过滤）

GET  /thsuaa/api/all-sys-users                      [⬜预留·后端已验证]
     返回：所有用户列表（无分页，慎用）

GET  /thsuaa/api/sys-users/hasUserName/{username}   [⬜预留·后端已验证]
     返回：Boolean，账号是否存在

POST /thsuaa/api/sys-users              — 创建用户（需管理员权限）  [⬜预留·未探测]
PUT  /thsuaa/api/sys-users              — 更新用户（需管理员权限）  [⬜预留·未探测]
PUT  /thsuaa/api/sys-users/self         — 当前用户更新自己          [⬜预留·未探测]
DELETE /thsuaa/api/sys-users/{id}       — 软删除（需管理员权限）    [⬜预留·未探测]
DELETE /thsuaa/api/sys-users/real/{id}  — 硬删除（需管理员权限）    [⬜预留·未探测]
```

### AI 服务使用场景

```python
# 按姓名查询用户 ID（param_resolver.py 中使用）
GET /thsuaa/api/sys-users?entityName.contains=张三&page=0&size=10
→ 取 id 字段作为 memberId
```

---

## 二、工时接口（`/api/workhour`）

> Controller：`WorkhourResource.java`

### 核心字段（Workhour）

| 字段 | 说明 |
|------|------|
| `id` | 工时记录ID |
| `memberId` | 员工ID（对应 sys_user.id） |
| `projectId` | 项目ID |
| `projectName` | 项目名称（只读，冗余字段） |
| `workhourDate` | 工时日期（ISO instant 格式，如 `2026-03-31T00:00:00Z`） |
| `workhour` | 工时时长（小时，0.5步长） |
| `workContent` | 工作内容描述（DTO 驼峰；DB 列名为 `work_content`） |
| `workType` | 工时大类（如"研发工作"，写死会被 SpringBoot 校验拒绝，需按用户/部门取） |
| `workhourType` | 工时类别（"正常工时"/"其他工时"，由 work_calendar.is_work_day 决定） |

### 接口列表

```
POST /api/workhour                                  [✅实调]
     Body: { projectId, workhourDate, workhour, workContent?, workType, workhourType, memberId? }
     说明：填报工时。无 memberId 时填报当前登录用户
     注意：JSON 字段 workContent（驼峰），DB 列 work_content（蛇形），不是 description
     调用方：save_workhour.py、batch_save_workhour.py（写）

GET  /api/workhour/by-date-range                    [✅实调]
     Query: startDate=YYYY-MM-DD, endDate=YYYY-MM-DD, memberId?, projectId?, orgId?
     返回：工时记录数组（直接返回列表，非分页对象）
     ⚠️ 不传 memberId 时返回全员数据（非当前用户），需显式传入
     调用方：query_timesheet / compute_statistics / param_resolver / work_type_resolver
            / save_workhour / batch_save_workhour（调用最频繁的端点）

GET  /api/workhour/last-record                      [⬜预留·后端已验证]
     Query: memberId
     返回：用户最后一条工时记录

GET  /api/workhour/total-workhour                   [⬜预留·后端已验证]
     Query: startDate, endDate
     返回：工时合计统计

GET  /api/workhour/user-stats                       [⬜预留·后端已验证]
     Query: startDate, endDate
     返回：所有用户工时统计
     注：compute_statistics 是自取 by-date-range 原始记录在 Python 侧算统计，未用此端点

GET  /api/workhour/dept-members-stats               [⬜预留·后端已验证]
     Query: startDate, endDate, orgId（管理员权限）
     返回：部门成员工时统计
     注：employee 账号探测返回 400「没有权限查看」→ 端点存在，仅管理员可用

POST /api/workhour/batch-approve                    [✅实调]
     Body: List<String> workhourIds
     说明：批量审核工时
     调用方：approve_workhour.py（写）

GET  /api/workhour/export/project-simple            [✅实调]
     Query: startDate, endDate, title?, orgId（管理员权限）
     返回：Excel 文件（工时汇总表）
     调用方：export_report.py

PUT  /api/workhour/{id}    — 更新工时记录   [⬜预留·未探测]
DELETE /api/workhour/{id}  — 删除工时记录   [⬜预留·未探测]
```

---

## 三、项目接口（`/api/project-infos`）

> Controller：`ProjectInfoResource.java`
> 使用 JHipster Criteria 过滤语法，支持 `.equals`、`.contains`、`.in` 等操作符

### 核心字段（ProjectInfoDTO）

| 字段 | 说明 |
|------|------|
| `id` | 项目ID（String） |
| `projectName` | 项目名称 |
| `managerId` | 项目负责人ID |
| `managerName` | 项目负责人姓名 |
| `projectStage` | 项目阶段 |
| `projectType` | 项目类型 |

### 接口列表

```
GET  /api/project-infos                             [✅实调]
     Query（JHipster Criteria 语法）:
       projectName.contains=名称关键词
       projectName.equals=精确名称
       id.equals=项目ID
       id.in=id1,id2,id3
       managerId.equals=负责人ID
       page=0, size=10
     返回：ProjectInfoDTO 列表（Header 含 X-Total-Count）
     调用方：query_project.py、param_resolver.py（按项目名解析 projectId）

GET  /api/project-infos/{id}                        [✅实调]
     返回：单个 ProjectInfoDTO
     调用方：query_project.py（项目详情）

GET  /api/project-infos/to-filled-list              [⬜预留·后端已验证]
     返回：当前用户可填报的项目列表（无分页，用于填报工时时展示选项）

GET  /api/project-infos/brief/{id}                  [⬜预留·后端已验证]
     返回：项目简要信息 Map（id, projectName, managerName 等核心字段）

GET  /api/project-infos/{id}/is-manager             [⬜预留·后端已验证]
     返回：Boolean，当前用户是否为该项目负责人

GET  /api/project-infos/statistics                  [⬜预留·后端已验证]
     Query: deptId.equals?（可选）
     返回：项目统计数据 Map

POST /api/project-infos    — 创建项目（需管理员权限）              [⬜预留·未探测]
PUT  /api/project-infos/{id}   — 更新项目（需管理员或项目负责人权限）  [⬜预留·未探测]
DELETE /api/project-infos/{id} — 删除项目（需管理员权限）          [⬜预留·未探测]
```

### AI 服务使用场景

```python
# 按项目名称搜索项目 ID（param_resolver.py 中使用）
GET /api/project-infos?projectName.contains=AI平台&page=0&size=10
→ 取 id 字段

# 获取可填报项目列表（设计预留：引导用户选择项目时使用，当前代码未接入）
GET /api/project-infos/to-filled-list   [⬜预留·后端已验证]
```

---

## 四、项目成员接口（`/api/project-members`）

> Controller：`ProjectMemberResource.java`
> **本节 agent 当前无任何调用点。** 两个 GET 已实测后端存在，写接口未探测。

```
GET  /api/project-members/by-project/{projectId}    [⬜预留·后端已验证]
     返回：指定项目的所有成员列表（探测返回 200，空项目返回 []）

POST /api/project-members/batch                     [⬜预留·未探测]
     Body: { projectId, memberIds: [id1, id2, ...] }
     说明：批量添加项目成员

GET  /api/project-members        — 获取成员列表（分页）  [⬜预留·后端已验证]
POST /api/project-members        — 添加项目成员          [⬜预留·未探测]
PUT  /api/project-members/{id}   — 更新项目成员          [⬜预留·未探测]
DELETE /api/project-members/{id} — 移除项目成员          [⬜预留·未探测]
```

---

## 五、认证接口

```
POST /api/authenticate                              [⬜预留/仅测试]
     Body: { username, password, rememberMe }
     返回: { id_token: "Bearer xxx" }
     说明：获取 JWT token。AI 服务【不直接调用】，token 由 Spring Boot 网关注入请求头；
          仅开发者手动测试时用它换 token（见 CLAUDE.md「获取 JWT Token」）
```

---

## 六、工作日历接口（`/api/work-calendars`）

> Controller：`WorkCalendarResource.java`
> 用途：判断某天是工作日还是非工作日，进而决定工时类别（`workhourType`）。

```
GET /api/work-calendars/list                        [✅实调]
    Query 参数（按日期范围查单天）：
      dateValue.greaterThanOrEqual = {date}T00:00:00Z   # 当天 00:00
      dateValue.lessThanOrEqual    = {date}T23:59:59Z   # 当天 23:59
      isDeleted.equals             = 0                  # 排除已删除
      page = 0
      size = 1
    返回：WorkCalendar 列表（可能是裸数组，或 { content: [...] } 分页对象）
```

### 核心字段（WorkCalendar）

| 字段 | 说明 |
|------|------|
| `dateValue` | 日历日期（ISO instant，如 `2026-06-30T00:00:00Z`） |
| `isWorkDay` | 是否工作日：`"1"`=工作日 / 其他值=非工作日 |
| `isDeleted` | 软删除标记，查询固定传 `0` |

### AI 服务使用场景

```
# 填报工时时判断工时类别（save_workhour.py / batch_save_workhour.py 中使用）
GET /api/work-calendars/list?dateValue.greaterThanOrEqual=2026-06-30T00:00:00Z
    &dateValue.lessThanOrEqual=2026-06-30T23:59:59Z&isDeleted.equals=0&page=0&size=1
→ 取首条 isWorkDay：== "1" → "正常工时"，否则 → "其他工时"
→ 查询失败时降级默认 "正常工时"（不阻断填报）
```

---

## 七、MCP 服务账号认证（`/api/auth/mcp-token`）

> Controller：SpringBoot 侧 MCP 认证端点
> 用途：让 MCP server / 网关用预共享密钥换取**绑定指定身份的 JWT**，无需预配长期 token。

```
POST /api/auth/mcp-token                             [✅实调]
     Body: { "entity_id": "钉钉userid", "api_key": "预共享密钥" }
     返回: { "token": "<JWT>", "userId": "...", "entityType": "<角色>", ... }
     错误: 空请求体 → 400；凭据错误 → 透传 SpringBoot 错误状态
```

### 响应字段（已实测 2026-05-18）

| 字段 | 说明 |
|------|------|
| `token` | 签发的 JWT（工具调用透传给 SpringBoot） |
| `userId` | 绑定身份的用户 ID |
| `entityType` | **角色字段**（键名是 `entityType`，不是 `role`/`userType`） |

### 调用链（两层代理，勿混淆路径）

```
MCP server/网关 (_service_account.py)
  → POST {AI_SERVICE_URL}/api/internal/auth/mcp-token   ← ai-service 内部代理（internal_auth.py）
      → POST {SPRINGBOOT_BASE_URL}/api/auth/mcp-token    ← 本端点，SpringBoot 真正签发
```

- `/api/internal/auth/mcp-token`：ai-service 暴露给 MCP 侧的透传代理
- `/api/auth/mcp-token`：SpringBoot 真正签发 JWT 的端点（即本节）

### AI 服务使用场景

```
# MCP Service Account 懒加载换 token（_service_account.ensure_auth()，进程级缓存）
# C1 网关模式：每请求由客户端 header 自声明 entity_id，网关据此换取绑定该 entity_id 的 JWT
```

> 详尽设计见 `docs/superpowers/specs/2026-05-19-mcp-gateway-c1-identity-design.md`、
> `docs/superpowers/specs/2026-05-18-shared-service-account-design.md`。

---

## 八、开发注意事项

### 用户 ID 的两种含义

| 场景 | 使用字段 | 说明 |
|------|----------|------|
| 查工时/填工时（`memberId`） | `sys_user.id` | 即 `SysUserDTO.id` |
| 权限系统中的用户标识 | `sys_user.entityId` | 与 `id` 不同 |

> AI 服务中 `user_id` / `X-User-ID` 请求头传递的是 `sys_user.id`（即工时表的 `memberId`）。

### 项目名称搜索

- 使用 `GET /api/project-infos?projectName.contains=xx` 模糊搜索
- 优先取 `projectName` 精确匹配的结果
- 返回的 `id` 字段即为工时填报所需的 `projectId`

### 工时日期格式

- 填报时传 `YYYY-MM-DD`（如 `2026-03-31`）
- 查询返回的 `workhourDate` 是 ISO instant（如 `2026-03-31T00:00:00Z`），取 `T` 前部分得到日期

---

## 附：后端存在性探测记录

- **探测日期**：2026-06-30
- **方法**：经 SSH 隧道（本地 → 172 → 116）直连生产 SpringBoot `localhost:9900`，用 employee 测试账号（罗欢，`159****0206`）的 JWT 逐个发 **只读 GET** 请求，据返回状态码判定端点是否存在。
- **判定口径**：`200` = 存在可用；`400/401/403/405` = 存在（参数/权限/方法问题）；据此确认而非靠猜。
- **结果**：14 个预留只读端点**全部确认后端存在**（13 个返回 200，`dept-members-stats` 返回 400「没有权限查看」= 存在但仅管理员可用）。
- **未探测**：所有写接口（POST/PUT/DELETE）为避免污染生产数据，**未做实测**，标记 `[⬜预留·未探测]`，其后端存在性以文档/Controller 命名为准，尚待 Java 源码或 Swagger 核校。
