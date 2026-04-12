# SQL Agent 实施任务清单

> 设计文档：`docs/design/sql-agent-design.md`
> 预估总工作量：2-3 天
> 执行方式：Claude Code Team Mode

---

## 任务依赖关系

```
Task 1（配置层）──┐
                  ├── Task 3（工具注册 + Handler）── Task 5（集成联调）── Task 7（回归测试）
Task 2（SQL引擎）─┘                                       │
                                                          │
Task 4（Prompt 模板）──────────────────────────────────────┘
                                                          │
Task 6（安全校验）────────────────────────────────────────┘
```

可并行的任务组：
- **第一组（并行）**：Task 1 + Task 2 + Task 4（无互相依赖）
- **第二组（并行）**：Task 3 + Task 6（依赖第一组完成）
- **第三组（串行）**：Task 5 → Task 7

---

## Task 1：配置层扩展

**文件**：
- `fastapi-service/app/core/config.py`
- `.env.example`

**内容**：

1. 在 `config.py` 的 `Settings` 类中新增以下配置项：

```python
# SQL Agent 数据库配置（独立连接，为空时复用 MYSQL_* 配置）
SQL_AGENT_DB_HOST: str = ""
SQL_AGENT_DB_PORT: int = 0
SQL_AGENT_DB_NAME: str = ""
SQL_AGENT_DB_USER: str = ""
SQL_AGENT_DB_PASSWORD: str = ""

# SQL Agent LLM 配置（独立于 CHAT_LLM，为空时复用 CHAT_LLM_* 配置）
SQL_AGENT_LLM_API_KEY: str = ""
SQL_AGENT_LLM_API_BASE: str = ""
SQL_AGENT_LLM_MODEL: str = ""

# SQL Agent 功能开关与安全配置
SQL_AGENT_ENABLED: bool = True
SQL_AGENT_MAX_ROWS: int = 500
SQL_AGENT_QUERY_TIMEOUT: int = 30
```

2. 在 `.env.example` 中新增对应配置说明：

```bash
# ─── SQL Agent 配置 ───────────────────────────────────────
# 数据库连接（为空则复用上方 MYSQL_* 配置）
# 推荐使用只读账号，见 docs/design/sql-agent-design.md 第四节
# SQL_AGENT_DB_HOST=
# SQL_AGENT_DB_USER=ai_readonly
# SQL_AGENT_DB_PASSWORD=

# LLM 模型（为空则复用 CHAT_LLM_* 配置，即 vLLM qwen3-8b）
# 如 SQL 生成质量不够，可切换为更强模型：
# SQL_AGENT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
# SQL_AGENT_LLM_MODEL=qwen-plus
# SQL_AGENT_LLM_API_KEY=（同 DASHSCOPE_API_KEY 即可）

# 安全配置
# SQL_AGENT_ENABLED=true
# SQL_AGENT_MAX_ROWS=500
# SQL_AGENT_QUERY_TIMEOUT=30
```

**验证**：`from app.core.config import settings; print(settings.SQL_AGENT_ENABLED)` 正常输出。

---

## Task 2：SQL Engine（数据库连接与执行层）

**新建文件**：`fastapi-service/app/services/sql_engine.py`

**职责**：
1. SQLAlchemy 异步连接池管理
2. 表结构自动获取与缓存（过滤敏感列）
3. SQL 查询执行（带超时和行数限制）

**核心实现**：

```python
class SQLEngine:
    async def initialize(self):
        """
        构建连接 URL（优先 SQL_AGENT_DB_*，回退 MYSQL_*），
        创建 AsyncEngine，连接池 pool_size=5。
        依赖：aiomysql, sqlalchemy[asyncio]
        """

    async def get_table_schemas(self) -> str:
        """
        对 ALLOWED_TABLES 中的每张表执行 SHOW CREATE TABLE，
        过滤 BLOCKED_COLUMNS 中的敏感列，
        格式化为 LLM 可读的 DDL 文本。
        进程级缓存，TTL 1 小时。
        """

    async def execute_query(self, sql: str, timeout: int, max_rows: int) -> tuple:
        """
        执行 SQL（text()），返回 (rows: list[dict], columns: list[str])。
        - 自动追加 LIMIT（如原 SQL 无 LIMIT）
        - asyncio.wait_for 超时控制
        """

    async def close(self):
        """关闭连接池，在应用 shutdown 时调用"""

sql_engine = SQLEngine()  # 全局单例
```

**白名单与黑名单配置**（写在文件顶部常量）：

```python
ALLOWED_TABLES = {
    "workhour": "工时记录表",
    "project_info": "项目信息表",
    "sys_user": "用户/员工表",
    "workhour_attendence": "考勤记录表",
    "org_dept": "部门/组织表",
    "work_calendar": "工作日历表",
    "project_member": "项目成员关联表",
}

BLOCKED_COLUMNS = {
    "sys_user": ["password", "password_hash", "salt", "id_card", "phone", "email"],
}
```

