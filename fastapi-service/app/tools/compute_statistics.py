"""
Compute Statistics Tool - 统计计算工具

提供各种统计计算功能，包括用户工时汇总、项目工时汇总、部门统计等。
"""

import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, date
import httpx
from pydantic import BaseModel, Field
from enum import Enum

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.core.config import settings

logger = logging.getLogger(__name__)


class StatisticsType(str, Enum):
    """统计类型枚举"""
    USER_HOURS = "user_hours"  # 用户工时汇总
    PROJECT_HOURS = "project_hours"  # 项目工时汇总
    DEPARTMENT_HOURS = "department_hours"  # 部门工时汇总
    DAILY_HOURS = "daily_hours"  # 每日工时统计
    WEEKLY_HOURS = "weekly_hours"  # 每周工时统计
    MONTHLY_HOURS = "monthly_hours"  # 每月工时统计


class StatisticsQueryParams(BaseModel):
    """统计查询参数"""
    statistics_type: StatisticsType = Field(..., description="统计类型")
    start_date: str = Field(..., description="开始日期 (YYYY-MM-DD)")
    end_date: str = Field(..., description="结束日期 (YYYY-MM-DD)")
    user_id: Optional[str] = Field(None, description="用户ID（可选）")
    project_id: Optional[str] = Field(None, description="项目ID（可选）")
    department_id: Optional[str] = Field(None, description="部门ID（可选）")
    work_type: Optional[str] = Field(None, description="工时类型筛选（可选），如'其他工时'表示加班工时。不填则统计全部工时。")


class StatisticsItem(BaseModel):
    """统计项"""
    id: str
    name: str
    total_hours: float
    work_days: int
    average_daily_hours: float
    details: Dict[str, Any]


class StatisticsResult(BaseModel):
    """统计结果"""
    success: bool
    statistics_type: str
    date_range: str
    total_hours: float
    total_records: int
    items: List[StatisticsItem]
    summary: Dict[str, Any]
    error: Optional[str] = None


# JSON Schema定义
COMPUTE_STATISTICS_SCHEMA = {
    "type": "object",
    "properties": {
        "statistics_type": {
            "type": "string",
            "enum": ["user_hours", "project_hours", "department_hours", "daily_hours", "weekly_hours", "monthly_hours"],
            "description": "统计类型：user_hours(用户工时), project_hours(项目工时), department_hours(部门工时), daily_hours(每日工时), weekly_hours(每周工时), monthly_hours(每月工时)"
        },
        "start_date": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "description": "开始日期，格式：YYYY-MM-DD"
        },
        "end_date": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "description": "结束日期，格式：YYYY-MM-DD"
        },
        "user_id": {
            "type": "string",
            "description": "用户ID（可选），用于筛选特定用户"
        },
        "project_id": {
            "type": "string",
            "description": "项目ID（可选），用于筛选特定项目"
        },
        "department_id": {
            "type": "string",
            "description": "部门ID（可选），用于筛选特定部门"
        },
        "work_type": {
            "type": "string",
            "description": "工时类型筛选（可选），如'其他工时'表示加班工时。不填则统计全部工时。"
        }
    },
    "required": ["statistics_type", "start_date", "end_date"],
    "additionalProperties": False
}


