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
            result = await _compute_user_hours_statistics(params, start_date, end_date)
        elif params.statistics_type == StatisticsType.PROJECT_HOURS:
            result = await _compute_project_hours_statistics(params, start_date, end_date)
        elif params.statistics_type == StatisticsType.DEPARTMENT_HOURS:
            result = await _compute_department_hours_statistics(params, start_date, end_date)
        elif params.statistics_type == StatisticsType.DAILY_HOURS:
            result = await _compute_daily_hours_statistics(params, start_date, end_date)
        elif params.statistics_type == StatisticsType.WEEKLY_HOURS:
            result = await _compute_weekly_hours_statistics(params, start_date, end_date)
        elif params.statistics_type == StatisticsType.MONTHLY_HOURS:
            result = await _compute_monthly_hours_statistics(params, start_date, end_date)
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


async def _compute_user_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算用户工时统计"""
    base_url = "http://localhost:8080"
    url = f"{base_url}/api/statistics/user-hours"
    
    query_params = {
        "startDate": params.start_date,
        "endDate": params.end_date
    }
    
    if params.project_id:
        query_params["projectId"] = params.project_id
    if params.department_id:
        query_params["departmentId"] = params.department_id
    if params.user_id:
        query_params["userId"] = params.user_id
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params)
        response.raise_for_status()
        
        api_data = response.json()
        if not api_data.get("success", False):
            raise Exception(api_data.get("message", "API调用失败"))
        
        data = api_data.get("data", [])
        items = []
        total_hours = 0.0
        
        for item_data in data:
            item = StatisticsItem(
                id=item_data["userId"],
                name=item_data.get("userName", "未知用户"),
                total_hours=float(item_data["totalHours"]),
                work_days=int(item_data.get("workDays", 0)),
                average_daily_hours=float(item_data.get("averageDailyHours", 0.0)),
                details={
                    "department": item_data.get("department", ""),
                    "projects": item_data.get("projects", [])
                }
            )
            items.append(item)
            total_hours += item.total_hours
        
        return {
            "success": True,
            "statistics_type": "user_hours",
            "date_range": f"{params.start_date} 至 {params.end_date}",
            "total_hours": round(total_hours, 2),
            "total_records": len(items),
            "items": [item.dict() for item in items],
            "summary": {
                "average_hours_per_user": round(total_hours / max(1, len(items)), 2),
                "total_work_days": sum(item.work_days for item in items),
                "date_range_days": (end_date - start_date).days + 1
            }
        }


async def _compute_project_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算项目工时统计"""
    base_url = "http://localhost:8080"
    url = f"{base_url}/api/statistics/project-hours"
    
    query_params = {
        "startDate": params.start_date,
        "endDate": params.end_date
    }
    
    if params.user_id:
        query_params["userId"] = params.user_id
    if params.department_id:
        query_params["departmentId"] = params.department_id
    if params.project_id:
        query_params["projectId"] = params.project_id
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params)
        response.raise_for_status()
        
        api_data = response.json()
        if not api_data.get("success", False):
            raise Exception(api_data.get("message", "API调用失败"))
        
        data = api_data.get("data", [])
        items = []
        total_hours = 0.0
        
        for item_data in data:
            item = StatisticsItem(
                id=item_data["projectId"],
                name=item_data.get("projectName", "未知项目"),
                total_hours=float(item_data["totalHours"]),
                work_days=int(item_data.get("workDays", 0)),
                average_daily_hours=float(item_data.get("averageDailyHours", 0.0)),
                details={
                    "status": item_data.get("status", ""),
                    "members": item_data.get("members", []),
                    "progress": item_data.get("progress", 0.0)
                }
            )
            items.append(item)
            total_hours += item.total_hours
        
        return {
            "success": True,
            "statistics_type": "project_hours",
            "date_range": f"{params.start_date} 至 {params.end_date}",
            "total_hours": round(total_hours, 2),
            "total_records": len(items),
            "items": [item.dict() for item in items],
            "summary": {
                "average_hours_per_project": round(total_hours / max(1, len(items)), 2),
                "total_work_days": sum(item.work_days for item in items),
                "date_range_days": (end_date - start_date).days + 1
            }
        }