**依赖**：在 `requirements.txt` 中新增：
```
sqlalchemy[asyncio]>=2.0
aiomysql>=0.2.0
```

**验证**：写一个简单测试脚本，连接数据库并执行 `SELECT 1`，确认连接正常。

---

## Task 3：sql_query 工具注册 + Handler

**新建文件**：`fastapi-service/app/tools/sql_query.py`

**职责**：
1. 定义 JSON Schema
2. 注册到 tool_registry
3. 实现 sql_query_handler

**JSON Schema**：

```python
SQL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "用户的数据分析问题（自然语言）",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}
```

**工具注册**：

```python
tool_registry.register_tool(
    name="sql_query",
    description=(
        "数据分析查询工具（适用：复杂统计分析、排名对比、趋势变化、缺勤检测等）。"
        "将用户的自然语言问题转换为 SQL 查询并返回结果。"
        "不适用于简单的个人工时查询或工时填报。"
    ),
    json_schema=SQL_QUERY_SCHEMA,
    handler=sql_query_handler,
    category=ToolCategory.STATISTICS,
    timeout=60,
    requires_permission=True,
)
```

**Handler 流程**（参考设计文档第九节）：

```
1. 提取 question + 权限上下文（user_id, entity_type, department_id）
2. 检查 SQL_AGENT_ENABLED 开关
3. 构建 PermissionContext → get_data_filter → 生成权限约束文本
4. 获取 table_schemas（调 sql_engine.get_table_schemas）
5. 渲染 sql_generation prompt → 调 LLM 生成 SQL
6. 调 validate_sql() 安全校验（Task 6 实现）
7. 调 sql_engine.execute_query() 执行
8. 渲染 sql_summarize prompt → 调 LLM 汇总结果
9. 返回 {success, answer, sql, row_count}
```

**LLM 客户端**：需创建独立 LLM 客户端实例，配置回退逻辑：

```python
def _get_sql_llm_config():
    """SQL Agent LLM 配置，为空时回退到 CHAT_LLM"""
    return {
        "api_key": settings.SQL_AGENT_LLM_API_KEY or settings.CHAT_LLM_API_KEY,
        "api_base": settings.SQL_AGENT_LLM_API_BASE or settings.CHAT_LLM_API_BASE,
        "model": settings.SQL_AGENT_LLM_MODEL or settings.CHAT_LLM_MODEL,
    }
```

**错误重试**：SQL 执行失败时，将错误信息反馈给 LLM 重新生成 SQL，最多重试 1 次。

**依赖**：Task 1（配置）、Task 2（sql_engine）、Task 4（Prompt）、Task 6（校验）

---

## Task 4：Prompt 模板

**新建文件**：`fastapi-service/app/prompts/sql_agent.yaml`

**包含两个 Prompt**：

### 4.1 sql_generation（SQL 生成）

System prompt 需包含：
- 角色定义（MySQL 查询助手）
- `{table_schemas}` 占位符（表结构）
- `{permission_constraints}` 占位符（权限约束）
- 规则列表（SELECT only、LIMIT、日期处理、JOIN 关系等）
- 常用表关联关系：
  - `workhour.member_id = sys_user.id`
  - `workhour.project_id = project_info.id`
  - `sys_user.dept_id = org_dept.id`
  - `project_member.project_id = project_info.id`
  - `project_member.member_id = sys_user.id`
- 输出要求：只输出纯 SQL，不加解释

User prompt：`{user_question}`

完整内容见设计文档第七节。

### 4.2 sql_summarize（结果汇总）

System prompt：
- 角色定义（数据分析助手）
- 规则（直接回答、关键数字加粗、表格展示、空结果提示）

User prompt：
- `{user_question}`
- `{row_count}`
- `{query_results}`

### 4.3 修改 `system.yaml`

在现有 system prompt 末尾新增 sql_query 工具的触发规则：

```yaml
sql_query_guidance: |
  当用户提出以下类型的分析问题时，使用 sql_query 工具：
  - 跨表关联查询（如"各部门工时对比"、"项目成员工时排名"）
  - 排序/排名/TOP N（如"工时最多的前10人"、"按工时排序"）
  - 趋势分析（如"近三个月工时变化"）
  - 缺勤/异常检测（如"谁还没填工时"、"工时异常的人"）
  - 现有工具无法覆盖的复杂统计

  以下场景不要使用 sql_query，用现有工具：
  - 简单查询自己的工时 → query_timesheet
  - 查项目信息 → query_project
  - 填报工时 → save_workhour
  - 基本统计（本月工时汇总） → compute_statistics
```

**验证**：`PromptManager` 能正常加载 `sql_agent.yaml` 并渲染占位符。

---

## Task 5：集成联调

**前提**：Task 1-4 和 Task 6 全部完成。

**步骤**：

### 5.1 应用启动集成

在 `fastapi-service/app/main.py`（或 `lifespan` 函数）中：

```python
# 应用启动时初始化 SQL Engine
from app.services.sql_engine import sql_engine

async def startup():
    if settings.SQL_AGENT_ENABLED:
        await sql_engine.initialize()

async def shutdown():
    await sql_engine.close()
```

