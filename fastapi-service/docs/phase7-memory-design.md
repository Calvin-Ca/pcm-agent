# 第七阶段：Memory System 设计方案

## 1. 目标

让 AI 助手具备"记忆"能力：
- **短期记忆**：同一会话内记住上下文（"上周"指代、"再查一次"等指代消解）
- **长期记忆**：跨会话记住用户偏好（"我一般只看本周数据"、"我的 user_id 是 emp001"）

---

## 2. 整体架构

```
用户发消息
  │
  ▼
stream_agent_response()
  ├── 加载短期记忆（Redis: session:{session_id}）
  ├── 加载长期记忆（Redis: memories:{user_id}）
  ├── PromptBuilder 构建带历史的 messages 列表
  │
  ▼
LangGraph StateGraph（新增 conversation_history 字段）
  ├── classify_intent  ← 知道上下文，指代词可以被解析
  ├── execute_tool / execute_rag
  └── execute_llm  ← 传入完整 messages，支持多轮对话
  │
  ▼
响应生成后
  ├── 将本轮 (user + assistant) 保存到 Redis 会话历史
  └── 提取关键信息 → 保存到长期记忆（可选，重要度 > 阈值才存）
```

---

## 3. 短期记忆（Task 37）

### 3.1 数据模型

```python
class Message(BaseModel):
    role: str               # "user" | "assistant"
    content: str
    timestamp: str          # ISO 8601
    intent: Optional[str]   # 识别的意图（如 "tool_execution"）

class Session(BaseModel):
    session_id: str
    user_id: str
    messages: List[Message] = []
    created_at: str
    last_active: str
```

### 3.2 Redis 存储

| Key 格式                  | 类型   | 说明               |
|--------------------------|--------|--------------------|
| `session:{session_id}`   | String | JSON 序列化的 Session |

- TTL：30 分钟（每次访问自动续期）
- 保留最近 10 条消息（= 5 轮对话）

### 3.3 SessionMemoryService 接口

```python
async def get_conversation_history(session_id: str) -> List[Message]
async def add_messages(session_id: str, user_id: str, user_msg: str, assistant_msg: str, intent: str = None)
async def clear_session(session_id: str)
```

---

## 4. 长期记忆（Task 38）

### 4.1 数据模型

```python
class UserMemory(BaseModel):
    memory_id: str          # UUID
    user_id: str
    content: str            # 记忆内容（自然语言，如 "用户的 ID 是 emp001"）
    importance: float       # 0.0 ~ 1.0，由 LLM 评估或规则赋值
    access_count: int       # 被检索次数
    created_at: str
    last_accessed: str
```

### 4.2 Redis 存储

| Key 格式                  | 类型      | 说明                        |
|--------------------------|-----------|-----------------------------|
| `memory:{user_id}:{id}`  | Hash      | 单条记忆的所有字段           |
| `memories:{user_id}`     | Sorted Set| score = 综合得分（用于检索） |

### 4.3 时间衰减检索算法

```
score = importance × e^(-decay_rate × elapsed_days) + keyword_match_bonus
decay_rate = 0.1   # 7天后得分衰减至约 50%
```

检索流程：
1. 从 sorted set 取出用户全部记忆（按 score 倒序）
2. 用 BM25 对查询和记忆内容做关键词匹配
3. 乘以时间衰减系数得到最终 score
4. 返回 top-k 条记忆，更新 `last_accessed` 和 `access_count`

### 4.4 UserMemoryService 接口

```python
async def store_memory(user_id: str, content: str, importance: float = 0.5) -> str
async def retrieve_relevant_memories(user_id: str, query: str, top_k: int = 5) -> List[UserMemory]
async def list_memories(user_id: str) -> List[UserMemory]
async def clear_memories(user_id: str)
```

---

## 5. Prompt Builder（Task 39）

将短期 + 长期记忆注入 LLM 调用：

```python
async def build_messages_with_history(
    user_message: str,
    session_id: str,
    user_id: str,
    base_system_prompt: str,
) -> List[dict]  # OpenAI messages 格式
```

输出的 messages 结构：
```
[
  {"role": "system", "content": "...base prompt...\n\n用户记忆：\n- 用户倾向查询本周数据\n- ..."},
  {"role": "user",   "content": "上周的工时多少？"},      ← 历史第1轮
  {"role": "assistant", "content": "上周您的工时是..."},
  ...（最近 5 轮）
  {"role": "user",   "content": "这周呢？"},              ← 当前消息
]
```

---

## 6. LangGraph 集成（Task 40）

### 6.1 AgentState 新增字段

```python
class AgentState(TypedDict):
    # 现有字段...
    conversation_history: List[Dict[str, str]]  # OpenAI messages 格式（不含当前消息）
    user_memories: List[str]                    # 长期记忆摘要列表
```

### 6.2 node_execute_llm 变更

现在：调用 `llm_client.generate(prompt=message, system_prompt=...)`

变更后：调用 `llm_client.generate(messages=messages_with_history)` ← 带完整上下文

### 6.3 响应后保存记忆

在 `stream_agent_response()` 的 `finally` 块（或新增一个保存步骤）：
- 保存 user message 和 assistant response 到 `SessionMemoryService`
- 如果本轮有工具调用结果，尝试从中提取用户偏好信息存入长期记忆

---

## 7. 记忆管理 API（Task 41）

```
GET    /api/ai/memory?user_id={user_id}        查询用户长期记忆列表
DELETE /api/ai/memory?user_id={user_id}        清除用户长期记忆
```

用户从前端可以清空 AI 对自己的"印象"。

---

## 8. 实现优先级和文件变更清单

| 优先级 | 任务      | 新建文件                              | 修改文件                    |
|--------|-----------|---------------------------------------|-----------------------------|
| P0     | Task 37   | `app/services/session_memory.py`     | `app/main.py`               |
| P0     | Task 38   | `app/services/user_memory.py`        | —                           |
| P0     | Task 39   | `app/services/prompt_builder.py`     | —                           |
| P0     | Task 40   | —                                     | `app/services/langgraph_agent.py` |
| P0     | Task 41   | `app/api/memory.py`                  | `app/main.py`, `app/api/chat.py` |
| P1     | Task 37.3 | `tests/test_session_memory.py`       | —                           |
| P1     | Task 38.4 | `tests/test_user_memory.py`          | —                           |

---

## 9. 关键技术决策

1. **长期记忆不依赖 embeddings API**：使用 BM25 + 时间衰减，全部在本地计算，无需调用外部 API
2. **Redis 既存储短期也存储长期**：不引入额外依赖，Milvus 向量检索作为未来可选增强
3. **记忆提取不阻塞响应**：在 `finally` 块异步触发，失败不影响主流程
4. **前端 session_id 管理**：前端传入 session_id（已有 ChatRequest 字段），无 session_id 时按无状态处理

---

## 10. 待讨论点

1. **长期记忆如何触发提取？** 方案A：每轮对话都尝试提取（消耗 LLM token）；方案B：只在工具调用成功后提取（节省 token）
2. **记忆上限**：每用户最多保存多少条长期记忆？建议 50 条，超出时删除得分最低的
3. **session_id 由谁生成？** 前端生成或后端生成，前端如果传空则后端自动生成 UUID 并在响应中返回
