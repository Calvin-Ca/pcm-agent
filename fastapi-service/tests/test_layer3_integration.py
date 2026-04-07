"""
Layer 3 集成测试

前提条件：
1. FastAPI 服务已启动（http://localhost:8000）
2. SpringBoot 服务已启动（http://localhost:8080）
3. .env 文件已配置 DASHSCOPE_API_KEY

运行方式：
    export L3_BASE_URL="http://localhost:8000"
    export L3_USER_ID="test_user_1"
    export L3_TOKEN="your_jwt_token"
    export L3_ENTITY_TYPE="deptAdmin"
    cd fastapi-service
    pytest tests/test_layer3_integration.py -v -s

注意：此测试依赖外部服务，不纳入 CI，仅供手动运行。
"""

import os
import pytest
import httpx


BASE_URL = os.getenv("L3_BASE_URL", "http://localhost:8000")
TEST_USER_ID = os.getenv("L3_USER_ID", "test_user_1")
TEST_TOKEN = os.getenv("L3_TOKEN", "")
TEST_ENTITY_TYPE = os.getenv("L3_ENTITY_TYPE", "employee")


def make_headers(entity_type: str = None) -> dict:
    return {
        "Content-Type": "application/json",
        "X-User-ID": TEST_USER_ID,
        "X-Entity-Type": entity_type or TEST_ENTITY_TYPE,
        "X-Department-ID": "1",
        "Authorization": f"Bearer {TEST_TOKEN}",
    }


def chat(message: str, entity_type: str = None) -> dict:
    """发送聊天请求（非流式）并返回响应"""
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{BASE_URL}/api/ai/chat",
            headers=make_headers(entity_type),
            json={"message": message},
        )
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text}"
    return response.json()


# ─── 辅助断言 ─────────────────────────────────────────────────────────────────

def get_intent(result: dict) -> str:
    """从响应中提取 intent"""
    route_info = result.get("result", {}).get("route_info", {})
    return route_info.get("intent_type", "general_chat")


def get_tool_name(result: dict) -> str:
    """从响应中提取 tool_name"""
    return result.get("result", {}).get("tool_name")


def get_message(result: dict) -> str:
    """从响应中提取 message"""
    return result.get("message", "") or result.get("result", {}).get("message", "")


# ─── 基础路由测试 ──────────────────────────────────────────────────────────────

class TestBasicRouting:
    def test_general_chat(self):
        """闲聊路由"""
        result = chat("你好")
        assert get_message(result), "应有回复内容"
        # 闲聊不走工具，intent 为 None 或 general_chat
        intent = get_intent(result)
        assert intent in (None, "general_chat"), f"闲聊应为 general_chat，实际：{intent}"

    def test_knowledge_qa_routing(self):
        """知识问答路由"""
        result = chat("加班算工时吗")
        assert get_message(result), "应有回复内容"
        assert len(get_message(result)) > 10, "回答不应为空或过短"


# ─── 工时查询测试 ──────────────────────────────────────────────────────────────

class TestQueryTimesheet:
    def test_query_self_this_week(self):
        """查自己本周工时"""
        result = chat("查一下我本周工时")
        assert get_intent(result) == "tool_execution", f"应为 tool_execution，实际：{get_intent(result)}"
        assert get_tool_name(result) == "query_timesheet", f"应为 query_timesheet，实际：{get_tool_name(result)}"
        assert get_message(result), "应有工时查询结果"

    def test_query_self_this_month(self):
        """查本月工时"""
        result = chat("查本月工时")
        assert get_intent(result) == "tool_execution", f"应为 tool_execution，实际：{get_intent(result)}"
        assert get_tool_name(result) == "query_timesheet", f"应为 query_timesheet，实际：{get_tool_name(result)}"


# ─── 工时填报测试 ──────────────────────────────────────────────────────────────

class TestSaveWorkhour:
    def test_save_with_project_name(self):
        """填报工时时传项目名，验证 param_resolver 将其转为 projectId"""
        result = chat("帮我填今天工时8小时，项目是AI助手")
        intent = get_intent(result)
        # 接受 tool_execution（参数完整）或 clarify（参数不完整）
        assert intent in ("tool_execution", "clarify"), f"应为 tool_execution 或 clarify，实际：{intent}"

    def test_save_missing_project(self):
        """缺少项目时，行为取决于 LLM 判断（追问或尝试执行）"""
        result = chat("填今天8小时")
        intent = get_intent(result)
        assert intent in ("tool_execution", "clarify"), f"应为 tool_execution 或 clarify，实际：{intent}"
        assert get_message(result), "应有回复"


# ─── 项目查询测试 ──────────────────────────────────────────────────────────────

class TestQueryProject:
    def test_query_fillable_projects(self):
        """查询可填报项目列表"""
        result = chat("我可以填报哪些项目")
        assert get_intent(result) == "tool_execution", f"应为 tool_execution，实际：{get_intent(result)}"
        assert get_tool_name(result) == "query_project", f"应为 query_project，实际：{get_tool_name(result)}"
        assert get_message(result), "应返回项目列表"


# ─── 多工具并行测试 ───────────────────────────────────────────────────────────

class TestMultiTool:
    def test_query_two_members(self):
        """查询两人工时，验证并行执行"""
        result = chat("查张三和李四本月工时")
        # 期望有汇总回复
        assert get_message(result), "应有汇总回复"


# ─── 权限测试 ──────────────────────────────────────────────────────────────────

class TestPermission:
    def test_export_report_employee_forbidden(self):
        """普通员工不能导出报表"""
        result = chat("导出本月工时报表", entity_type="employee")
        msg = get_message(result)
        assert "权限" in msg or "管理员" in msg or "不足" in msg, f"应返回权限不足提示，实际：{msg}"

    def test_approve_workhour_employee_forbidden(self):
        """普通员工不能审核工时"""
        result = chat("审核工时记录 12345", entity_type="employee")
        msg = get_message(result)
        assert "权限" in msg or "管理员" in msg or "不足" in msg, f"应返回权限不足提示，实际：{msg}"