确认 sql_query 工具通过 `tool_registry` 自动注册（模块导入时自动执行）。

### 5.2 端到端测试（手动）

启动服务后，通过 API 发送以下测试请求：

```bash
# 简单查询
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-User-ID: test_user" \
  -H "X-Entity-Type: superAdmin" \
  -d '{"message": "统计本月各项目的工时总量，按工时从高到低排序"}'

# 权限测试（普通员工，应只返回自己的数据）
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "X-User-ID: 123" \
  -H "X-Entity-Type: employee" \
  -d '{"message": "我上个月在哪些项目上花了时间"}'

# 应走现有工具（不应触发 sql_query）
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "X-User-ID: 123" \
  -H "X-Entity-Type: employee" \
  -d '{"message": "查一下我本周的工时"}'
```

### 5.3 验证清单

- [ ] sql_query 工具在 `tool_registry.list_tools()` 中可见
- [ ] LLM 对复杂分析问题选择 sql_query 工具
- [ ] LLM 对简单查询仍选择 query_timesheet 等现有工具
- [ ] SQL 生成正确（JOIN 关系、WHERE 条件）
- [ ] 权限约束生效（employee 只能查自己）
- [ ] 安全校验拦截非 SELECT 语句
- [ ] 结果以自然语言汇总返回
- [ ] SQL 执行错误时重试 1 次后返回友好提示

---

## Task 6：SQL 安全校验

**文件**：`fastapi-service/app/tools/sql_query.py`（放在同一文件中）

**实现 `validate_sql()` 函数**：

```python
import sqlparse

def validate_sql(sql: str) -> tuple[bool, str]:
    """
    校验 LLM 生成的 SQL 是否安全。
    返回 (is_safe, error_message)。
    """
```

**校验规则**：

1. **语句类型**：只允许 SELECT（用 `sqlparse.parse` 解析 statement type）
2. **多语句检测**：禁止分号分隔的多条 SQL
3. **表白名单**：提取 FROM/JOIN 后的表名，必须在 `ALLOWED_TABLES` 中
4. **列黑名单**：检测 SQL 中是否包含 `BLOCKED_COLUMNS` 中的列名
5. **危险关键字**：禁止 `INTO OUTFILE`、`LOAD_FILE`、`INTO DUMPFILE`、`BENCHMARK`、`SLEEP`
6. **LIMIT 强制**：如无 LIMIT，自动追加 `LIMIT {SQL_AGENT_MAX_ROWS}`

**依赖**：在 `requirements.txt` 中新增：
```
sqlparse>=0.5.0
```

**单元测试**（写在 `tests/test_sql_validation.py`）：

```python
# 应通过
assert validate_sql("SELECT * FROM workhour LIMIT 10")[0] == True

# 应拦截
assert validate_sql("DELETE FROM workhour")[0] == False
assert validate_sql("SELECT * FROM secret_table")[0] == False
assert validate_sql("SELECT password FROM sys_user")[0] == False
assert validate_sql("SELECT * FROM workhour; DROP TABLE workhour")[0] == False
assert validate_sql("SELECT * FROM workhour INTO OUTFILE '/tmp/x'")[0] == False
```

---

## Task 7：回归测试与文档更新

**前提**：Task 5 联调通过。

### 7.1 精度回归测试

运行现有 2000 条精度测试，确认 sql_query 工具注册后不影响现有 87% 通过率：

```bash
cd fastapi-service
pytest tests/test_classification_accuracy.py -n 8 --timeout=120 -q
```

**通过标准**：通过率 ≥ 85%（允许 ±2% 波动，因为 LLM 有随机性）

如果精度下降超过 2%，排查 sql_query 是否抢了现有工具的流量（system.yaml 中的触发规则需调整）。

### 7.2 文档更新

1. 更新 `docs/roadmap.md`：
   - L3 剩余项标记 SQL Agent 为 ✅
   - 记录完成日期

2. 新建 `docs/changelog/2026-04-xx.md`（用实际日期）：
   - SQL Agent 功能说明
   - 配置指引
   - 使用示例

3. 更新 `CLAUDE.md`：
   - 在核心服务文件表中新增 `sql_engine.py`
   - 在工具层列表中新增 `sql_query.py`

---

## 执行建议（Team Mode）

### 并行分组

```
Teammate 1: Task 1（配置） + Task 4（Prompt）
Teammate 2: Task 2（SQL Engine） + Task 6（安全校验）
```

两组完成后合并，再串行执行：

```
任意一人: Task 3（Handler，依赖 1+2+4+6）
任意一人: Task 5（集成联调）
任意一人: Task 7（回归测试 + 文档）
```

### 注意事项

- Task 2 需要数据库连接才能测试，确保 `.env` 或 `.env.local` 中有 MySQL 配置
- Task 3 的 Handler 是核心串联层，必须等 Task 1/2/4/6 完成后再做
- Task 7 回归测试需要 vLLM 服务运行中