async def compute_statistics_handler(**kwargs) -> Dict[str, Any]:
    """
    统计计算处理函数
    
    Args:
        **kwargs: 查询参数
        
    Returns:
        Dict[str, Any]: 统计结果
    """
    try:
        # 提取非业务参数，避免污染 StatisticsQueryParams
        auth_token = kwargs.pop("auth_token", None)
        kwargs.pop("context", None)

        # 构建 Authorization header
        headers = {}
        if auth_token:
            token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
            headers["Authorization"] = token

        # 参数验证和解析
        params = StatisticsQueryParams(**kwargs)

        # 验证日期格式和逻辑
        start_date = datetime.strptime(params.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d").date()

        if start_date > end_date:
            return {
                "success": False,
                "error": "开始日期不能晚于结束日期",
                "statistics_type": params.statistics_type,
                "date_range": f"{params.start_date} 至 {params.end_date}",
                "total_hours": 0,
                "total_records": 0,
                "items": [],
                "summary": {}
            }

        # 根据统计类型调用不同的处理函数
        if params.statistics_type == StatisticsType.USER_HOURS:
            result = await _compute_user_hours_statistics(params, start_date, end_date, headers)
        elif params.statistics_type == StatisticsType.PROJECT_HOURS:
            result = await _compute_project_hours_statistics(params, start_date, end_date, headers)
        elif params.statistics_type == StatisticsType.DEPARTMENT_HOURS:
            result = await _compute_department_hours_statistics(params, start_date, end_date, headers)
        elif params.statistics_type == StatisticsType.DAILY_HOURS:
            result = await _compute_daily_hours_statistics(params, start_date, end_date, headers)
        elif params.statistics_type == StatisticsType.WEEKLY_HOURS:
            result = await _compute_weekly_hours_statistics(params, start_date, end_date, headers)
        elif params.statistics_type == StatisticsType.MONTHLY_HOURS:
            result = await _compute_monthly_hours_statistics(params, start_date, end_date, headers)
        else:
            return {
                "success": False,
                "error": f"不支持的统计类型: {params.statistics_type}",
                "statistics_type": params.statistics_type,
                "date_range": f"{params.start_date} 至 {params.end_date}",
                "total_hours": 0,
                "total_records": 0,
                "items": [],
                "summary": {}
            }
        
        return result
        
    except ValueError as e:
        logger.error(f"参数验证失败: {str(e)}")
        return {
            "success": False,
            "error": f"参数格式错误: {str(e)}",
            "statistics_type": kwargs.get("statistics_type", "unknown"),
            "date_range": f"{kwargs.get('start_date', '')} 至 {kwargs.get('end_date', '')}",
            "total_hours": 0,
            "total_records": 0,
            "items": [],
            "summary": {}
        }
    except Exception as e:
        logger.error(f"统计计算异常: {str(e)}")
        return {
            "success": False,
            "error": f"计算异常: {str(e)}",
            "statistics_type": kwargs.get("statistics_type", "unknown"),
            "date_range": f"{kwargs.get('start_date', '')} 至 {kwargs.get('end_date', '')}",
            "total_hours": 0,
            "total_records": 0,
            "items": [],
            "summary": {}
        }


async def _fetch_workhour_records(
    params: StatisticsQueryParams, headers: Dict[str, str] = None
) -> List[Dict[str, Any]]:
    """通过 /api/workhour/by-date-range 获取原始工时记录，供各统计类型统一使用。"""
    base_url = settings.SPRINGBOOT_BASE_URL
    url = f"{base_url}/api/workhour/by-date-range"

    query_params = {
        "startDate": params.start_date,
        "endDate": params.end_date,
    }
    if params.user_id:
        query_params["memberId"] = params.user_id
    if params.project_id:
        query_params["projectId"] = params.project_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params, headers=headers or {})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            data = data.get("data", [])
        return data if isinstance(data, list) else []


def _filter_by_work_type(records: List[Dict[str, Any]], work_type: Optional[str]) -> List[Dict[str, Any]]:
    """按工时类型筛选记录（支持 workType / work_type 两种字段名）。"""
    if not work_type:
        return records
    return [
        r for r in records
        if r.get("workType") == work_type or r.get("work_type") == work_type
    ]