async def _compute_department_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算部门工时统计"""
    base_url = "http://localhost:8080"
    url = f"{base_url}/api/statistics/department-hours"
    
    query_params = {
        "startDate": params.start_date,
        "endDate": params.end_date
    }
    
    if params.department_id:
        query_params["departmentId"] = params.department_id
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params)
        response.raise_for_status()
        
        api_data = response.json()
        if not api_data.get("success", False):
            raise Exception(api_data.get("message", "API调用失败"))
        
        data = api_data.get("data", [])
        items = []
        total_hours = 0.0
        
        for item_data in data:
            item = StatisticsItem(
                id=item_data["departmentId"],
                name=item_data.get("departmentName", "未知部门"),
                total_hours=float(item_data["totalHours"]),
                work_days=int(item_data.get("workDays", 0)),
                average_daily_hours=float(item_data.get("averageDailyHours", 0.0)),
                details={
                    "member_count": item_data.get("memberCount", 0),
                    "projects": item_data.get("projects", [])
                }
            )
            items.append(item)
            total_hours += item.total_hours
        
        return {
            "success": True,
            "statistics_type": "department_hours",
            "date_range": f"{params.start_date} 至 {params.end_date}",
            "total_hours": round(total_hours, 2),
            "total_records": len(items),
            "items": [item.dict() for item in items],
            "summary": {
                "average_hours_per_department": round(total_hours / max(1, len(items)), 2),
                "total_work_days": sum(item.work_days for item in items),
                "date_range_days": (end_date - start_date).days + 1
            }
        }


async def _compute_daily_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算每日工时统计"""
    base_url = "http://localhost:8080"
    url = f"{base_url}/api/statistics/daily-hours"
    
    query_params = {
        "startDate": params.start_date,
        "endDate": params.end_date
    }
    
    if params.user_id:
        query_params["userId"] = params.user_id
    if params.project_id:
        query_params["projectId"] = params.project_id
    if params.department_id:
        query_params["departmentId"] = params.department_id
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params)
        response.raise_for_status()
        
        api_data = response.json()
        if not api_data.get("success", False):
            raise Exception(api_data.get("message", "API调用失败"))
        
        data = api_data.get("data", [])
        items = []
        total_hours = 0.0
        
        for item_data in data:
            item = StatisticsItem(
                id=item_data["date"],
                name=f"{item_data['date']} ({item_data.get('weekday', '')})",
                total_hours=float(item_data["totalHours"]),
                work_days=1,
                average_daily_hours=float(item_data["totalHours"]),
                details={
                    "user_count": item_data.get("userCount", 0),
                    "project_count": item_data.get("projectCount", 0)
                }
            )
            items.append(item)
            total_hours += item.total_hours
        
        return {
            "success": True,
            "statistics_type": "daily_hours",
            "date_range": f"{params.start_date} 至 {params.end_date}",
            "total_hours": round(total_hours, 2),
            "total_records": len(items),
            "items": [item.dict() for item in items],
            "summary": {
                "average_daily_hours": round(total_hours / max(1, len(items)), 2),
                "max_daily_hours": max([item.total_hours for item in items], default=0),
                "min_daily_hours": min([item.total_hours for item in items], default=0),
                "date_range_days": (end_date - start_date).days + 1
            }
        }


async def _compute_weekly_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算每周工时统计"""
    # 简化实现，实际应该按周分组
    return await _compute_daily_hours_statistics(params, start_date, end_date)


async def _compute_monthly_hours_statistics(params: StatisticsQueryParams, start_date: date, end_date: date) -> Dict[str, Any]:
    """计算每月工时统计"""
    # 简化实现，实际应该按月分组
    return await _compute_daily_hours_statistics(params, start_date, end_date)


def register_compute_statistics_tool():
    """注册统计计算工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="compute_statistics",
            description="对工时数据进行汇总统计分析（总工时、排名、部门对比、趋势等）。若需查看原始工时明细记录，请用 query_timesheet",
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