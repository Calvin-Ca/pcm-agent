# 项目背景说明

## 项目名称
工时管理系统 (Workhour Management System)

## 项目是做什么的
这是一个企业级工时管理系统，主要用于：
- **项目管理**：管理项目信息、合同、分包合同等
- **工时填报**：员工录入和提交工时记录
- **考勤管理**：集成钉钉考勤数据，统计打卡记录、工作时长、加班时长
- **成员管理**：管理项目成员、部门人员
- **统计分析**：工时统计、考勤报表、数据可视化
- **审批流程**：工时审批、多级审核
- **AI智能助手**：提供AI问答、知识库检索、智能分析功能

## 技术栈

### 后端 (springboot3/)
| 技术 | 版本 | 用途 |
|------|------|------|
| Spring Boot | 3.5.8 | 核心框架 |
| Java | 17 | 编程语言 |
| JHipster | 8.11.0 | 代码生成、脚手架 |
| MySQL | 8.0 | 关系数据库 |
| Spring Security | - | 认证授权 (OAuth2) |
| Spring Data JPA | - | ORM 数据访问 |
| Liquibase | - | 数据库版本管理 |
| MapStruct | 1.6.3 | DTO/实体映射 |
| Maven | 3.2.5+ | 构建工具 |
| OpenAPI | - | API 文档 |

### 前端 (web/)
| 技术 | 版本 | 用途 |
|------|------|------|
| Vue | 3.2 | 前端框架 |
| TypeScript | 4.6 | 类型系统 |
| Ant Design Vue | 3.1 | UI 组件库 |
| Vite | 2.9 | 构建工具 (开发) |
| Vue CLI | 5.0 | 构建工具 (生产) |
| Vuex | 4.0 | 状态管理 |
| Vue Router | 4.0 | 路由管理 |
| Axios | 0.26 | HTTP 客户端 |
| ECharts | 5.6 | 数据可视化 |
| Dayjs | 1.11 | 日期处理 |

### AI 服务 (ai_service/)
| 技术 | 版本 | 用途 |
|------|------|------|
| FastAPI | 0.110+ | Python Web 框架 |
| Python | 3.9+ | 编程语言 |
| Milvus | 2.3.3 | 向量数据库 (知识库) |
| Redis | 7 | 缓存、会话 |
| OpenAI SDK | 1.30+ | LLM 调用 |
| LangChain | 0.2+ | AI 应用框架 |
| Prometheus | 2.48 | 监控指标 |
| Grafana | 10.2 | 监控可视化 |
| MySQL | 8.0 | 数据存储 |

## 代码规范

### Java 后端规范
- **代码检查**: Checkstyle (nohttp 规则)
- **格式化**: Prettier + prettier-plugin-java
- **编码**: UTF-8
- **禁止**: 代码中不允许使用 http:// (使用 https://)

### 前端规范
- **ESLint**: Vue 3 + TypeScript 推荐规则
- **Prettier**: 代码格式化
- **Stylelint**: CSS/Less 样式检查
- **Git Hooks**: Husky + lint-staged (提交前自动检查)

#### ESLint 关键规则
- 使用单引号
- 必须分号结尾
- 多行结尾逗号
- 组件名使用 kebab-case
- 允许 `any` 类型
- 允许 `console` (开发环境)

#### TypeScript 配置
- 严格空检查: 关闭 (`strictNullChecks: false`)
- 未使用参数/变量检查: 开启
- 模块解析: Node
- 路径别名: `@/` 指向 `src/`

### Git 提交规范
- 使用 Husky 预提交钩子
- 提交前自动运行 lint 检查
- 提交前自动格式化代码

## 项目目录结构

```
项目根目录/
├── springboot3/          # Spring Boot 后端
│   ├── src/main/java/    # Java 源代码
│   ├── src/main/resources/  # 配置文件
│   ├── .jhipster/        # JHipster 实体配置
│   └── pom.xml           # Maven 配置
├── web/                  # Vue 前端
│   ├── src/              # 源代码
│   ├── pages/            # 多页面入口
│   └── vite.config.ts    # Vite 配置
└── ai_service/           # FastAPI AI 服务
    ├── fastapi-service/  # Python 服务代码
    ├── docker-compose.yml # 依赖服务编排
    └── prompts/          # AI Prompt 模板
```

## 开发环境启动

### 后端
```bash
cd springboot3
./mvnw
```

### 前端
```bash
cd web
yarn vite:dev
```

### AI 服务
```bash
cd ai_service
docker-compose up -d
```

## 关键实体
- WorkhourAttendance (考勤统计)
- DingAttendance (钉钉考勤原始数据)
- ProjectMember (项目成员)

---

## 当前重点工作：AI智能助手功能

正在开发 AI 智能助手模块，为工时管理系统添加自然语言交互能力。

### 功能范围
- **工时查询**：自然语言查询工时记录和统计
- **知识库问答**：基于企业制度文档的 RAG 问答
- **周报生成**：自动汇总工时生成周报
- **工时填报**：通过对话填报工时
- **权限控制**：严格遵循现有权限体系

### 项目文档位置
| 文档 | 路径 |
|------|------|
| 需求文档 | `ai_service/.kiro/specs/ai-assistant/requirements.md` |
| 技术设计 | `ai_service/.kiro/specs/ai-assistant/design.md` |
| 任务清单 | `ai_service/.kiro/specs/ai-assistant/tasks.md` |

### 开发阶段（共13个阶段，65个任务）
- **P0 - MVP 阶段**：基础设施、AI服务核心、网关层、前端组件、基础RAG、集成测试
- **P1 - 完整版本**：Memory System、周报/填报、Enterprise RAG、可观测性、Prompt管理
- **P2 - 可选功能**：风险评估、多语言支持等

### 当前进展
- [x] 第一阶段：基础设施搭建（Docker Compose环境）
- [x] 第二阶段：AI服务层核心功能（Tool Registry、基础工具、Intent Router、Planner Agent等）
- [ ] 第三阶段：SpringBoot网关层（进行中）
- [ ] 第四阶段：前端聊天组件
- [ ] 第五阶段：基础RAG功能

### 关键组件
| 组件 | 说明 |
|------|------|
| Tool Registry | 动态工具注册中心 |
| Intent Router | 意图识别与路由 |
| Planner Agent | 任务规划Agent |
| Task Executor | 任务执行器 |
| RAG Engine | 知识库检索引擎 |
| Memory System | 短期会话记忆 + 长期用户记忆 |
| Permission Validator | 权限验证器 |
