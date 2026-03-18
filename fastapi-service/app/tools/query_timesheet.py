"""
Query Timesheet Tool - 工时查询工具

提供工时数据查询功能，支持按用户、时间范围等条件查询工时记录。
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
import httpx
from pydantic import BaseModel, Field

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


class TimesheetQueryParams(BaseModel):
    """工时查询参数"""
    user_id: str = Field(..., description="用户ID")
    start_date: str = Field(..., description="开始日期 (YYYY-MM-DD)")
    end_date: str = Field(..., description="结束日期 (YYYY-MM-DD)")
    project_id: Optional[str] = Field(None, description="项目ID（可选）")


class TimesheetRecord(BaseModel):
    """工时记录"""
    id: int
    user_id: str
    project_id: str
    project_name: str
    date: str
    duration: float
    description: str
    created_at: str


class TimesheetQueryResult(BaseModel):
    """工时查询结果"""
    success: bool
    total_hours: float
    record_count: int
    records: List[TimesheetRecord]
    summary: Dict[str, Any]


# JSON Schema定义
QUERY_TIMESHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "user_id": {
            "type": "string",
            "description": "用户ID，必填"
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
        "project_id": {
            "type": "string",
            "description": "项目ID（可选），用于筛选特定项目的工时"
        }
    },
    "required": ["user_id", "start_date", "end_date"],
    "additionalProperties": False
}


async def query_timesheet_handler(**kwargs) -> Dict[str, Any]:
    """
    工时查询处理函数
    
    Args:
        **kwargs: 查询参数
        
    Returns:
        Dict[str, Any]: 查询结果
    """
    try:
        # 参数验证和解析
        params = TimesheetQueryParams(**kwargs)
        
        # 验证日期格式和逻辑
        start_date = datetime.strptime(params.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d").date()
        
        if start_date > end_date:
            return {
                "success": False,
                "error": "开始日期不能晚于结束日期",
                "total_hours": 0,
                "record_count": 0,
                "records": [],
                "summary": {}
            }
        
        # 构建查询URL和参数
        base_url = "http://localhost:8080"  # SpringBoot服务地址
        url = f"{base_url}/api/workhours/query"
        
        query_params = {
            "userId": params.user_id,
            "startDate": params.start_date,
            "endDate": params.end_date
        }
        
        if params.project_id:
            query_params["projectId"] = params.project_id
        
        # 调用SpringBoot API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=query_params)
            response.raise_for_status()
            
            api_data = response.json()
            
            # 处理API响应
            if not api_data.get("success", False):
                return {
                    "success": False,
                    "error": api_data.get("message", "查询失败"),
                    "total_hours": 0,
                    "record_count": 0,
                    "records": [],
                    "summary": {}
                }
            
            # 格式化结果
            records_data = api_data.get("data", [])
            records = []
            total_hours = 0.0
            project_summary = {}
            
            for record_data in records_data:
                record = TimesheetRecord(
                    id=record_data["id"],
                    user_id=record_data["userId"],
                    project_id=record_data["projectId"],
                    project_name=record_data.get("projectName", "未知项目"),
                    date=record_data["date"],
                    duration=float(record_data["duration"]),
                    description=record_data.get("description", ""),
                    created_at=record_data.get("createdAt", "")
                )
                records.append(record)
                total_hours += record.duration
                
                # 按项目统计
                project_name = record.project_name
                if project_name not in project_summary:
                    project_summary[project_name] = {
                        "project_id": record.project_id,
                        "hours": 0.0,
                        "days": set()
                    }
                project_summary[project_name]["hours"] += record.duration
                project_summary[project_name]["days"].add(record.date)
            
            # 转换项目统计格式
            formatted_summary = {}
            for project_name, stats in project_summary.items():
                formatted_summary[project_name] = {
                    "project_id": stats["project_id"],
                    "total_hours": stats["hours"],
                    "work_days": len(stats["days"])
                }
            
            return {
                "success": True,
                "total_hours": round(total_hours, 2),
                "record_count": len(records),
                "records": [record.dict() for record in records],
                "summary": {
                    "date_range": f"{params.start_date} 至 {params.end_date}",
                    "total_hours": round(total_hours, 2),
                    "average_daily_hours": round(total_hours / max(1, (end_date - start_date).days + 1), 2),
                    "projects": formatted_summary
                }
            }
            
    except ValueError as e:
        logger.error(f"参数验证失败: {str(e)}")
        return {
            "success": False,
            "error": f"参数格式错误: {str(e)}",
            "total_hours": 0,
            "record_count": 0,
            "records": [],
            "summary": {}
        }
    except httpx.HTTPError as e:
        logger.error(f"API调用失败: {str(e)}")
        return {
            "success": False,
            "error": f"服务调用失败: {str(e)}",
            "total_hours": 0,
            "record_count": 0,
            "records": [],
            "summary": {}
        }
    except Exception as e:
        logger.error(f"工时查询异常: {str(e)}")
        return {
            "success": False,
            "error": f"查询异常: {str(e)}",
            "total_hours": 0,
            "record_count": 0,
            "records": [],
            "summary": {}
        }


def register_query_timesheet_tool():
    """注册工时查询工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="query_timesheet",
            description="查询用户工时记录，支持按时间范围和项目筛选",
            json_schema=QUERY_TIMESHEET_SCHEMA,
            handler=query_timesheet_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=30,
            requires_permission=True
        )
        logger.info("工时查询工具注册成功")
    except Exception as e:
        logger.error(f"工时查询工具注册失败: {str(e)}")
        raise


# 自动注册工具（当模块被导入时）
if __name__ != "__main__":
    register_query_timesheet_tool()