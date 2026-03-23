# 代码优化任务清单

基于 `app/` 目录全面扫描生成，按优先级排列。

## 高优先级（可靠性 & 安全性）

### [ ] 1. 数据库密码不应硬编码
- **文件：** `app/core/config.py`
- **问题：** `MYSQL_PASSWORD: str = "19990512"` 直接写在代码中
- **建议：** 改为必须从环境变量提供，去掉默认值或设为空字符串

### [ ] 2. 工具注册验证
- **文件：** `app/main.py`
- **问题：** 工具通过 import 副作用自动注册，失败时不会报错，工具列表可能不完整
- **建议：** 在 lifespan 中添加验证步骤，检查预期的工具是否全部注册成功

### [ ] 3. 工具调用重试机制
- **文件：** `app/services/task_executor.py`
- **问题：** 下游 SpringBoot 服务临时不可用时直接报错，无自动重试
- **建议：** 在 `execute_single_task()` 中实现指数退避重试（最多 3 次）

## 中优先级（可维护性）

### [ ] 4. 工具注册模式统一 — 提取基类
- **文件：** `app/tools/` 目录下所有工具
- **问题：** `query_timesheet.py`、`query_project.py` 等每个工具都重复定义 Params 模型 + Result 模型 + JSON Schema + 注册调用，~150 行重复结构
- **建议：** 创建 `app/tools/base.py` 提供通用工具基类，各工具只需定义参数和处理函数

### [ ] 5. 意图分类规则配置化
- **文件：** `app/services/intent_router.py`
- **问题：** 关键词、LLM 提示词、规则判断分散在 `__init__`、`_check_*` 方法、`_classify_with_llm` 等多处，修改时容易遗漏
- **建议：** 将规则提取到 `app/config/intent_rules.yaml`，`IntentRouter` 初始化时加载

### [ ] 6. LLM 调用统一封装
- **文件：** `app/services/intent_router.py`、`app/services/langgraph_agent.py`
- **问题：** 多处直接构建 LLM 调用，缺少统一的重试、超时、速率限制管理
- **建议：** 在 `LLMClient` 中添加 `call_with_json_response()` 等便捷方法

### [ ] 7. 会话日志参数封装
- **文件：** `app/services/conversation_logger.py`
- **问题：** `log_conversation()` 接收 15+ 个参数，调用时易出错
- **建议：** 用 Pydantic 模型 `ConversationLogData` 封装参数

### [ ] 8. 记忆服务存储抽象
- **文件：** `app/services/session_memory.py`、`app/services/user_memory.py`
- **问题：** 两个服务各自管理 Redis Key，迁移存储后端需改两个文件
- **建议：** 创建 `MemoryStore` 抽象接口，支持未来切换到 PostgreSQL 等

## 低优先级（完善性）

### [ ] 9. EmbeddingService 归档
- **文件：** `app/services/embedding_service.py`
- **问题：** 定义了 3 个 Embedding 实现但未被任何生产代码引用，当前 RAG 用的是 LangChain 的 `OpenAIEmbeddings`
- **建议：** 移至 `docs/deprecated/` 或删除

### [ ] 10. PlannerAgent 未真正使用
- **文件：** `app/models/task_plan.py`、`app/api/chat.py`
- **问题：** `PlannerAgent` 在启动时初始化，但 LangGraph 中 `complex_request` 直接走 `execute_llm`，未调用规划器
- **建议：** 方案 A — 实现 LangGraph 的 `plan_complex_request` 节点；方案 B — 暂时移除初始化，减少启动耗时

### [ ] 11. 上下文快照截断优化
- **文件：** `app/services/langgraph_agent.py` (第 494-501 行)
- **问题：** JSON > 8KB 时粗暴截断，可能丢失重要上下文
- **建议：** 实现渐进式压缩：先汇总旧历史消息，再裁剪记忆，最后才截断

### [ ] 12. 异常处理标准化
- **文件：** `app/services/session_memory.py` 等多处
- **问题：** 部分异常用空 `pass` 吞掉，没有日志记录
- **建议：** 统一改为 `logger.warning(...)` + 返回降级默认值

### [ ] 13. 添加单元测试
- **文件：** 整个 `app/` 目录
- **问题：** 关键服务（意图路由、任务执行、记忆管理）缺乏自动化测试
- **建议：** 创建 `tests/unit/` 和 `tests/integration/` 测试套件
