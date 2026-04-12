# SQL Agent 设计文档

> 创建日期：2026-04-12
> 状态：待实施
> 预估工作量：2-3 天

---

## 一、目标

让用户通过自然语言提出任意分析类问题，AI 自动生成 SQL 查询并返回结果，突破现有固定工具接口的局限。

### 解锁场景示例

```
"统计各部门近三个月工时趋势，按下降幅度排序"
"本月工时填报最少的前10个人"
"AI平台项目每周投入多少人日"
"张三上个月都在哪些项目上花了时间"
"哪些人本周还没填工时"
```

---

## 二、架构设计

### 整体流程

```
用户请求（自然语言分析问题）
  │
  ▼
LangGraph node_llm_with_tools
  │  LLM 识别为 sql_query 工具调用
  ▼
sql_query Tool Handler
  │
  ├─ 1. 权限预检（PermissionValidator.get_data_filter）
  │     → 生成 WHERE 约束条件（基于 entity_type）
  │
  ├─ 2. 构建 SQL 生成 Prompt
  │     → 表结构 schema + 权限约束 + 用户问题
  │
  ├─ 3. LLM 生成 SQL
  │     → 独立 LLM 配置（SQL_AGENT_LLM_*），默认复用 CHAT_LLM（vLLM qwen3-8b）
  │     → 复杂 SQL 场景可单独指向更强模型（如 DashScope qwen-plus）
  │
  ├─ 4. SQL 安全校验
  │     → SELECT-only 白名单 + 表白名单 + 列黑名单
  │
  ├─ 5. 执行 SQL
  │     → SQLAlchemy 只读连接（ai_readonly 账号）
  │     → LIMIT 500 强制截断
  │
  ├─ 6. LLM 汇总结果
  │     → 将查询结果转为自然语言回答
  │
  └─ 7. 返回结果
        → {success, answer, sql, row_count}
```

### 与现有架构的关系

```
现有 LangGraph DAG（不变）：
  START → node_llm_with_tools → route
    ├─ execute_tool → END          ← sql_query 走这条路径
    ├─ execute_rag → END
    ├─ execute_llm → END
    ├─ clarify_node → END
    └─ plan_and_execute → END

新增：
  tool_registry 中注册 sql_query 工具
  → LLM 通过 Function Calling 自动选择调用
  → 无需修改 LangGraph 图结构
```

---

## 三、新增文件

| 文件 | 职责 |
|------|------|
| `app/tools/sql_query.py` | 工具注册 + Handler + SQL 安全校验 |
| `app/services/sql_engine.py` | SQLAlchemy 连接管理 + SQL 执行 + 表 schema 缓存 |
| `app/prompts/sql_agent.yaml` | SQL 生成 Prompt + 结果汇总 Prompt |

### 修改文件

| 文件 | 改动 |
|------|------|
| `app/core/config.py` | 新增 SQL Agent 配置项 |
| `.env.example` | 新增 SQL 数据库连接配置 |
| `app/prompts/system.yaml` | 新增 sql_query 工具触发条件描述 |

---

## 四、数据库配置

### 账号方案

**推荐方案**：独立只读账号（数据库层面封死写操作）

```sql
CREATE USER 'ai_readonly'@'%' IDENTIFIED BY 'strong_password_here';
GRANT SELECT ON workhour_db.workhour TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.project_info TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.sys_user TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.workhour_attendence TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.org_dept TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.work_calendar TO 'ai_readonly'@'%';
GRANT SELECT ON workhour_db.project_member TO 'ai_readonly'@'%';
FLUSH PRIVILEGES;
```

**过渡方案**：如暂时无法创建只读账号，可复用现有 root 账号，但**必须启用应用层 SQL 校验**（详见第六节）。后续补上只读账号即可，代码无需改动。

### 配置项（`config.py`）

```python
# SQL Agent 数据库配置（独立于现有 MYSQL_* 配置）
SQL_AGENT_DB_HOST: str = ""       # 空 = 复用 MYSQL_HOST
SQL_AGENT_DB_PORT: int = 0        # 0 = 复用 MYSQL_PORT
SQL_AGENT_DB_NAME: str = ""       # 空 = 复用 MYSQL_DATABASE
SQL_AGENT_DB_USER: str = ""       # 空 = 复用 MYSQL_USER（过渡期）
SQL_AGENT_DB_PASSWORD: str = ""   # 空 = 复用 MYSQL_PASSWORD（过渡期）

# SQL Agent LLM 配置（独立于 CHAT_LLM，允许指向更强模型）
# 为空时回退复用 CHAT_LLM_* 配置（即 vLLM qwen3-8b）
SQL_AGENT_LLM_API_KEY: str = ""     # 空 = 复用 CHAT_LLM_API_KEY
SQL_AGENT_LLM_API_BASE: str = ""    # 空 = 复用 CHAT_LLM_API_BASE
SQL_AGENT_LLM_MODEL: str = ""       # 空 = 复用 CHAT_LLM_MODEL
# 示例：指向 DashScope qwen-plus 获得更强 SQL 生成能力
# SQL_AGENT_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
# SQL_AGENT_LLM_MODEL=qwen-plus

# SQL Agent 安全配置
SQL_AGENT_ENABLED: bool = True
SQL_AGENT_MAX_ROWS: int = 500
SQL_AGENT_QUERY_TIMEOUT: int = 30  # 秒
```

