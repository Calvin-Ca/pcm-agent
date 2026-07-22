# FastAPI AI 服务启动链路笔记

本文整理我在阅读项目启动过程时重点追问过的问题，目标是把 `fastapi-service/main.py` 的 `lifespan` 初始化流程串起来。

## 1. 总入口

本服务入口是：

```text
fastapi-service/main.py
```

底部通过 uvicorn 启动：

```python
uvicorn.run(
    "main:app",
    host="0.0.0.0",
    port=8000,
    reload=False,
    workers=1,
)
```

FastAPI app 创建时绑定了生命周期函数：

```python
app = FastAPI(
    ...
    lifespan=lifespan,
)
```

所以初次启动时，uvicorn 加载 `main:app` 后，会触发：

```text
uvicorn startup
  -> FastAPI lifespan(app)
  -> 初始化数据库、工具、权限、LLM、Redis 记忆、SQL Agent、RAG
  -> yield
  -> 服务开始接收请求
```

## 2. 启动阶段完整顺序

当前 `lifespan` 里的主要顺序是：

```text
1. 初始化数据库表
2. 初始化 ToolRegistry
3. 导入并注册 app.tools
4. 校验预期工具是否齐全
5. 初始化 PermissionValidator
6. 初始化 LLMClient
7. 初始化 Redis、短期记忆、长期记忆、PromptBuilder
8. 初始化聊天组件和 LangGraph Agent
9. 如果开启 SQL_AGENT_ENABLED，初始化 SQL Engine
10. 初始化 LangChain RAG
11. 注册 Prometheus 服务信息指标
12. yield，服务进入运行状态
```

这个启动过程大量采用 fail-soft 设计：数据库、Redis、SQL Agent、RAG 某些组件失败时会记录 warning，但尽量不阻塞核心 AI 服务启动。

## 3. 数据库初始化

启动时这段代码负责准备数据库表：

```python
try:
    from app.services.database import get_db_service
    get_db_service().create_tables()
    logger.info("[OK] Database tables ready")
except Exception as db_err:
    logger.warning(f"[WARN]  数据库不可用，审计日志功能将降级: {db_err}")
```

调用链：

```text
lifespan()
  -> get_db_service()
    -> 第一次调用时创建 DatabaseService()
      -> create_engine(...)
      -> sessionmaker(...)
  -> create_tables()
    -> Base.metadata.create_all(bind=self.engine)
```

这里初始化的是 AI 服务自己的审计相关表，不是业务主表。

当前主要有两张表：

```text
conversation_logs  AI 对话/工具调用审计日志
ai_sessions        AI 会话汇总表
```

对应模型：

```text
fastapi-service/app/models/conversation.py
fastapi-service/app/models/ai_session.py
```

### Base = declarative_base()

`Base = declarative_base()` 是 SQLAlchemy ORM 的模型基类，也可以理解成“表模型登记簿”。

所有继承 `Base` 的类，都会在模块被 import 时注册到：

```python
Base.metadata
```

例如：

```python
class ConversationLog(Base):
    __tablename__ = "conversation_logs"
```

表示 `ConversationLog` 这个 Python 类映射到数据库里的 `conversation_logs` 表。

### 为什么要 import ai_session

`create_tables()` 里有这几行：

```python
from app.models.conversation import Base
import app.models.ai_session  # noqa: F401
Base.metadata.create_all(bind=self.engine)
```

`ConversationLog` 和 `Base` 在 `conversation.py` 里，导入 `Base` 时会加载 `conversation_logs` 模型。

但 `AiSession` 在另一个文件 `ai_session.py` 里。只有执行：

```python
import app.models.ai_session
```

Python 才会运行 `class AiSession(Base): ...`，SQLAlchemy 才知道还有 `ai_sessions` 这张表。

所以这行 import 不是为了使用变量，而是为了触发模型注册。

`# noqa: F401` 是告诉代码检查器：这个 import 看起来未使用，但这是有意为之。

## 4. Redis 记忆初始化

启动时这段代码初始化 Redis 和记忆系统：

```python
import redis.asyncio as aioredis
redis_client = aioredis.Redis(...)
await redis_client.ping()

initialize_session_memory(...)
initialize_user_memory(...)

initialize_prompt_builder(
    session_memory_service=get_session_memory(),
    user_memory_service=get_user_memory(),
)
```

