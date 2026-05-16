"""
技术债 #2：工具注册失败无感知

测试 ToolRegistry.verify_expected_tools()：
- 全部预期工具注册成功 → 返回空缺失列表，不报 error
- 某个工具注册失败（缺失）→ 返回缺失列表，并 logger.error 明确报出缺哪个
"""

import logging

import pytest

from app.services.tool_registry import (
    ToolRegistry,
    EXPECTED_TOOL_NAMES,
    verify_expected_tools,
)
from app.models.tool import ToolCategory


VALID_SCHEMA = {"type": "object", "properties": {}}


def _dummy_handler(**kwargs):
    return {"success": True}


class TestVerifyExpectedTools:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        ToolRegistry._instance = None
        yield
        ToolRegistry._instance = None

    def _register_all(self, registry, skip=None):
        skip = skip or set()
        for name in EXPECTED_TOOL_NAMES:
            if name in skip:
                continue
            registry.register_tool(
                name=name,
                description=f"dummy {name}",
                json_schema=VALID_SCHEMA,
                handler=_dummy_handler,
                category=ToolCategory.DATA_QUERY,
            )

    def test_all_tools_registered_returns_no_missing(self, caplog):
        registry = ToolRegistry()
        self._register_all(registry)

        with caplog.at_level(logging.ERROR):
            missing = verify_expected_tools(registry)

        assert missing == []
        # 不应有 error 级日志
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_missing_tool_detected_and_logged(self, caplog):
        registry = ToolRegistry()
        # 故意不注册 query_timesheet，模拟其注册失败
        self._register_all(registry, skip={"query_timesheet"})

        with caplog.at_level(logging.ERROR):
            missing = verify_expected_tools(registry)

        assert "query_timesheet" in missing
        # 明确报出缺哪个
        error_logs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("query_timesheet" in m for m in error_logs), error_logs

    def test_multiple_missing_tools_all_reported(self, caplog):
        registry = ToolRegistry()
        self._register_all(registry, skip={"kb_outline", "save_workhour"})

        with caplog.at_level(logging.ERROR):
            missing = verify_expected_tools(registry)

        assert set(missing) >= {"kb_outline", "save_workhour"}
        error_logs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        joined = " ".join(error_logs)
        assert "kb_outline" in joined and "save_workhour" in joined
