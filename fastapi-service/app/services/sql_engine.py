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
from typing import Any, Dict, List, Optional, Tuple
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 允许访问的表（白名单）
ALLOWED_TABLES = {
    "workhour": "工时记录表（核心表）",
    "project_info": "项目信息表",
    "sys_user": "用户/员工表",
    "workhour_attendance": "考勤记录表",
    "org_dept": "部门/组织表",
    "work_calendar": "工作日历表",
    "project_member": "项目成员关联表",
}

# 敏感列黑名单（禁止在 SQL 中出现）
BLOCKED_COLUMNS = {
    "sys_user": ["password", "password_hash", "salt", "id_card", "phone", "email"],
}

# ── 极简 schema（单行格式，减少 token 消耗）────────────────────────────────
# 格式: "table(col1, col2 FK→ref, ...)"  每张表一行
COMPACT_SCHEMAS = {
    "workhour": "workhour(id PK, member_id FK→sys_user.id, workhour_date datetime, workhour decimal 小时数, project_id FK→project_info.id, work_content 工作内容, is_deleted 0未删/1已删)",
    "project_info": "project_info(id PK, project_name 项目名称, manager_id 项目经理ID, manager_name 项目经理姓名)",
    "sys_user": "sys_user(id PK, entity_name 员工姓名, dept_id FK→org_dept.id)",
    "org_dept": "org_dept(id PK, dept_name 部门名称)",
    "workhour_attendance": "workhour_attendance(id PK, member_id FK→sys_user.id, attendance_date date, status 正常/迟到/早退)",
    "work_calendar": "work_calendar(id PK, calendar_date date, is_workday 1是/0否)",
    "project_member": "project_member(id PK, project_id FK→project_info.id, member_id FK→sys_user.id, join_date date)",
}

# ── 动态表选择：关键词 → 相关表映射 ─────────────────────────────────────────
# 核心表 workhour + sys_user 始终包含，其余按关键词命中追加
TABLE_KEYWORDS = {
    "project_info": ["项目", "project", "哪些项目", "项目名"],
    "org_dept": ["部门", "dept", "部", "科室", "组"],
    "workhour_attendance": ["考勤", "打卡", "迟到", "早退", "缺勤", "attendance"],
    "work_calendar": ["工作日", "节假日", "日历", "休息日", "周末"],
    "project_member": ["项目成员", "参与人", "项目组", "哪些人参与"],
}

# 始终包含的核心表
CORE_TABLES = {"workhour", "sys_user"}


def select_relevant_tables(question: str) -> list:
    """
    根据用户问题关键词，选择相关的表。
    核心表（workhour, sys_user）始终包含，其余按关键词命中追加。
    """
    tables = set(CORE_TABLES)
    question_lower = question.lower()
    for table, keywords in TABLE_KEYWORDS.items():
        if any(kw in question_lower for kw in keywords):  # 每个表枚举一些关键词
            tables.add(table)
    return sorted(tables)


def build_compact_schema(tables: list) -> str:
    """构建紧凑的表结构文本，只包含相关表。"""
    lines = []
    for t in tables:
        if t in COMPACT_SCHEMAS:
            lines.append(COMPACT_SCHEMAS[t])
    return "\n".join(lines)


def build_db_url() -> str:
    """构建数据库连接 URL"""
    host = settings.SQL_AGENT_DB_HOST or settings.MYSQL_HOST
    port = settings.SQL_AGENT_DB_PORT or settings.MYSQL_PORT
    db = settings.SQL_AGENT_DB_NAME or settings.MYSQL_DATABASE
    user = settings.SQL_AGENT_DB_USER or settings.MYSQL_USER
    password = settings.SQL_AGENT_DB_PASSWORD or settings.MYSQL_PASSWORD
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4"


class SQLEngine:
    def __init__(self):
        self._engine = None
        self._schema_cache = {}
        self._schema_cache_time = 0
        self._schema_ttl = 3600  # 1小时

    async def initialize(self):
        """初始化连接池"""
        db_url = build_db_url()
        self._engine = create_async_engine(
            db_url,
            pool_size=5,
            max_overflow=2,
            pool_recycle=3600,
            echo=False,
        )
        logger.info("SQLEngine initialized")

    async def get_table_schemas(self, question: str = "") -> str:
        """
        获取表结构文本（紧凑格式）。
        如果传入 question，则动态选择相关表（减少 token）；
        否则返回全部表。
        """
        if question:
            tables = select_relevant_tables(question) 
        else:
            tables = sorted(COMPACT_SCHEMAS.keys())
        return build_compact_schema(tables)

    async def execute_query(
        self, sql: str, timeout: int = 30, max_rows: int = 500,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict], List[str]]:
        """
        执行 SELECT 查询。
        - timeout：查询超时秒数
        - max_rows：最大返回行数
        返回 (rows, column_names)
        """
        if not self._engine:
            raise RuntimeError("SQLEngine not initialized")

        # 自动追加 LIMIT
        sql = sql.strip()
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {max_rows}"

        try:
            async with self._engine.connect() as conn:
                result = await asyncio.wait_for(
                    conn.execute(text(sql), parameters or {}),
                    timeout=timeout
                )
                rows = result.fetchall()
                columns = list(result.keys())

                # 转换为字典列表
                row_dicts = [dict(zip(columns, row)) for row in rows]
                return row_dicts, columns
        except asyncio.TimeoutError:
            raise Exception(f"查询超时（{timeout}秒），请缩小查询范围")
        except Exception as e:
            logger.error(f"SQL 执行失败: {e}, SQL: {sql}")
            raise

    async def close(self):
        """关闭连接池"""
        if self._engine:
            await self._engine.dispose()
            logger.info("SQLEngine closed")


# 全局单例
sql_engine = SQLEngine()
