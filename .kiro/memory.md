# AI 服务开发记录

## 2026-03-20 开发进度

### 完成的工作

1. **Docker 环境配置**
   - 配置了 docker-compose.yml，包含 MySQL、Redis、Milvus、Prometheus、Grafana 等服务
   - 修改了 Dockerfile 启动命令为 `python main.py`
   - 配置了环境变量，支持多 LLM 配置（意图识别、主对话、任务规划）

2. **本地开发环境**
   - 创建了 conda 环境 `workhour`
   - 配置了 PyCharm 调试环境
   - 创建了 `.env.local` 用于本地开发
   - 安装了必要的依赖（cryptography、pymysql 等）

3. **日志系统**
   - 配置了 JSON 格式日志输出
   - 实现了按天轮转的日志文件（保留 30 天）
   - 日志保存位置：`fastapi-service/logs/app.log`
   - 修复了日志重复输出问题

4. **数据库连接**
   - 实现了数据库连接测试接口 `/api/db/test`
   - 创建了数据库服务类 `DatabaseService`
   - 配置了连接池和会话管理

5. **会话记录功能**
   - 设计并创建了 `conversation_logs` 表
   - 实现了会话日志记录服务 `ConversationLogger`
   - 在流式和非流式聊天接口中集成了日志记录
   - 记录内容包括：
     - 用户消息、路由类型、意图识别
     - 工具调用、任务规划
     - 响应时间、token 消耗
     - 状态和错误信息

### 关键文件

- `app/models/conversation.py` - 会话记录数据模型
- `app/services/database.py` - 数据库服务
- `app/services/conversation_logger.py` - 会话日志记录器
- `app/core/logging_config.py` - 日志配置
- `app/api/db_test.py` - 数据库测试接口
- `app/api/init_db.py` - 数据库初始化接口
- `app/api/conversation_query.py` - 会话记录查询接口

### 配置说明

**环境变量配置：**
- 意图识别 LLM：qwen-flash（轻量快速）
- 主对话 LLM：qwen-plus（能力更强）
- MySQL：localhost:3306/workhour
- Redis：localhost:6379
- Milvus：localhost:19530

**启动方式：**
- Docker：`docker-compose up -d`
- 本地：运行 `main.py`（PyCharm 调试）

### 下一步计划

- 完善工具调用的日志记录（记录具体调用的工具和参数）
- 实现任务规划的详细记录
- 添加 token 消耗统计
- 考虑添加会话数据分析接口
