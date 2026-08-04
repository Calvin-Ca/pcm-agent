"""SQL Agent 安全链路的 F5 教学服务。

真实执行：HTTP 路由、LangGraph、TaskExecutor、PermissionValidator、SQL Prompt、
validate_sql。Mock：Function Calling/SQL LLM 的返回值、数据库 schema 与查询结果。

本文件只用于本地断点学习，不被 main.py 或生产服务导入。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# 直接运行 scripts/*.py 时，sys.path[0] 是 scripts/；补入 fastapi-service/。
SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

# 必须在 app.core.config 首次导入前设置，避免调试请求写审计库或上报链路。
os.environ["CONVERSATION_LOG_ENABLED"] = "false"
os.environ["LANGFUSE_ENABLED"] = "false"

from fastapi import FastAPI
import uvicorn

from app.api.chat import initialize_chat_components, router as chat_router
from app.services.permission_validator import PermissionValidator
from app.services.tool_registry import ToolRegistry
from app.services.sql_engine import sql_engine
from app.tools import sql_query as sql_query_module


class DebugPromptBuilder:
    """提供最小对话历史，让真实 Function Calling 节点能够运行。"""

    async def build_messages_with_history(
        self,
        user_message: str,
        session_id: str,
        user_id: str | None,
        base_system_prompt: str,
    ) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": base_system_prompt},
            {"role": "user", "content": user_message},
        ]


class DebugFunctionCallingLLM:
    """首轮固定选择 sql_query，拿到 observation 后结束 Agent Loop。"""

    api_base = "debug://local"
    model = "debug-function-calling"

    async def generate_with_tools(self, messages, tools, **kwargs):
        has_tool_observation = any(message.get("role") == "tool" for message in messages)
        if has_tool_observation:
            return {
                "finish_reason": "stop",
                "content": "SQL Agent 调试链路已完成。",
                "tool_calls": [],
            }

        question = next(
            (message.get("content", "") for message in reversed(messages)
             if message.get("role") == "user"),
            "",
        )
        return {
            "finish_reason": "tool_calls",
            "content": None,
            "tool_calls": [
                {"name": "sql_query", "arguments": {"question": question}}
            ],
        }

    async def generate(self, *args, **kwargs):
        return "调试用自然语言摘要。"


class DebugSQLAgentLLM:
    """按请求内容返回确定 SQL，方便分别观察语义辅助层和硬规则层。"""

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        prompt = "\n".join(str(message.get("content", "")) for message in messages)

        # 显式触发硬规则：模拟 LLM 生成危险 SQL，随后应被 validate_sql 拦截。
        if "演示硬规则" in prompt:
            return "DELETE FROM workhour WHERE member_id = 'debug-user'"

        # 提示注入/越权请求：模拟语义层把恶意意图改写成受限 SELECT。
        # 这是辅助行为，不应被当作可靠安全边界。
        if "忽略权限" in prompt or "所有人的工时" in prompt:
            return (
                "SELECT workhour.member_id AS 用户ID, "
                "SUM(workhour.duration) AS 总工时 "
                "FROM workhour "
                "WHERE workhour.member_id = 'debug-user' "
                "GROUP BY workhour.member_id LIMIT 100"
            )

        # 正常 employee 查询：主动遵守 Prompt 中注入的当前用户条件。
        user_match = re.search(r"workhour\.member_id IN \('([^']+)'\)", prompt)
        user_id = user_match.group(1) if user_match else "debug-user"
        return (
            "SELECT workhour.member_id AS 用户ID, "
            "SUM(workhour.duration) AS 总工时 "
            "FROM workhour "
            f"WHERE workhour.member_id = '{user_id}' "
            "GROUP BY workhour.member_id LIMIT 100"
        )


async def _debug_table_schemas(question: str = "") -> str:
    return (
        "workhour(id, member_id, project_id, duration, workhour_date); "
        "sys_user(id, entity_name, org_id)"
    )


async def _debug_execute_query(
    sql: str, timeout: int = 30, max_rows: int = 500
) -> tuple[List[Dict[str, Any]], List[str]]:
    # 断在这里可检查：只有 validate_sql 返回安全的 SQL 才能抵达执行层。
    return ([{"用户ID": "debug-user", "总工时": 40.0}], ["用户ID", "总工时"])


def create_debug_app() -> FastAPI:
    # SQL 工具模块导入时已注册到单例 ToolRegistry；这里复用真实注册信息和 handler。
    registry = ToolRegistry()
    permission_validator = PermissionValidator()

    # 只替换外部依赖。sql_query_handler、权限条件构造、Prompt 和硬规则均为真实代码。
    sql_query_module.SQLAgentLLMClient = DebugSQLAgentLLM
    sql_engine._engine = object()  # 跳过真实连接池初始化
    sql_engine.get_table_schemas = _debug_table_schemas
    sql_engine.execute_query = _debug_execute_query

    initialize_chat_components(
        tool_reg=registry,
        perm_validator=permission_validator,
        llm_client=DebugFunctionCallingLLM(),
        prompt_builder=DebugPromptBuilder(),
    )

    app = FastAPI(title="SQL Agent Security Chain Debug")
    app.include_router(chat_router, prefix="/api")

    @app.get("/debug/info")
    async def debug_info():
        return {
            "mode": "mock-external-dependencies",
            "real_layers": [
                "HTTP identity resolution",
                "LangGraph Function Calling route",
                "TaskExecutor context injection",
                "PermissionValidator data filter",
                "SQL prompt construction",
                "validate_sql hard rules",
            ],
            "mocked": ["LLM responses", "database schema/query"],
        }

    return app


app = create_debug_app()


if __name__ == "__main__":
    print("\nSQL Agent 安全链路调试服务：http://127.0.0.1:8010/docs")
    print("按 docs_caich/02_sql_agent_security_f5.md 设置断点并发送请求。\n")
    uvicorn.run(app, host="127.0.0.1", port=8010, reload=False)