作用链路：

```text
连接 Redis
  -> ping 验证可用
  -> 初始化 SessionMemoryService
  -> 初始化 UserMemoryService
  -> 初始化 PromptBuilder
  -> initialize_chat_components(..., prompt_builder=get_prompt_builder())
```

### 短期记忆

短期记忆实现位置：

```text
fastapi-service/app/services/session_memory.py
```

它用 Redis 保存当前 session 最近几轮对话：

```text
key: session:{session_id}
value: JSON，包含 user/assistant 消息列表
ttl: SESSION_EXPIRE_SECONDS，默认 1800 秒
```

保存时用 Redis `setex`，所以短期记忆是真正按时间过期。

同时还有条数限制：

```text
MAX_CONVERSATION_HISTORY = 10
```

所以短期记忆的遗忘机制是：

```text
30 分钟不访问 -> Redis 自动删除
消息超过上限 -> 只保留最近 N 条
```

### 长期记忆

长期记忆实现位置：

```text
fastapi-service/app/services/user_memory.py
```

它也用 Redis，但不是按 TTL 删除，而是按用户保存一些稳定偏好/身份/工作场景信息。

Redis key 结构：

```text
memory:{user_id}:{memory_id}   # Hash，保存单条记忆
memories:{user_id}             # Sorted Set，保存该用户所有 memory_id 和分数
```

单条记忆结构：

```text
memory_id
user_id
content
importance
access_count
created_at
last_accessed
```

长期记忆的“遗忘”不是直接删除，而是时间衰减：

```python
score = memory.importance * decay * (0.3 + 0.7 * bm25)
```

含义：

```text
最终分数 = 重要性 × 时间衰减 × 关键词匹配加权
```

分数越高，越容易被检索出来注入 prompt。

每个用户最多保留：

```text
MAX_MEMORIES_PER_USER = 50
```

超过上限时，删除 Sorted Set 里分数最低的一条。

所以长期记忆的机制是：

```text
不设置 TTL
越久不用分数越低
超过 50 条时淘汰低分记忆
```

### 为什么短期和长期都用 Redis

当前项目分工是：

```text
Redis:
  - 短期会话历史
  - 长期用户记忆
  - 服务于在线 prompt 构造

MySQL:
  - conversation_logs 审计日志
  - ai_sessions 会话汇总
  - 服务于后台查询、统计、审计
```

Redis 放在 prompt 热路径上，读写快、易降级、适合缓存型上下文。

MySQL 用来保存可审计、可统计的历史记录。

更严格的生产设计可以是：

```text
短期记忆：Redis
长期记忆：MySQL/PostgreSQL 作为主存储，Redis 做缓存
语义记忆：向量库
审计日志：MySQL
```

当前项目属于轻量实现：先让 Agent 具备上下文能力，同时保持主聊天流程可降级。

## 5. PromptBuilder 如何使用记忆

聊天时，LangGraph Agent 会调用：

```python
_prompt_builder.build_messages_with_history(...)
```

构造出来的 messages 大致是：

```python
[
    {"role": "system", "content": "系统提示词 + 长期用户记忆"},
    {"role": "user", "content": "上一轮用户问题"},
    {"role": "assistant", "content": "上一轮 AI 回答"},
    {"role": "user", "content": "当前问题"},
]
```

运行时读写链路：

```text
每次聊天开始
  -> 从长期记忆中检索相关内容
  -> 从短期记忆中取当前 session 历史
  -> 拼进 prompt

每次聊天结束
  -> 保存本轮 user/assistant 到短期记忆
  -> 如果工具调用成功，尝试按规则提取长期记忆
  -> 写 MySQL 审计日志和 ai_sessions 汇总
```

长期记忆提取不是每轮都存，而是在工具调用成功后用规则判断，例如：

```text
我的工号是 ...
我是 ... 部门
我负责 ... 项目
我一般 / 我习惯 / 我通常 ...
```

## 6. SQL Agent 初始化

启动时：

```python
if settings.SQL_AGENT_ENABLED:
    try:
        await sql_engine.initialize()
        logger.info("[OK] SQL Engine initialized")
    except Exception as sql_err:
        logger.warning(...)
```

配置开关：

```text
SQL_AGENT_ENABLED
```

SQL Engine 初始化位置：

```text
fastapi-service/app/services/sql_engine.py
```

