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
    user_id: Optional[str] = Field(None, description="用户ID（可选，不填则查询当前登录用户）")
    member_name: Optional[str] = Field(None, description="成员姓名（可选，用于查询指定人员，自动解析为user_id）")
    start_date: str = Field(..., description="开始日期 (YYYY-MM-DD)")
    end_date: str = Field(..., description="结束日期 (YYYY-MM-DD)")
    project_id: Optional[str] = Field(None, description="项目ID（可选）")


class TimesheetRecord(BaseModel):
    """工时记录"""
    id: str
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
    "required": ["start_date", "end_date"],
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
        # 提取非业务参数
        auth_token = kwargs.pop("auth_token", None)
        kwargs.pop("target_self", None)  # 移除非工具参数

        # 参数验证和解析
        params = TimesheetQueryParams(**{k: v for k, v in kwargs.items()
                                         if k in ["user_id", "member_name", "start_date", "end_date", "project_id"]})

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

        # 构建请求头（含认证 token）
        request_headers = {}
        if auth_token:
            # 避免重复的 "Bearer " 前缀
            token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
            request_headers["Authorization"] = token

        # Docker 容器内访问宿主机 SpringBoot 服务
        import os
        base_url = os.getenv("SPRINGBOOT_BASE_URL") or f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"

        # 如果指定了姓名但没有 user_id，先查成员获取 memberId
        resolved_user_id = params.user_id
        resolved_member_name = None
        if params.member_name and not resolved_user_id:
            member_result = await _lookup_member_by_name(
                params.member_name, base_url, request_headers
            )
            if not member_result["success"]:
                return {
                    "success": False,
                    "error": member_result["error"],
                    "total_hours": 0,
                    "record_count": 0,
                    "records": [],
                    "summary": {}
                }
            resolved_user_id = member_result["member_id"]
            resolved_member_name = member_result["member_name"]

        url = f"{base_url}/api/workhour/by-date-range"
        query_params: Dict[str, Any] = {
            "startDate": params.start_date,
            "endDate": params.end_date,
        }
        # 优先使用解析后的用户ID，否则回退到当前登录用户
        # langgraph_agent.py 在无 member_name 时会将当前用户 user_id 注入参数，
        # 此处作为 fallback 防止不传 memberId 导致 SpringBoot 返回全员数据
        if resolved_user_id:
            query_params["memberId"] = resolved_user_id
        elif params.user_id:
            query_params["memberId"] = params.user_id

        if params.project_id:
            query_params["projectId"] = params.project_id

        # 调用SpringBoot API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=query_params, headers=request_headers)
            response.raise_for_status()
            
            api_data = response.json()

            # SpringBoot /api/workhour/by-date-range 直接返回数组
            if isinstance(api_data, list):
                records_data = api_data
            elif isinstance(api_data, dict):
                # 兼容旧格式 {success, data}
                if not api_data.get("success", True):
                    return {
                        "success": False,
                        "error": api_data.get("message", "查询失败"),
                        "total_hours": 0,
                        "record_count": 0,
                        "records": [],
                        "summary": {}
                    }
                records_data = api_data.get("data", [])
            else:
                records_data = []

            # 格式化结果（适配 WorkhourDTO 字段名）
            records = []
            total_hours = 0.0
            project_summary = {}

            for record_data in records_data:
                # workhourDate 是 ISO instant 字符串，取日期部分
                raw_date = record_data.get("workhourDate", "")
                if raw_date and "T" in raw_date:
                    raw_date = raw_date.split("T")[0]

                workhour_val = record_data.get("workhour", 0) or 0
                record = TimesheetRecord(
                    id=str(record_data.get("id", "")),
                    user_id=record_data.get("memberId", ""),
                    project_id=record_data.get("projectId", ""),
                    project_name=record_data.get("projectName", "未知项目"),
                    date=raw_date,
                    duration=float(workhour_val),
                    description=record_data.get("description", ""),
                    created_at=record_data.get("createdAt", "")
                )
                records.append(record)
                total_hours += record.duration
                
                # 按项目统计
                project_name = record.project_name or "未知项目"
                if project_name not in project_summary:
                    project_summary[project_name] = {
                        "project_id": record.project_id,
                        "hours": 0.0,
                        "days": set()
                    }
                project_summary[project_name]["hours"] += record.duration
                if record.date:
                    project_summary[project_name]["days"].add(record.date)
            
            # 转换项目统计格式
            formatted_summary = {}
            for project_name, stats in project_summary.items():
                formatted_summary[project_name] = {
                    "project_id": stats["project_id"],
                    "total_hours": stats["hours"],
                    "work_days": len(stats["days"])
                }
            
            summary_info: Dict[str, Any] = {
                "date_range": f"{params.start_date} 至 {params.end_date}",
                "total_hours": round(total_hours, 2),
                "average_daily_hours": round(total_hours / max(1, (end_date - start_date).days + 1), 2),
                "projects": formatted_summary
            }
            if resolved_member_name:
                summary_info["member_name"] = resolved_member_name

            return {
                "success": True,
                "total_hours": round(total_hours, 2),
                "record_count": len(records),
                "records": [record.dict() for record in records],
                "summary": summary_info
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


async def _lookup_member_by_name(
    name: str,
    base_url: str,
    headers: Dict[str, str]
) -> Dict[str, Any]:
    """
    通过姓名从 /thsuaa/api/sys-users 查找用户，返回 id（即工时表中的 memberId）。
    优先精确匹配 entityName，否则取第一个模糊匹配结果。
    """
    try:
        url = f"{base_url}/thsuaa/api/sys-users"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"entityName.contains": name, "page": 0, "size": 10},
                headers=headers
            )
            resp.raise_for_status()
            members = resp.json()

        if not members:
            return {"success": False, "error": f"未找到姓名包含「{name}」的用户"}

        # 优先精确匹配 entityName，否则取第一个
        exact = [m for m in members if m.get("entityName") == name]
        member = exact[0] if exact else members[0]

        return {
            "success": True,
            "member_id": member["id"],
            "member_name": member.get("entityName", name),
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"用户查询失败: HTTP {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": f"用户查询异常: {str(e)}"}


def register_query_timesheet_tool():
    """注册工时查询工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="query_timesheet",
            description="查询工时记录，返回明细条目和简单汇总。支持按人员、时间范围、项目筛选，支持'本周''本月''上周'等模糊时间描述（自动推断日期范围）。适用于：查某人/自己的工时明细、查某时间段填报情况、按项目/人员筛选工时、加班记录查询。",

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