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
    from app.services.permission_validator import permission_validator
    from app.models.tool import ToolExecutionContext

    # 空 context fallback（测试环境或匿名用户）
    if not context:
        return "（无数据范围限制，匿名查询模式）"

    # 将 dict 或 ToolExecutionContext 转为 PermissionContext
    if isinstance(context, dict):
        # 补全缺失的必填字段，防止 ValidationError
        if not context.get("user_id"):
            context["user_id"] = "anonymous"
        if not context.get("entity_type"):
            context["entity_type"] = "employee"
        ctx = ToolExecutionContext(**context)
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

    question: str = kwargs.get("question", "")
    context: Dict[str, Any] = kwargs.get("context", {})
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
        table_schemas = await sql_engine.get_table_schemas(question=question)
    except Exception as e:
        logger.error(f"获取表结构失败: {e}")
        return {"success": False, "error": f"获取表结构失败: {e}"}

    # 3. 构建权限约束
    permission_constraints = _build_permission_constraints(context)

    # 4. LLM 生成 SQL
    #    prompt 结构优化：system 短指令 + user 紧凑上下文
    #    让 qwen3-8b 不会因 prompt 过长而"忘记"输出格式要求
    from datetime import date as _date
    pm = get_prompt_manager()
    sql_generation_prompt = pm.format(
        "sql_generation",
        table_schemas=table_schemas,
        permission_constraints=permission_constraints,
        user_question=question,
        user_id=user_id or "unknown",
        today=str(_date.today()),
    )

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

    final_sql = sql_or_error  # 通过校验的 SQL

    # 6. 执行 SQL（重试 1 次）
    query_results = []
    columns = []
    max_retries = 1

    for attempt in range(max_retries + 1):
        try:
            rows, columns = await sql_engine.execute_query(
                final_sql,
                timeout=settings.SQL_AGENT_QUERY_TIMEOUT,
                max_rows=settings.SQL_AGENT_MAX_ROWS,
            )
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
                "自定义 SQL 查询工具（兜底工具，仅当专用工具无法覆盖时使用）。"
                "专用工具清单：query_timesheet（工时明细查询）、compute_statistics（统计排名对比趋势）。"
                "sql_query 适用：多表 JOIN、窗口函数、漏填工时检测（work_calendar 与 workhour 差集）、"
                "加班统计（workhour_attendance.overtime_hours）、考勤异常、打卡时间、复杂条件筛选。"
                "sql_query 不适用：上述专用工具已覆盖的标准查询场景（工时明细、排名TopN、部门对比、总工时汇总等）。"
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