async def _compute_user_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算用户工时统计（基于原始记录聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    user_map = defaultdict(lambda: {"total_hours": 0.0, "work_days": set(), "projects": set()})

    for r in records:
        uid = r.get("memberId") or r.get("userId") or "unknown"
        user_map[uid]["total_hours"] += float(r.get("workhour", 0) or 0)
        user_map[uid]["work_days"].add(str(r.get("workhourDate", ""))[:10])
        user_map[uid]["projects"].add(r.get("projectName", ""))

    items = []
    total_hours = 0.0
    for uid, info in sorted(user_map.items(), key=lambda x: -x[1]["total_hours"]):
        item = StatisticsItem(
            id=uid,
            name=uid,
            total_hours=round(info["total_hours"], 2),
            work_days=len(info["work_days"]),
            average_daily_hours=round(info["total_hours"] / max(1, len(info["work_days"])), 2),
            details={"projects": list(info["projects"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "user_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_hours_per_user": round(total_hours / max(1, len(items)), 2),
            "total_work_days": sum(i.work_days for i in items),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


async def _compute_project_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算项目工时统计（基于原始记录聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    proj_map = defaultdict(lambda: {"total_hours": 0.0, "work_days": set(), "users": set()})

    for r in records:
        pid = r.get("projectId") or r.get("projectName", "unknown")
        pname = r.get("projectName", pid)
        proj_map[pid]["name"] = pname
        proj_map[pid]["total_hours"] += float(r.get("workhour", 0) or 0)
        proj_map[pid]["work_days"].add(str(r.get("workhourDate", ""))[:10])
        proj_map[pid]["users"].add(r.get("memberId", ""))

    items = []
    total_hours = 0.0
    for pid, info in sorted(proj_map.items(), key=lambda x: -x[1]["total_hours"]):
        item = StatisticsItem(
            id=pid,
            name=info.get("name", pid),
            total_hours=round(info["total_hours"], 2),
            work_days=len(info["work_days"]),
            average_daily_hours=round(info["total_hours"] / max(1, len(info["work_days"])), 2),
            details={"user_count": len(info["users"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "project_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_hours_per_project": round(total_hours / max(1, len(items)), 2),
            "total_work_days": sum(i.work_days for i in items),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


async def _compute_department_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算部门工时统计（基于原始记录聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    dept_map = defaultdict(lambda: {"total_hours": 0.0, "work_days": set(), "users": set()})

    for r in records:
        dept = r.get("orgName") or r.get("departmentName") or r.get("deptId", "未知部门")
        dept_map[dept]["total_hours"] += float(r.get("workhour", 0) or 0)
        dept_map[dept]["work_days"].add(str(r.get("workhourDate", ""))[:10])
        dept_map[dept]["users"].add(r.get("memberId", ""))

    items = []
    total_hours = 0.0
    for dept, info in sorted(dept_map.items(), key=lambda x: -x[1]["total_hours"]):
        item = StatisticsItem(
            id=dept,
            name=dept,
            total_hours=round(info["total_hours"], 2),
            work_days=len(info["work_days"]),
            average_daily_hours=round(info["total_hours"] / max(1, len(info["work_days"])), 2),
            details={"user_count": len(info["users"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "department_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_hours_per_department": round(total_hours / max(1, len(items)), 2),
            "total_work_days": sum(i.work_days for i in items),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


async def _compute_daily_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算每日工时统计（基于原始记录聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    day_map = defaultdict(lambda: {"total_hours": 0.0, "users": set(), "projects": set()})

    for r in records:
        d = str(r.get("workhourDate", ""))[:10]
        if not d:
            continue
        day_map[d]["total_hours"] += float(r.get("workhour", 0) or 0)
        day_map[d]["users"].add(r.get("memberId", ""))
        day_map[d]["projects"].add(r.get("projectName", ""))

    items = []
    total_hours = 0.0
    for d in sorted(day_map.keys()):
        info = day_map[d]
        item = StatisticsItem(
            id=d,
            name=d,
            total_hours=round(info["total_hours"], 2),
            work_days=1,
            average_daily_hours=round(info["total_hours"], 2),
            details={"user_count": len(info["users"]), "project_count": len(info["projects"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "daily_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_daily_hours": round(total_hours / max(1, len(items)), 2),
            "max_daily_hours": max([i.total_hours for i in items], default=0),
            "min_daily_hours": min([i.total_hours for i in items], default=0),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


async def _compute_weekly_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算每周工时统计（基于原始记录按周聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    week_map = defaultdict(lambda: {"total_hours": 0.0, "work_days": set(), "users": set()})

    for r in records:
        d_str = str(r.get("workhourDate", ""))[:10]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        # ISO 周: YYYY-WNN
        week_key = d.strftime("%Y-W%W")
        week_map[week_key]["total_hours"] += float(r.get("workhour", 0) or 0)
        week_map[week_key]["work_days"].add(d_str)
        week_map[week_key]["users"].add(r.get("memberId", ""))

    items = []
    total_hours = 0.0
    for wk in sorted(week_map.keys()):
        info = week_map[wk]
        item = StatisticsItem(
            id=wk,
            name=wk,
            total_hours=round(info["total_hours"], 2),
            work_days=len(info["work_days"]),
            average_daily_hours=round(info["total_hours"] / max(1, len(info["work_days"])), 2),
            details={"user_count": len(info["users"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "weekly_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_hours_per_week": round(total_hours / max(1, len(items)), 2),
            "total_work_days": sum(i.work_days for i in items),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


async def _compute_monthly_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date, headers: Dict[str, str] = None) -> Dict[str, Any]:
    """计算每月工时统计（基于原始记录按月聚合）"""
    records = await _fetch_workhour_records(params, headers)
    records = _filter_by_work_type(records, params.work_type)

    from collections import defaultdict
    month_map = defaultdict(lambda: {"total_hours": 0.0, "work_days": set(), "users": set()})

    for r in records:
        d_str = str(r.get("workhourDate", ""))[:10]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        month_key = d.strftime("%Y-%m")
        month_map[month_key]["total_hours"] += float(r.get("workhour", 0) or 0)
        month_map[month_key]["work_days"].add(d_str)
        month_map[month_key]["users"].add(r.get("memberId", ""))

    items = []
    total_hours = 0.0
    for mk in sorted(month_map.keys()):
        info = month_map[mk]
        item = StatisticsItem(
            id=mk,
            name=mk,
            total_hours=round(info["total_hours"], 2),
            work_days=len(info["work_days"]),
            average_daily_hours=round(info["total_hours"] / max(1, len(info["work_days"])), 2),
            details={"user_count": len(info["users"])},
        )
        items.append(item)
        total_hours += info["total_hours"]

    return {
        "success": True,
        "statistics_type": "monthly_hours",
        "date_range": f"{params.start_date} 至 {params.end_date}",
        "total_hours": round(total_hours, 2),
        "total_records": len(items),
        "items": [item.dict() for item in items],
        "summary": {
            "average_hours_per_month": round(total_hours / max(1, len(items)), 2),
            "total_work_days": sum(i.work_days for i in items),
            "date_range_days": (end_date - start_date).days + 1,
        },
    }


def register_compute_statistics_tool():
    """注册统计计算工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="compute_statistics",
            description="对工时数据进行汇总统计与排名分析，返回合计、均值、排名、趋势等聚合数据。支持'本月''上周''上月''本季度'等模糊时间描述（自动推断日期范围）。适用于：统计总工时、部门/人员工时排名、项目工时占比、月度/季度趋势分析、工时对比、TopN排名（如工时最多的前5人）。不返回明细条目。【重要】加班时长/加班统计 不适用此工具，加班数据在 workhour_attendance.overtime_hours，应使用 sql_query 查询。",
            json_schema=COMPUTE_STATISTICS_SCHEMA,
            handler=compute_statistics_handler,
            category=ToolCategory.STATISTICS,
            timeout=60,
            requires_permission=True
        )
        logger.info("统计计算工具注册成功")
    except Exception as e:
        logger.error(f"统计计算工具注册失败: {str(e)}")
        raise


# 自动注册工具（当模块被导入时）
if __name__ != "__main__":
    register_compute_statistics_tool()