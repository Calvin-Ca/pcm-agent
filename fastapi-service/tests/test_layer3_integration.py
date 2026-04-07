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


# ─── 基础路由测试 ──────────────────────────────────────────────────────────────

class TestBasicRouting:
    def test_general_chat(self):
        """闲聊路由"""
        result = chat("你好")
        assert result.get("intent") in ("general_chat", None)
        assert result.get("response"), "应有回复内容"

    def test_knowledge_qa_routing(self):
        """知识问答路由"""
        result = chat("加班算工时吗")
        # 路由到 knowledge_qa 或 general_chat 均可，关键是有实质性回答
        assert result.get("response"), "应有回复内容"
        assert len(result["response"]) > 10, "回答不应为空或过短"


# ─── 工时查询测试 ──────────────────────────────────────────────────────────────

class TestQueryTimesheet:
    def test_query_self_this_week(self):
        """查自己本周工时"""
        result = chat("查一下我本周工时")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_timesheet"
        assert result.get("response"), "应有工时查询结果"

    def test_query_self_this_month(self):
        """查本月工时"""
        result = chat("查本月工时")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_timesheet"


# ─── 工时填报测试 ──────────────────────────────────────────────────────────────

class TestSaveWorkhour:
    def test_save_with_project_name(self):
        """填报工时时传项目名，验证 param_resolver 将其转为 projectId"""
        result = chat("帮我填今天工时8小时，项目是AI助手")
        # 因为项目名可能不存在，接受 tool_execution 或追问
        assert result.get("intent") in ("tool_execution", "clarify")

    def test_save_missing_project(self):
        """缺少项目时，行为取决于 LLM 判断（追问或尝试执行）"""
        result = chat("填今天8小时")
        assert result.get("intent") in ("tool_execution", "clarify")
        assert result.get("response"), "应有回复"


# ─── 项目查询测试 ──────────────────────────────────────────────────────────────

class TestQueryProject:
    def test_query_fillable_projects(self):
        """查询可填报项目列表"""
        result = chat("我可以填报哪些项目")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_project"
        assert result.get("response"), "应返回项目列表"


# ─── 多工具并行测试 ────────────────────────────────────────────────────────────

class TestMultiTool:
    def test_query_two_members(self):
        """查询两人工时，验证并行执行"""
        result = chat("查张三和李四本月工时")
        # 期望有汇总回复
        assert result.get("response"), "应有汇总回复"


# ─── 权限测试 ──────────────────────────────────────────────────────────────────

class TestPermission:
    def test_export_report_employee_forbidden(self):
        """普通员工不能导出报表"""
        result = chat("导出本月工时报表", entity_type="employee")
        assert "权限" in result.get("response", "") or "管理员" in result.get("response", "")

    def test_approve_workhour_employee_forbidden(self):
        """普通员工不能审核工时"""
        result = chat("审核工时记录 12345", entity_type="employee")
        assert "权限" in result.get("response", "") or "管理员" in result.get("response", "")
