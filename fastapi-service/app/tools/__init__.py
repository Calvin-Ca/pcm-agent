"""
Tools package - 工具包

包含所有可用的AI工具实现。
"""

# 导入所有工具模块以触发自动注册
from . import query_timesheet
from . import query_project
from . import compute_statistics
from . import generate_weekly_report
from . import save_workhour

__all__ = [
    "query_timesheet",
    "query_project",
    "compute_statistics",
    "generate_weekly_report",
    "save_workhour",
]