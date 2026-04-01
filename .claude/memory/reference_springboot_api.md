---
name: SpringBoot 后端 API 参考
description: 工时/项目/用户接口路径、字段名、查询参数，AI 服务开发必查
type: reference
---

完整文档位于：`docs/springboot-api-reference.md`

## 关键速查

### 用户查询（param_resolver 使用）
- `GET /thsuaa/api/sys-users?entityName.contains=张三&page=0&size=10`
- 返回字段：`id`（=memberId）、`entityName`（姓名）、`entityType`、`deptId`

### 项目查询（param_resolver 使用）
- `GET /api/project-infos?projectName.contains=AI平台&page=0&size=10`
- 返回字段：`id`（=projectId）、`projectName`
- ⚠️ 不存在 `/api/project/search` 端点

### 工时填报
- `POST /api/workhour`
- Body: `{ projectId, workhourDate, workhour, description?, memberId? }`

### 工时查询
- `GET /api/workhour/by-date-range?startDate=xx&endDate=xx&memberId=xx`
- ⚠️ 不传 memberId 返回全员数据，必须显式传入当前用户 ID

### 可填报项目列表
- `GET /api/project-infos/to-filled-list`（无分页，返回当前用户可填项目）

## Why
2026-04-01 扫描 SpringBoot Controller 源码后整理，用于支撑 param_resolver.py 等 AI 工具层开发。
