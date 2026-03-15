"""
Query Project Tool - 项目查询工具

提供项目信息查询功能，支持查询项目详情、成员、进度等信息。
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx
from pydantic import BaseModel, Field

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


class ProjectQueryParams(BaseModel):
    """项目查询参数"""
    project_id: str = Field(..., description="项目ID")


class ProjectMember(BaseModel):
    """项目成员"""
    user_id: str
    user_name: str
    role: str
    join_date: str


class ProjectInfo(BaseModel):
    """项目信息"""
    id: str
    name: str
    description: str
    status: str
    start_date: str
    end_date: Optional[str]
    budget: Optional[float]
    progress: float
    manager_id: str
    manager_name: str
    members: List[ProjectMember]
    created_at: str
    updated_at: str


class ProjectQueryResult(BaseModel):
    """项目查询结果"""
    success: bool
    project: Optional[ProjectInfo]
    error: Optional[str] = None


# JSON Schema定义
QUERY_PROJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目ID，必填"
        }
    },
    "required": ["project_id"],
    "additionalProperties": False
}


async def query_project_handler(**kwargs) -> Dict[str, Any]:
    """
    项目查询处理函数
    
    Args:
        **kwargs: 查询参数
        
    Returns:
        Dict[str, Any]: 查询结果
    """
    try:
        # 参数验证和解析
        params = ProjectQueryParams(**kwargs)
        
        # 构建查询URL
        base_url = "http://localhost:8080"  # SpringBoot服务地址
        url = f"{base_url}/api/projects/{params.project_id}"
        
        # 调用SpringBoot API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            
            if response.status_code == 404:
                return {
                    "success": False,
                    "project": None,
                    "error": f"项目 {params.project_id} 不存在"
                }
            
            response.raise_for_status()
            api_data = response.json()
            
            # 处理API响应
            if not api_data.get("success", False):
                return {
                    "success": False,
                    "project": None,
                    "error": api_data.get("message", "查询失败")
                }
            
            # 格式化项目信息
            project_data = api_data.get("data", {})
            
            # 处理项目成员信息
            members = []
            for member_data in project_data.get("members", []):
                member = ProjectMember(
                    user_id=member_data["userId"],
                    user_name=member_data.get("userName", "未知用户"),
                    role=member_data.get("role", "成员"),
                    join_date=member_data.get("joinDate", "")
                )
                members.append(member)
            
            # 构建项目信息
            project = ProjectInfo(
                id=project_data["id"],
                name=project_data["name"],
                description=project_data.get("description", ""),
                status=project_data.get("status", "未知"),
                start_date=project_data.get("startDate", ""),
                end_date=project_data.get("endDate"),
                budget=project_data.get("budget"),
                progress=float(project_data.get("progress", 0.0)),
                manager_id=project_data.get("managerId", ""),
                manager_name=project_data.get("managerName", "未知"),
                members=members,
                created_at=project_data.get("createdAt", ""),
                updated_at=project_data.get("updatedAt", "")
            )
            
            return {
                "success": True,
                "project": project.dict(),
                "error": None
            }
            
    except ValueError as e:
        logger.error(f"参数验证失败: {str(e)}")
        return {
            "success": False,
            "project": None,
            "error": f"参数格式错误: {str(e)}"
        }
    except httpx.HTTPError as e:
        logger.error(f"API调用失败: {str(e)}")
        return {
            "success": False,
            "project": None,
            "error": f"服务调用失败: {str(e)}"
        }
    except Exception as e:
        logger.error(f"项目查询异常: {str(e)}")
        return {
            "success": False,
            "project": None,
            "error": f"查询异常: {str(e)}"
        }


def register_query_project_tool():
    """注册项目查询工具到工具注册中心"""
    try:
        tool_registry.register_tool(
            name="query_project",
            description="查询项目详细信息，包括项目基本信息、成员列表、进度等",
            json_schema=QUERY_PROJECT_SCHEMA,
            handler=query_project_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=30,
            requires_permission=True
        )
        logger.info("项目查询工具注册成功")
    except Exception as e:
        logger.error(f"项目查询工具注册失败: {str(e)}")
        raise


# 自动注册工具（当模块被导入时）
if __name__ != "__main__":
    register_query_project_tool()