> 配置项独立于现有 `MYSQL_*`，允许后续指向不同的只读实例或从库。
> 为空时回退复用现有配置，做到零配置即可启动。

---

## 五、表 Schema 与访问控制

### 允许访问的表（白名单）

```python
ALLOWED_TABLES = {
    "workhour": "工时记录表（核心表）",
    "project_info": "项目信息表",
    "sys_user": "用户/员工表",
    "workhour_attendence": "考勤记录表",
    "org_dept": "部门/组织表",
    "work_calendar": "工作日历表",
    "project_member": "项目成员关联表",
}
```

> 后续扩展：只需在此字典中添加新表名即可。

### 敏感列黑名单（禁止在 SQL 中出现）

```python
BLOCKED_COLUMNS = {
    "sys_user": ["password", "password_hash", "salt", "id_card", "phone", "email"],
}
```

> SQL 生成 Prompt 中不会展示这些列的 schema，且执行前校验 SQL 中不包含这些列名。

### 表结构 Schema 自动获取

```python
# sql_engine.py 启动时自动 introspect
async def get_table_schemas() -> Dict[str, str]:
    """
    对每张允许的表执行 SHOW CREATE TABLE，
    过滤掉黑名单列后缓存结构描述。
    进程级缓存，TTL 1 小时。
    """
```

---

## 六、安全层设计（三道防线）

### 第一道：数据库账号（只读）

- `ai_readonly` 仅有 SELECT 权限
- 即使应用层被绕过，数据库层面也无法执行写操作

### 第二道：应用层 SQL 校验

在执行前对 LLM 生成的 SQL 进行白名单校验：

```python
import sqlparse

def validate_sql(sql: str) -> tuple[bool, str]:
    """
    校验规则：
    1. 仅允许 SELECT 语句（禁止 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE）
    2. 禁止多条语句（分号分割）
    3. 仅允许访问白名单中的表
    4. 禁止访问黑名单列
    5. 禁止 INTO OUTFILE / LOAD_FILE 等文件操作
    6. 强制追加 LIMIT（无 LIMIT 则自动加 LIMIT 500）
    """
```

### 第三道：权限约束注入（Prompt 层）

根据用户角色，在 SQL 生成 Prompt 中注入强制 WHERE 约束：

```python
def build_permission_constraint(context: PermissionContext) -> str:
    """
    基于 PermissionValidator.get_data_filter() 生成 SQL 约束描述。
    注入到 LLM 的 system prompt 中，LLM 生成 SQL 时必须遵守。
    """
    data_filter = permission_validator.get_data_filter(context)

    if data_filter.is_unrestricted:
        return "你可以查询所有数据，无权限限制。"

    constraints = []
    if data_filter.user_ids:
        ids = ", ".join(f"'{uid}'" for uid in data_filter.user_ids)
        constraints.append(f"工时表(workhour)的 member_id 必须在 ({ids}) 范围内")
    if data_filter.department_ids:
        ids = ", ".join(f"'{did}'" for did in data_filter.department_ids)
        constraints.append(f"用户表(sys_user)的 dept_id 必须在 ({ids}) 范围内")

    return "数据权限约束（必须遵守）：\n" + "\n".join(f"- {c}" for c in constraints)
```

### 各角色权限矩阵

| 角色 | 数据范围 | SQL 约束 |
|------|---------|---------|
| superAdmin | 全部数据 | 无限制 |
| regionAdmin | 管辖大区所有部门 | `dept_id IN (管辖部门列表)` |
| companyAdmin | 公司范围 | `dept_id IN (管辖部门列表)` |
| deptAdmin | 本部门所有成员 | `dept_id = 本部门ID` |
| deptSubAdmin | 本部门所有成员 | `dept_id = 本部门ID` |
| employee | 仅自己 | `member_id = 自己的ID` |

---

## 七、Prompt 设计

### SQL 生成 Prompt（`sql_agent.yaml`）

