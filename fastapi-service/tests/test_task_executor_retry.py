"""
技术债 #3：TaskExecutor._execute_tool_call 重试集成测试

通过真实的 TaskExecutor + ToolRegistry 验证：
- handler 前两次抛网络错、第三次成功 → 最终成功且 handler 被调用 3 次
- handler 抛权限错 → 不重试，handler 只被调用 1 次
"""

import httpx
import pytest

from app.services.task_executor import TaskExecutor
from app.services.tool_registry import ToolRegistry
from app.models.tool import ToolCategory
from app.models.task_plan import TaskNode, TaskType

pytestmark = pytest.mark.asyncio

SCHEMA = {"type": "object", "properties": {}}


@pytest.fixture(autouse=True)
def reset_registry():
    ToolRegistry._instance = None
    yield
    ToolRegistry._instance = None


async def test_handler_retries_on_network_error_then_succeeds():
    registry = ToolRegistry()
    calls = {"n": 0}

    async def flaky_handler(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return {"success": True, "data": "ok"}

    registry.register_tool(
        name="flaky_tool",
        description="dummy",
        json_schema=SCHEMA,
        handler=flaky_handler,
        category=ToolCategory.DATA_QUERY,
    )

    executor = TaskExecutor(tool_registry=registry)
    task = TaskNode(
        task_id="t1",
        task_type=TaskType.TOOL_CALL,
        tool_name="flaky_tool",
        parameters={},
        timeout=5,
    )

    result = await executor._execute_tool_call(task, permission_context=None)

    assert result["success"] is True
    assert result["result"] == {"success": True, "data": "ok"}
    assert calls["n"] == 3  # 初次 + 重试 2 次


async def test_permission_error_not_retried():
    registry = ToolRegistry()
    calls = {"n": 0}

    async def denied_handler(**kwargs):
        calls["n"] += 1
        raise PermissionError("无权限访问")

    registry.register_tool(
        name="denied_tool",
        description="dummy",
        json_schema=SCHEMA,
        handler=denied_handler,
        category=ToolCategory.DATA_QUERY,
    )

    executor = TaskExecutor(tool_registry=registry)
    task = TaskNode(
        task_id="t2",
        task_type=TaskType.TOOL_CALL,
        tool_name="denied_tool",
        parameters={},
        timeout=5,
    )

    with pytest.raises(PermissionError):
        await executor._execute_tool_call(task, permission_context=None)

    assert calls["n"] == 1  # 立即失败，不重试