初始化时主要创建异步 SQLAlchemy 连接池：

```python
create_async_engine(...)
```

数据库连接优先使用：

```text
SQL_AGENT_DB_*
```

如果没配，则复用：

```text
MYSQL_*
```

SQL Agent 用于处理普通 Function Calling 工具不方便表达的复杂分析，比如跨表 JOIN、反连接、复杂聚合等。

初始化失败时只影响 SQL Agent，不阻塞普通聊天、业务工具和 RAG。

## 7. LangChain RAG 初始化

启动时：

```python
_kb_path = _resolve_knowledge_base_path()
kb_result = await initialize_langchain_rag(kb_path=_kb_path)
```

RAG 实现位置：

```text
fastapi-service/app/services/langchain_rag.py
```

初始化链路：

```text
initialize_langchain_rag(kb_path)
  -> _load_documents_from_dir(kb_path)
  -> _split_documents(raw_docs)
  -> LangChainRAGService.initialize(chunks)
    -> 初始化 Embedding
    -> 初始化 LLM
    -> 初始化向量存储 Milvus，失败降级 FAISS
    -> 初始化 BM25 检索器
    -> 初始化 EnsembleRetriever
    -> 可选初始化 MultiQueryRetriever
    -> 可选初始化 CrossEncoderReranker
```

当前实际链路：

```text
用户问题
  -> EnsembleRetriever
    -> 向量检索，权重 0.6
    -> BM25 关键词检索，权重 0.4
  -> 取前 5 个 chunk
  -> RAG prompt
  -> ChatOpenAI / qwen-plus 生成答案
```

当前代码里：

```python
USE_MULTI_QUERY = False
USE_RERANKER = False
```

如果功能全开，查询链路会变成：

```text
用户问题
  -> MultiQueryRetriever
    -> LLM 改写出多个查询
    -> 每个查询进入 EnsembleRetriever
      -> Milvus 向量检索
      -> BM25 关键词检索
      -> 0.6 / 0.4 混合
  -> CrossEncoderReranker 精排
  -> 取 Top 5 chunk
  -> 拼 context
  -> LLM 生成答案
```

全开后召回和排序质量更好，但首 token 延迟会增加。

## 8. raw_docs 为空的问题

我本地调试时遇到：

```python
raw_docs = await asyncio.to_thread(_load_documents_from_dir, kb_path)
if not raw_docs:
    return {
        "success": False,
        "error": f"未在 {kb_path} 找到任何文档",
        ...
    }
```

直接原因是路径差异。

旧代码使用：

```python
_kb_path = os.path.join(os.path.dirname(__file__), "knowledge-base")
```

本地调试时实际查找：

```text
fastapi-service/knowledge-base
```

但仓库真实知识库在：

```text
knowledge-base
```

Docker 环境里没问题，是因为 compose 有挂载：

```yaml
./knowledge-base:/app/knowledge-base
```

容器内 `main.py` 位于 `/app/main.py`，所以查 `/app/knowledge-base` 正好存在。

本地源码调试时没有这个挂载，导致 `raw_docs` 为空。

现在已改为 `_resolve_knowledge_base_path()`：

```text
1. 优先使用 KB_PATH 环境变量
2. 再找 service_dir / "knowledge-base"        # Docker
3. 再找 service_dir.parent / "knowledge-base" # 本地源码
```

这样本地调试和 Docker 部署都能找到知识库。

## 9. 启动阶段的设计理解

整体设计可以总结为：

```text
核心聊天链路尽量启动成功
周边能力按需初始化
依赖不可用时降级，而不是直接让服务无法启动
```

不同组件的角色：

```text
DatabaseService:
  准备审计表，写 conversation_logs / ai_sessions

Redis:
  提供短期会话记忆和长期用户记忆

PromptBuilder:
  把记忆注入 LLM messages

ToolRegistry:
  管理业务工具注册

PermissionValidator:
  工具调用前做权限校验

LLMClient:
  主对话模型客户端

SQL Engine:
  SQL Agent 查询执行层

LangChain RAG:
  知识库问答检索和生成链路
```

启动完成日志：

```python
logger.info("🎉 AI Service startup completed successfully")
```

它表示核心初始化流程走完了，不代表每个降级组件都一定完全可用。判断具体能力是否可用，还要看前面的 `[OK]` / `[WARN]` 日志。