```yaml
sql_generation:
  system: |
    你是一个 SQL 查询助手，根据用户的自然语言问题生成 MySQL 查询语句。

    ## 数据库表结构
    {table_schemas}

    ## 数据权限约束
    {permission_constraints}

    ## 规则
    1. 只生成 SELECT 语句，禁止任何写操作
    2. 必须遵守数据权限约束中的 WHERE 条件
    3. 查询结果行数不要超过 500 行，必要时使用 LIMIT
    4. 日期字段使用 DATE_FORMAT 格式化为可读格式
    5. 使用中文别名（AS）使结果列名更易读
    6. 工时表的 workhour_date 存储为 datetime 类型，比较日期时使用 DATE() 函数
    7. 涉及员工姓名时 JOIN sys_user 表（entity_name 字段）
    8. 涉及项目名称时 JOIN project_info 表（project_name 字段）
    9. 涉及部门名称时 JOIN org_dept 表
    10. 只输出 SQL 语句，不要输出任何解释

    ## 常用关联
    - workhour.member_id = sys_user.id
    - workhour.project_id = project_info.id
    - sys_user.dept_id = org_dept.id
    - project_member.project_id = project_info.id
    - project_member.member_id = sys_user.id

  user: |
    {user_question}

sql_summarize:
  system: |
    你是一个数据分析助手。根据用户的问题和 SQL 查询结果，用简洁的中文回答。

    规则：
    1. 直接回答问题，不要描述 SQL 本身
    2. 关键数字加粗
    3. 如果数据量较多，用表格格式展示
    4. 如果查询结果为空，友好地告知用户
    5. 如果数据量超过展示限制，说明"仅展示前 N 条"

  user: |
    用户问题：{user_question}

    查询结果（{row_count} 行）：
    {query_results}
```

### system.yaml 新增工具触发规则

```yaml
# 在现有 system prompt 中追加：
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

---

## 八、工具定义

### JSON Schema

```python
SQL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "用户的分析问题（自然语言）",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}
```

### 注册

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

---

## 九、Handler 实现逻辑

```python
async def sql_query_handler(**kwargs) -> Dict[str, Any]:
    """
    SQL Agent 工具处理函数。

    流程：
    1. 提取用户问题和权限上下文
    2. 获取表结构 schema（缓存）
    3. 构建权限约束
    4. 调用 LLM 生成 SQL
    5. 校验 SQL 安全性
    6. 执行 SQL（带超时）
    7. 调用 LLM 汇总结果
    8. 返回答案
    """
    question = kwargs.get("question", "")
    auth_token = kwargs.pop("auth_token", None)
    user_id = kwargs.pop("user_id", None)
    entity_type = kwargs.pop("entity_type", "employee")
    department_id = kwargs.pop("department_id", None)

    if not question:
        return {"success": False, "error": "请描述您想分析的问题"}

    # 1. 构建权限上下文
    context = PermissionContext(
        user_id=user_id or "unknown",
        entity_type=entity_type,
        department_id=department_id,
    )

    # 2. 获取表结构
    table_schemas = await sql_engine.get_table_schemas()

    # 3. 构建权限约束
    permission_constraints = build_permission_constraint(context)

    # 4. LLM 生成 SQL（使用独立 LLM 配置，默认回退 CHAT_LLM）
    sql_prompt = prompt_manager.render("sql_agent", "sql_generation", {
        "table_schemas": table_schemas,
        "permission_constraints": permission_constraints,
        "user_question": question,
    })
    generated_sql = await sql_llm_client.call(sql_prompt)  # sql_llm_client: 独立实例

    # 5. SQL 安全校验
    is_safe, error_msg = validate_sql(generated_sql)
    if not is_safe:
        logger.warning(f"SQL 安全校验失败: {error_msg}, SQL: {generated_sql}")
        return {"success": False, "error": f"查询被安全策略拦截：{error_msg}"}

    # 6. 执行 SQL
    try:
        results, columns = await sql_engine.execute_query(
            generated_sql,
            timeout=settings.SQL_AGENT_QUERY_TIMEOUT,
            max_rows=settings.SQL_AGENT_MAX_ROWS,
        )
    except Exception as e:
        logger.error(f"SQL 执行失败: {e}, SQL: {generated_sql}")
        # SQL 执行失败时可以重试一次（让 LLM 修正 SQL）
        return {"success": False, "error": f"查询执行失败，请换个方式描述问题"}

    # 7. LLM 汇总结果
    formatted_results = format_results(results, columns)
    summary_prompt = prompt_manager.render("sql_agent", "sql_summarize", {
        "user_question": question,
        "row_count": len(results),
        "query_results": formatted_results,
    })
    answer = await llm_client.call(summary_prompt)

    return {
        "success": True,
        "answer": answer,
        "sql": generated_sql,
        "row_count": len(results),
    }
