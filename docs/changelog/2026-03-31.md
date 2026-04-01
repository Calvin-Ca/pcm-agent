# 变更记录 — 2026-03-31

## 核心改造：Function Calling 架构升级

### 背景

助手"不聪明"的根因：意图分类（qwen-flash）与参数提取（qwen-plus）是两步独立 LLM 调用，上下文割裂；system prompt 只有 3 行，LLM 不知道当前用户是谁、哪些参数必填。

### 改动文件

| 文件 | 改动内容 |
|------|---------|
| `fastapi-service/app/services/llm_client.py` | 新增 `generate_with_tools()` 方法，支持 OpenAI Function Calling 格式 |
| `fastapi-service/app/prompts/system.yaml` | 替换为带用户身份变量（user_id/user_name/entity_type/department_id/today/week_start 等）和 6 条行为规则的模板 |
| `fastapi-service/app/services/langgraph_agent.py` | 新增 `_build_openai_tools()` + `node_llm_with_tools()` 节点；图入口从 `classify_intent` 改为 `llm_with_tools`；`node_execute_llm` 增加短路透传逻辑；`stream_agent_response` 注入动态 system prompt |
| `fastapi-service/main.py` | 新建（从 `app/main.py` 移出），顶部用 dotenv 加载 `.env`+`.env.local`，knowledge-base 路径改为绝对路径 |
| `fastapi-service/app/core/config.py` | 新增 `SPRINGBOOT_BASE_URL` 字段；env_file 改为 `(".env", ".env.local")` 双文件加载；`extra = "ignore"` |
| `.env.local` | 新建，本地开发覆盖（REDIS_HOST/MILVUS_HOST/SPRINGBOOT_BASE_URL/MYSQL_HOST=localhost） |
| `.gitignore` | 新建，忽略 `.env`、`.env.local`、`__pycache__`、`.venv` 等 |

### 新架构流程

```
改造前（两步分离）：
  用户消息 → qwen-flash 意图分类 → qwen-plus 参数提取 → 注入 user_id → 执行工具

改造后（一步到位）：
  用户消息 + 用户身份 + 工具schema → qwen-plus with tools
    ├─ tool_calls → 参数已完整 → 执行工具
    └─ stop → 文字回复（追问/闲聊/知识问答）
```

### LangGraph 图结构

```
START → llm_with_tools（Function Calling 主节点）
           ├─ tool_execution → execute_tool → END
           ├─ knowledge_qa  → execute_rag  → END
           ├─ general_chat  → execute_llm  → END（llm_result 预填，不重复调 LLM）
           └─ clarify       → clarify_node → END

降级路径：llm_with_tools 失败时直接调用 node_classify_intent()（旧规则路由）
```

### 环境配置说明

本地开发在 `.env.local` 里覆盖差异值，Docker 环境不需要此文件：

```
# .env.local（本地开发，不提交）
REDIS_HOST=localhost
MILVUS_HOST=localhost
SPRINGBOOT_BASE_URL=http://localhost:8080
MYSQL_HOST=localhost
```

### 已知问题（待下一步修复）

- `query_timesheet.py`：参数中无 `member_name` 时，`user_id` 注入逻辑依赖 Function Calling 的 system prompt 规则，需验证查询默认归属是否正确
- `save_workhour.py`：项目名→项目ID 转换层未实现，需配合 `param_resolver.py` 统一处理
- RAG 知识库：Milvus 本地未启动时降级为空，需要 Docker 环境才能完整测试
