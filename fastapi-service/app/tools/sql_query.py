"""
SQL Query Tool - SQL Agent 的 SQL 查询执行工具

职责：
1. 注册 sql_query 工具到 tool_registry
2. 实现 sql_query_handler 完整流程：
   - 提取参数（user_question）
   - 构建权限上下文
   - 获取表结构
   - LLM 生成 SQL
   - 安全校验 + 执行
   - LLM 汇总结果
   - 失败重试 1 次
3. 提供独立的 SQL Agent LLM 客户端（独立配置，回退到 CHAT_LLM）
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import sqlparse
from sqlparse.sql import Identifier, IdentifierList

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


def _get_cross_db_tables(sql: str) -> List[str]:
    """用 sqlparse 提取 SQL 中的跨库表名（含 . 的表名如 db.table、`db`.`table`）。"""
    parsed = sqlparse.parse(sql)
    cross_db_tables = []

    def _extract(token):
        if isinstance(token, Identifier):
            parent = token.get_parent_name()
            real = token.get_real_name()
            if parent and real:
                cross_db_tables.append(f"{parent}.{real}")
        elif isinstance(token, IdentifierList):
            for ident in token.get_identifiers():
                _extract(ident)
        elif token.is_group:
            for sub in token.tokens:
                _extract(sub)

    for stmt in parsed:
        for token in stmt.tokens:
            _extract(token)
    return cross_db_tables

# ── JSON Schema ────────────────────────────────────────────────────────────────

SQL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "用户的自然语言问题，如「各部门本月工时对比」「工时最多的前10人」"
        }
    },
    "required": ["question"],
    "additionalProperties": False
}

# ── SQL 安全校验（从 sql_engine 导入） ──────────────────────────────────────

ALLOWED_TABLES = {
    "workhour": "工时记录表（核心表）",
    "project_info": "项目信息表",
    "sys_user": "用户/员工表",
    "workhour_attendance": "考勤记录表",
    "org_dept": "部门/组织表",
    "work_calendar": "工作日历表",
    "project_member": "项目成员关联表",
}

BLOCKED_COLUMNS = {
    "sys_user": ["password", "password_hash", "salt", "id_card", "phone", "email"],
}


def validate_sql(sql: str, max_rows: int = 500) -> Tuple[bool, str]:
    """
    校验 LLM 生成的 SQL 是否安全。
    返回 (is_safe, sql_or_error)。
    校验通过时返回添加了 LIMIT 的完整 SQL。
    """
    import sqlparse

    if not sql or not sql.strip():
        return False, "SQL 为空"

    sql_lower = sql.lower()

    # 1. 多语句检测
    statements = sqlparse.split(sql)
    if len(statements) > 1:
        return False, "禁止多条 SQL 语句"

    # 2. 语句类型检测
    parsed = sqlparse.parse(sql)
    for stmt in parsed:
        stmt_type = stmt.get_type().upper()
        if stmt_type and stmt_type != 'SELECT' and stmt_type != 'UNKNOWN':
            return False, f"仅允许 SELECT 语句，当前为 {stmt_type}"

    # 3. 危险关键字检测（使用单词边界避免 is_deleted 等列名误判）
    dangerous_keywords = [
        r'\binto\s+outfile\b', r'\bload_file\b', r'\binto\s+dumpfile\b',
        r'\bbenchmark\b', r'\bsleep\b', r'\bload\s+data\b',
        r'\bdrop\b', r'\bdelete\b', r'\bupdate\b', r'\binsert\b', r'\balter\b', r'\bcreate\b', r'\btruncate\b'
    ]
    for keyword in dangerous_keywords:
        if re.search(keyword, sql_lower):
            return False, f"禁止使用 {keyword}"

    # 4. 跨库访问检测：仅检测 FROM/JOIN 子句中的 db.table 格式（alias.column 不属于此类）
    cross_db_in_clause = re.search(
        r'\b(?:FROM|JOIN)\s+`?\w+`?\s*\.\s*`?\w+`?', sql, re.IGNORECASE
    )
    if cross_db_in_clause:
        return False, "禁止跨库访问"

    # 5. 表白名单检测
    from_patterns = [
        r'\bFROM\s+`?(\w+)`?',
        r'\bJOIN\s+`?(\w+)`?',
        r'\bINNER\s+JOIN\s+`?(\w+)`?',
        r'\bLEFT\s+JOIN\s+`?(\w+)`?',
        r'\bRIGHT\s+JOIN\s+`?(\w+)`?'
    ]
    table_names = set()
    for pattern in from_patterns:
        matches = re.findall(pattern, sql, re.IGNORECASE)
        for m in matches:
            if isinstance(m, tuple):
                table_names.update(t.lower() for t in m if t)
            else:
                table_names.add(m.lower())

    for table in table_names:
        if table not in ALLOWED_TABLES:
            return False, f"表 {table} 不在允许列表中"

    # 6. 列黑名单检测
    for table, columns in BLOCKED_COLUMNS.items():
        for col in columns:
            if re.search(rf'\b{col}\b', sql, re.IGNORECASE):
                return False, f"列 {col} 禁止访问"

    # 6. LIMIT 强制追加
    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql = sql.rstrip().rstrip(';') + f" LIMIT {max_rows}"

    return True, sql


# ── SQL Agent 独立 LLM 客户端 ────────────────────────────────────────────────

class SQLAgentLLMClient:
    """
    SQL Agent 专用 LLM 客户端。
    优先使用 SQL_AGENT_LLM_* 配置，回退到 CHAT_LLM_*。
    """

    def __init__(self):
        from app.core.config import settings

        # 优先使用 SQL Agent 独立配置，回退到 CHAT_LLM_*
        self._api_key = (
            settings.SQL_AGENT_LLM_API_KEY
            or settings.CHAT_LLM_API_KEY
        )
        self._api_base = (
            settings.SQL_AGENT_LLM_API_BASE
            or settings.CHAT_LLM_API_BASE
        )
        self._model = (
            settings.SQL_AGENT_LLM_MODEL
            or settings.CHAT_LLM_MODEL
        )

        if not self._api_key:
            logger.warning("SQL Agent LLM API Key 未配置")

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        调用 LLM 生成文本。
        messages: [{"role": "system|user|assistant", "content": "..."}]
        """
        import aiohttp
        import re as _re

        url = f"{self._api_base.rstrip('/')}/chat/completions"
        # vLLM qwen3 通过 chat_template_kwargs 关闭 thinking；Ollama 用 think:false
        _is_ollama = "ollama" in self._api_base.lower()
        if _is_ollama:
            _thinking_param = {"think": False}
        else:
            _thinking_param = {"chat_template_kwargs": {"enable_thinking": False}}
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.1),
            "max_tokens": kwargs.get("max_tokens", 800),
            **_thinking_param,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"] or ""
                        content = _re.sub(r"<think>[\s\S]*?</think>", "", content, flags=_re.DOTALL).strip()
                        if "<think>" in content:
                            content = _re.sub(r"<think>[\s\S]*", "", content, flags=_re.DOTALL).strip()
                        return content
                    else:
                        error_text = await resp.text()
                        raise Exception(f"LLM API 错误: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"SQL Agent LLM 调用失败: {e}")
            raise


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _build_permission_constraints(context: Any) -> str:
    """根据权限上下文构建 SQL WHERE 条件约束文本。"""
    from app.services.permission_validator import PermissionContext, permission_validator

    # 空 context fallback（测试环境或匿名用户）
    if not context:
        return "（无数据范围限制，匿名查询模式）"

    # 将 dict 转为 PermissionContext，避免 ToolExecutionContext 与权限模型字段不兼容。
    if isinstance(context, dict):
        context = dict(context)
        # 补全缺失的必填字段，防止 ValidationError
        if not context.get("user_id"):
            context["user_id"] = "anonymous"
        if not context.get("entity_type"):
            context["entity_type"] = "employee"
        ctx = PermissionContext(**context)
    else:
        ctx = context

    validator = permission_validator
    data_filter = validator.get_data_filter(ctx)

    constraints = []
    if data_filter.is_unrestricted:
        return "（无数据范围限制，管理员可查询所有数据）"

    if data_filter.user_ids:
        user_ids = ",".join(f"'{uid}'" for uid in data_filter.user_ids)
        constraints.append(f"workhour.member_id IN ({user_ids})")

    if data_filter.department_ids:
        dept_ids = ",".join(f"'{did}'" for did in data_filter.department_ids)
        constraints.append(f"sys_user.dept_id IN ({dept_ids})")

    if data_filter.project_ids:
        project_ids = ",".join(f"'{pid}'" for pid in data_filter.project_ids)
        constraints.append(f"workhour.project_id IN ({project_ids})")

    if constraints:
        return "数据范围限制（自动注入 WHERE 条件）：\n  " + "\n  AND ".join(constraints)
    else:
        return "（无显式数据范围限制，仅返回当前用户数据）"


_PROTECTED_MEMBER_TABLES = {"workhour", "workhour_attendance", "project_member"}
_SQL_KEYWORDS = {
    "where", "join", "inner", "left", "right", "full", "cross", "on", "group",
    "order", "having", "limit", "union", "offset", "for", "into",
}


def enforce_sql_permissions(sql: str, context: Any) -> Tuple[str, Dict[str, Any]]:
    """在每个受保护数据源内部强制施加可信权限范围。"""
    from app.services.permission_validator import EntityType, PermissionContext

    if not context:
        raise PermissionError("缺少权限上下文，拒绝执行 SQL")
    if isinstance(context, dict):
        try:
            ctx = PermissionContext(**dict(context))
        except Exception as exc:
            raise PermissionError(f"权限上下文无效: {exc}") from exc
    elif isinstance(context, PermissionContext):
        ctx = context
    else:
        try:
            ctx = PermissionContext.model_validate(context, from_attributes=True)
        except Exception as exc:
            raise PermissionError(f"权限上下文类型无效: {exc}") from exc

    if ctx.entity_type == EntityType.SUPER_ADMIN:
        return sql, {}

    params: Dict[str, Any] = {}
    if ctx.entity_type == EntityType.EMPLOYEE:
        params["perm_user_id"] = ctx.user_id
        member_predicate = "{alias}.member_id = :perm_user_id"
        user_predicate = "{alias}.id = :perm_user_id"
    else:
        department_ids = list(dict.fromkeys(
            ([ctx.department_id] if ctx.department_id else []) + list(ctx.managed_departments)
        ))
        if not department_ids:
            raise PermissionError("管理员缺少管辖部门范围，拒绝执行 SQL")
        placeholders = []
        for index, department_id in enumerate(department_ids):
            key = f"perm_dept_{index}"
            params[key] = department_id
            placeholders.append(f":{key}")
        dept_list = ", ".join(placeholders)
        member_predicate = (
            "EXISTS (SELECT 1 FROM sys_user __perm_user "
            "WHERE __perm_user.id = {alias}.member_id "
            f"AND __perm_user.org_id IN ({dept_list}))"
        )
        user_predicate = f"{{alias}}.org_id IN ({dept_list})"

    source_pattern = re.compile(
        r"(?P<prefix>\b(?:FROM|JOIN)\s+)"
        r"(?P<quote>`?)(?P<table>workhour_attendance|project_member|workhour|sys_user)(?P=quote)"
        r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
        re.IGNORECASE,
    )
    protected_count = 0

    def replace_source(match: re.Match) -> str:
        nonlocal protected_count
        table = match.group("table").lower()
        alias = match.group("alias")
        if alias and alias.lower() in _SQL_KEYWORDS:
            alias = None
        alias = alias or table
        predicate = user_predicate if table == "sys_user" else member_predicate
        predicate = predicate.format(alias="__perm_src")
        protected_count += 1
        return (
            f"{match.group('prefix')}(SELECT * FROM {table} AS __perm_src "
            f"WHERE {predicate}) AS {alias}"
            + (f" {match.group('alias')}" if match.group("alias") and match.group("alias").lower() in _SQL_KEYWORDS else "")
        )

    scoped_sql = source_pattern.sub(replace_source, sql)
    if protected_count == 0:
        # 日历等公共只读表无需主体范围；其他表仍由表白名单负责。
        return sql, params
    return scoped_sql, params


# ── Handler ──────────────────────────────────────────────────────────────────

async def sql_query_handler(**kwargs) -> Dict[str, Any]:
    """
    SQL 查询处理函数。

    完整流程：
    1. 提取参数（question）
    2. 构建权限上下文
    3. 获取表结构
    4. LLM 生成 SQL
    5. 安全校验
    6. 执行 SQL（失败重试 1 次）
    7. LLM 汇总结果
    """
    from app.core.config import settings
    from app.services.sql_engine import sql_engine
    from app.services.prompt_manager import get_prompt_manager

    question: str = kwargs.get("question", "")          # 用户消息
    context: Dict[str, Any] = kwargs.get("context", {}) # 权限上下文
    user_id: str = context.get("user_id", "")
    entity_type: str = context.get("entity_type", "employee")
    department_id: Optional[str] = context.get("department_id")

    if not question:
        return {"success": False, "error": "question 参数为空"}

    # 1. 初始化 SQL Engine（如未初始化）
    if not hasattr(sql_engine, "_engine") or sql_engine._engine is None:
        await sql_engine.initialize()

    # 2. 获取表结构
    try:
        table_schemas = await sql_engine.get_table_schemas(question=question) # 基于用户提问获取相应的表字段，兜底两个表
    except Exception as e:
        logger.error(f"获取表结构失败: {e}")
        return {"success": False, "error": f"获取表结构失败: {e}"}

    # 3. 构建权限约束
    permission_constraints = _build_permission_constraints(context)

    # 4. LLM 生成 SQL
    #    prompt 结构优化：system 短指令 + user 紧凑上下文
    #    让 qwen3-8b 不会因 prompt 过长而"忘记"输出格式要求
    from datetime import date as _date, timedelta
    pm = get_prompt_manager()

    # 预计算常用日期范围，避免 LLM 自行推断出错（B9 修复）
    _today = _date.today()
    _week_start = _today - timedelta(days=_today.weekday())
    _last_week_start = _week_start - timedelta(days=7)
    _last_week_end = _week_start - timedelta(days=1)

    sql_generation_prompt = pm.format(
        "sql_generation",
        table_schemas=table_schemas,     # 表字段介绍
        permission_constraints=permission_constraints, # 数据范围限制
        user_question=question,
        user_id=user_id or "unknown",
        today=str(_today),
        department_id=department_id or "",
        month_start=str(_today.replace(day=1)),
        month_end=str(_today),
        week_start=str(_week_start),
        week_end=str(_today),
        last_week_start=str(_last_week_start),
        last_week_end=str(_last_week_end),
    )

    # F5 调试快照：sql_generation_prompt 的一次实际值，仅供理解 Prompt 拼装结果。
    # 根据问题生成MySQL SELECT语句。只输出SQL，不要解释。
    #
    # 当前用户ID：d1e88d66-cc87-40c7-bbe3-2dff2d093b41
    # 今天日期：2026-08-03
    # 当前用户部门ID：
    #
    # 常用日期范围（直接使用，不要自行计算）：
    # - 本月：2026-08-01 至 2026-08-03
    # - 本周：2026-08-03 至 2026-08-03
    # - 上周：2026-07-27 至 2026-08-02
    #
    # 表结构：
    # sys_user(id PK, entity_name 员工姓名, dept_id FK→org_dept.id)
    # workhour(id PK, member_id FK→sys_user.id, workhour_date datetime,
    #          workhour decimal 小时数, project_id FK→project_info.id,
    #          work_content 工作内容, is_deleted 0未删/1已删)
    #
    # 表关联规则：
    # - workhour.member_id = sys_user.id（人员）
    # - workhour.project_id = project_info.id（项目）
    # - sys_user.org_id = org_dept.ext_field_2（部门，用于获取部门名称：
    #   org_dept.dept_name；注意是 org_id 而非 dept_id）
    # - project_member.member_id = sys_user.id（项目成员）
    # - workhour_attendance.member_id = sys_user.id（考勤/加班，关键字段：
    #   overtime_hours加班时长、check_in_time上班打卡、
    #   check_out_time下班打卡、work_date日期）
    #
    # 【部门层级规则】org_dept 表中 ext_field_2 是父部门 ID：
    # - 二级部门：id = ext_field_2（自己指向自己，即有子部门归在它下面的部门）
    # - 三级子部门：id != ext_field_2（有明确父部门的班组/小组）
    # - 用户问“各部门”“部门占比”“部门排名”时，只统计二级部门
    #   （WHERE od.id = od.ext_field_2），不展开到三级子部门，避免图表过碎。
    # - 关联写法：JOIN org_dept od ON su.org_id = od.ext_field_2
    #   WHERE od.id = od.ext_field_2
    #
    # 【漏填工时专用查询模板】查询用户在某日期区间内哪些工作日没有填工时：
    # SELECT DATE(wc.date_value) AS 日期, wc.work_hour AS 应填工时
    # FROM work_calendar wc
    # LEFT JOIN workhour wh
    #   ON DATE(wc.date_value)=DATE(wh.workhour_date)
    #  AND wh.member_id='d1e88d66-cc87-40c7-bbe3-2dff2d093b41'
    # WHERE wc.is_work_day='1'
    #   AND DATE(wc.date_value) BETWEEN '起始日期' AND '结束日期'
    #   AND wh.id IS NULL
    # ORDER BY wc.date_value LIMIT 100
    # 说明：work_calendar.is_work_day='1'（字符串）；work_calendar.work_hour
    # 是该天应填工时；不要 JOIN sys_user 或 org_dept（漏填时 wh 记录不存在，
    # JOIN 结果全为 NULL）。
    #
    # 【加班时长专用查询模板】查询个人加班数据（workhour_attendance 表）：
    # SELECT wa.work_date AS 日期, wa.overtime_hours AS 加班时长,
    #        wa.overtime_type AS 加班类型
    # FROM workhour_attendance wa
    # WHERE wa.member_id='d1e88d66-cc87-40c7-bbe3-2dff2d093b41'
    #   AND wa.work_date BETWEEN '起始日期' AND '结束日期'
    #   AND wa.overtime_hours > 0
    # ORDER BY wa.work_date LIMIT 100
    # 说明：overtime_hours 是 decimal 类型；汇总加班总时长用
    # SUM(wa.overtime_hours)；member_id 对应 sys_user.id。
    #
    # 【部门加班统计模板】当问题含“部门”“本部门”“排名”时，
    # 查部门内所有人的加班排名：
    # SELECT su.entity_name AS 姓名,
    #        SUM(wa.overtime_hours) AS 加班总时长
    # FROM workhour_attendance wa
    # JOIN sys_user su ON wa.member_id = su.id
    # JOIN org_dept od ON su.org_id = od.ext_field_2
    # WHERE od.ext_field_2 = ''
    #   AND wa.work_date BETWEEN '起始日期' AND '结束日期'
    #   AND wa.overtime_hours > 0
    # GROUP BY su.id, su.entity_name
    # ORDER BY SUM(wa.overtime_hours) DESC LIMIT 100
    # 说明：od.ext_field_2 是部门ID，与 sys_user.org_id 关联；必须用
    # JOIN sys_user + org_dept 扩展范围，不得仅限制当前 member_id。
    #
    # 权限约束：数据范围限制（自动注入 WHERE 条件）：
    #   workhour.member_id IN ('d1e88d66-cc87-40c7-bbe3-2dff2d093b41')
    #
    # 生成规则：
    # 1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE
    # 2. workhour.workhour_date 是 datetime 类型，日期比较必须用 DATE() 函数
    # 3. 人员姓名用 sys_user.entity_name 字段
    # 4. 部门名称用 org_dept.dept_name 字段，关联条件为
    #    sys_user.org_id = org_dept.ext_field_2
    # 5. 所有列名必须使用中文别名（AS 中文名）
    # 6. 结果必须加 LIMIT（默认 LIMIT 100）
    # 7. 涉及漏填工时时，必须使用上方漏填工时专用查询模板
    # 8. 涉及部门范围时，必须使用部门加班统计模板
    #
    # 问题：上个月加班时长最多的三个人，分别加了多少小时？

    llm_client = SQLAgentLLMClient()
    generated_sql = ""

    try:
        generated_sql = await llm_client.generate(
            messages=[
                {"role": "system", "content": "Output ONLY one MySQL SELECT statement. No explanation. No markdown. No code blocks. Pure SQL text only."},
                {"role": "user", "content": sql_generation_prompt}
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        # 清理 LLM 返回内容中的 thinking/reasoning 内容
        generated_sql = re.sub(r"<think>[\s\S]*?</think>", "", generated_sql)
        generated_sql = generated_sql.strip()
        # 清理 LLM 返回的 markdown 代码块
        generated_sql = re.sub(r"^```sql\s*", "", generated_sql)
        generated_sql = re.sub(r"^```\s*", "", generated_sql)
        generated_sql = re.sub(r"\s*```$", "", generated_sql)
        generated_sql = generated_sql.strip()
    except Exception as e:
        logger.error(f"SQL 生成失败: {e}")
        return {"success": False, "error": f"SQL 生成失败: {e}"}

    # 5. 安全校验
    is_safe, sql_or_error = validate_sql(generated_sql, max_rows=settings.SQL_AGENT_MAX_ROWS)
    if not is_safe:
        logger.warning(f"SQL 安全校验未通过: {sql_or_error} | SQL: {generated_sql}")
        return {"success": False, "error": f"SQL 安全校验未通过: {sql_or_error}"}

    candidate_sql = sql_or_error

    # 6. 服务端强制权限注入；改写后再次执行完整安全校验。
    try:
        scoped_sql, permission_params = enforce_sql_permissions(candidate_sql, context)
    except PermissionError as e:
        logger.warning(f"SQL 权限注入失败: {e}")
        return {"success": False, "error": f"SQL 权限校验未通过: {e}"}
    is_safe, final_sql_or_error = validate_sql(scoped_sql, max_rows=settings.SQL_AGENT_MAX_ROWS)
    if not is_safe:
        logger.warning(f"权限注入后的 SQL 复检失败: {final_sql_or_error}")
        return {"success": False, "error": f"SQL 权限注入后复检未通过: {final_sql_or_error}"}
    final_sql = final_sql_or_error

    # 7. 执行 SQL（重试 1 次）
    query_results = []
    columns = []
    max_retries = 1

    for attempt in range(max_retries + 1):
        try:
            execute_kwargs = {
                "timeout": settings.SQL_AGENT_QUERY_TIMEOUT,
                "max_rows": settings.SQL_AGENT_MAX_ROWS,
            }
            if permission_params:
                execute_kwargs["parameters"] = permission_params
            rows, columns = await sql_engine.execute_query(final_sql, **execute_kwargs)
            query_results = rows
            break
        except Exception as e:
            logger.warning(f"SQL 执行失败（第 {attempt + 1} 次）: {e}")
            if attempt >= max_retries:
                return {"success": False, "error": f"SQL 执行失败: {e}"}
            # 短暂等待后重试
            import asyncio
            await asyncio.sleep(0.5)

    # 7. LLM 汇总结果
    try:
        sql_summarize_prompt = pm.format(
            "sql_summarize",
            user_question=question,
            row_count=len(query_results),
            query_results=str(query_results),
        )
        summary = await llm_client.generate(
            messages=[
                {"role": "user", "content": sql_summarize_prompt}
            ],
            temperature=0.3,
            max_tokens=1500,
        )
    except Exception as e:
        logger.warning(f"SQL 结果汇总失败: {e}")
        summary = f"查询完成，共返回 {len(query_results)} 条数据（汇总生成失败）"

    # 转换非 JSON 可序列化类型（Decimal、date、datetime 等）
    from datetime import date as _date_type, datetime as _datetime_type
    def _sanitize_row(row):
        result = {}
        for k, v in row.items():
            if v is None:
                result[k] = None
            elif isinstance(v, _datetime_type):
                result[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(v, _date_type):
                result[k] = v.strftime("%Y-%m-%d")
            elif hasattr(v, '__float__') and not isinstance(v, (int, float, bool)):
                result[k] = str(v)
            else:
                result[k] = v
        return result

    return {
        "success": True,
        "question": question,
        "sql": final_sql,
        "row_count": len(query_results),
        "columns": columns,
        "data": [_sanitize_row(r) for r in query_results],
        "summary": summary,
    }


# ── 工具注册 ──────────────────────────────────────────────────────────────────

def register_sql_query_tool():
    """注册 sql_query 工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="sql_query",
            description=(
                "执行自定义 SQL 查询。适用场景：多表 JOIN 关联、复杂条件筛选、窗口函数、自定义时间区间聚合。"
                "【必须使用此工具的场景】："
                "1. 漏填工时查询（关联 work_calendar 与 workhour 找出未填日期）；"
                "2. 加班时长查询（加班数据在 workhour_attendance.overtime_hours，compute_statistics 不含此字段）；"
                "3. 考勤异常查询（workhour_attendance.is_abnormal）；"
                "4. 打卡时间查询（workhour_attendance.check_in_time/check_out_time）。"
                "参数：question（自然语言问题）。"
            ),
            json_schema=SQL_QUERY_SCHEMA,
            handler=sql_query_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=60,
            requires_permission=True,
        )
        logger.info("sql_query 工具注册成功")
    except Exception as e:
        logger.error(f"sql_query 工具注册失败: {e}")
        raise


# ── 自动注册 ────────────────────────────────────────────────────────────────

if __name__ != "__main__":
    register_sql_query_tool()
