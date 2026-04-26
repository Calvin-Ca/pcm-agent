# SpringBoot 后端 API 参考（AI 服务开发用）

> 路径前缀：`http://localhost:8080`（本地开发）/ `http://host.docker.internal:8080`（Docker 内）
> 所有接口均需携带 `Authorization: Bearer <token>` 请求头

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
GET  /thsuaa/api/sys-users
     Query: entityName.contains=姓名, entityType.equals=xx, orgId.equals=xx, deptId.equals=xx
            page=0, size=10
     返回：SysUserDTO 列表（分页，Header 含 X-Total-Count）

GET  /thsuaa/api/sys-users/{id}
     返回：单个 SysUserDTO

GET  /thsuaa/api/sys-users-manager
     Query: 同上（含项目负责人视角的权限过滤）

GET  /thsuaa/api/all-sys-users
     返回：所有用户列表（无分页，慎用）

GET  /thsuaa/api/sys-users/hasUserName/{username}
     返回：Boolean，账号是否存在

POST /thsuaa/api/sys-users              — 创建用户（需管理员权限）
PUT  /thsuaa/api/sys-users              — 更新用户（需管理员权限）
PUT  /thsuaa/api/sys-users/self         — 当前用户更新自己
DELETE /thsuaa/api/sys-users/{id}       — 软删除（需管理员权限）
DELETE /thsuaa/api/sys-users/real/{id}  — 硬删除（需管理员权限）
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
POST /api/workhour
     Body: { projectId, workhourDate, workhour, workContent?, workType, workhourType, memberId? }
     说明：填报工时。无 memberId 时填报当前登录用户
     注意：JSON 字段 workContent（驼峰），DB 列 work_content（蛇形），不是 description

GET  /api/workhour/by-date-range
     Query: startDate=YYYY-MM-DD, endDate=YYYY-MM-DD, memberId?, projectId?, orgId?
     返回：工时记录数组（直接返回列表，非分页对象）
     ⚠️ 不传 memberId 时返回全员数据（非当前用户），需显式传入

GET  /api/workhour/last-record
     Query: memberId
     返回：用户最后一条工时记录

GET  /api/workhour/total-workhour
     Query: startDate, endDate
     返回：工时合计统计

GET  /api/workhour/user-stats
     Query: startDate, endDate
     返回：所有用户工时统计

GET  /api/workhour/dept-members-stats
     Query: startDate, endDate, orgId（管理员权限）
     返回：部门成员工时统计

POST /api/workhour/batch-approve
     Body: List<String> workhourIds
     说明：批量审核工时

GET  /api/workhour/export/project-simple
     Query: startDate, endDate, title?, orgId（管理员权限）
     返回：Excel 文件（工时汇总表）

PUT  /api/workhour/{id}    — 更新工时记录
DELETE /api/workhour/{id}  — 删除工时记录
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
GET  /api/project-infos
     Query（JHipster Criteria 语法）:
       projectName.contains=名称关键词
       projectName.equals=精确名称
       id.equals=项目ID
       id.in=id1,id2,id3
       managerId.equals=负责人ID
       page=0, size=10
     返回：ProjectInfoDTO 列表（Header 含 X-Total-Count）

GET  /api/project-infos/{id}
     返回：单个 ProjectInfoDTO

GET  /api/project-infos/to-filled-list
     返回：当前用户可填报的项目列表（无分页，用于填报工时时展示选项）

GET  /api/project-infos/brief/{id}
     返回：项目简要信息 Map（id, projectName, managerName 等核心字段）

GET  /api/project-infos/{id}/is-manager
     返回：Boolean，当前用户是否为该项目负责人

GET  /api/project-infos/statistics
     Query: deptId.equals?（可选）
     返回：项目统计数据 Map

POST /api/project-infos    — 创建项目（需管理员权限）
PUT  /api/project-infos/{id}   — 更新项目（需管理员或项目负责人权限）
DELETE /api/project-infos/{id} — 删除项目（需管理员权限）
```

### AI 服务使用场景

```python
# 按项目名称搜索项目 ID（param_resolver.py 中使用）
GET /api/project-infos?projectName.contains=AI平台&page=0&size=10
→ 取 id 字段

# 获取可填报项目列表（fill_workhour 引导用户选择时使用）
GET /api/project-infos/to-filled-list
```

---

## 四、项目成员接口（`/api/project-members`）

> Controller：`ProjectMemberResource.java`

```
GET  /api/project-members/by-project/{projectId}
     返回：指定项目的所有成员列表

POST /api/project-members/batch
     Body: { projectId, memberIds: [id1, id2, ...] }
     说明：批量添加项目成员

GET  /api/project-members        — 获取成员列表（分页）
POST /api/project-members        — 添加项目成员
PUT  /api/project-members/{id}   — 更新项目成员
DELETE /api/project-members/{id} — 移除项目成员
```

---

## 五、认证接口

```
POST /api/authenticate
     Body: { username, password, rememberMe }
     返回: { id_token: "Bearer xxx" }
     说明：获取 JWT token（AI 服务不直接调用，token 由 Spring Boot 网关注入请求头）
```

---

## 六、开发注意事项

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