```

---

## 十、sql_engine.py 核心设计

```python
"""
SQL Engine — SQL Agent 的数据库连接和查询执行层

职责：
1. 管理 SQLAlchemy 只读连接池
2. 自动获取并缓存表结构
3. 执行 SQL 查询（带超时和行数限制）
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
import asyncio

class SQLEngine:
    def __init__(self):
        self._engine = None
        self._schema_cache = {}
        self._schema_cache_time = 0

    async def initialize(self, db_url: str):
        """初始化连接池"""
        self._engine = create_async_engine(
            db_url,
            pool_size=5,
            max_overflow=2,
            pool_recycle=3600,
            echo=False,
        )

    async def get_table_schemas(self) -> str:
        """
        获取允许访问的表结构（缓存 1 小时）。
        返回格式化的 DDL 文本（已过滤敏感列）。
        """

    async def execute_query(
        self, sql: str, timeout: int = 30, max_rows: int = 500
    ) -> tuple[list[dict], list[str]]:
        """
        执行 SELECT 查询。
        - timeout：查询超时秒数
        - max_rows：最大返回行数
        返回 (rows, column_names)
        """

    async def close(self):
        """关闭连接池"""
        if self._engine:
            await self._engine.dispose()

# 全局单例
sql_engine = SQLEngine()
```

### 连接 URL 构建

```python
def build_db_url(settings) -> str:
    host = settings.SQL_AGENT_DB_HOST or settings.MYSQL_HOST
    port = settings.SQL_AGENT_DB_PORT or settings.MYSQL_PORT
    db = settings.SQL_AGENT_DB_NAME or settings.MYSQL_DATABASE
    user = settings.SQL_AGENT_DB_USER or settings.MYSQL_USER
    password = settings.SQL_AGENT_DB_PASSWORD or settings.MYSQL_PASSWORD
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4"
```

---

## 十一、错误处理与重试

### SQL 生成失败时的重试机制

```
第一次生成 SQL → 执行失败（语法错误 / 表名错误）
  → 将错误信息 + 原 SQL 反馈给 LLM
  → 第二次生成 SQL → 执行
  → 仍失败 → 返回友好提示"请换个方式描述问题"
```

最多重试 1 次，避免无限循环。

### 常见错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| SQL 语法错误 | 反馈 LLM 重试 1 次 |
| 表不存在 | 返回"该数据暂不支持查询" |
| 查询超时 | 返回"查询数据量过大，请缩小范围" |
| 连接失败 | 返回"数据库连接异常，请稍后重试" |
| 权限校验拦截 | 返回"您没有查看该数据的权限" |
| 结果为空 | 正常返回"未查询到相关数据" |

---

## 十二、依赖项

```
# requirements.txt 新增
sqlalchemy[asyncio]>=2.0
aiomysql>=0.2.0
sqlparse>=0.5.0
```

---

## 十三、实施步骤

### Day 1：基础框架

1. `config.py` 新增 SQL Agent 配置项
2. `sql_engine.py`：连接管理 + schema 缓存 + 查询执行
3. `sql_query.py`：工具注册 + Handler 框架 + SQL 校验逻辑
4. `sql_agent.yaml`：SQL 生成 + 结果汇总 Prompt
5. 单元测试：SQL 校验函数

### Day 2：集成与权限

6. Handler 完整实现（LLM 调用 + 错误重试）
7. 权限约束注入（复用 PermissionValidator）
8. `system.yaml` 新增 sql_query 工具触发规则
9. 集成测试：端到端流程（自然语言 → SQL → 结果 → 回答）

### Day 3：优化与验证

10. 敏感列过滤 + 表 schema 自动 introspect
11. 边界场景测试（权限隔离、超时、大数据量、SQL 注入尝试）
12. 在线联调（接入真实数据库验证效果）
13. 精度回归测试（确保 sql_query 注册后不影响现有 87% 精度）

---

## 十四、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM 生成错误 SQL | 查询失败 | 重试 1 次 + 友好提示 |
| LLM 生成绕过权限的 SQL | 数据泄露 | 三道防线：DB 只读 + 应用校验 + Prompt 约束 |
| LLM 误选 sql_query（该用现有工具） | 性能浪费 | system prompt 明确区分场景 |
| 大表全扫描导致慢查询 | 性能问题 | LIMIT 500 + 查询超时 30s |
| 注册新工具后影响现有精度 | 精度回退 | Day 3 跑回归测试验证 |

---

## 十五、后续扩展

完成基础版后，可考虑：

1. **查询结果可视化**：返回图表（ECharts JSON），前端渲染
2. **SQL 缓存**：相同语义问题复用已生成的 SQL，减少 LLM 调用
3. **自然语言 → SQL 精度测试集**：建立评估集，持续优化 Prompt
4. **从库读取**：配置 `SQL_AGENT_DB_HOST` 指向 MySQL 从库，不影响主库性能
