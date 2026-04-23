"""
Permission Validator - 权限验证模块

提供基于角色和数据范围的权限验证功能。
"""

import logging
from typing import Dict, Any, List, Optional, Set
from enum import Enum
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """用户角色类型枚举"""
    SUPER_ADMIN = "superAdmin"  # 超级管理员
    REGION_ADMIN = "regionAdmin"  # 大区管理员
    DEPT_ADMIN = "deptAdmin"  # 部门管理员
    DEPT_SUB_ADMIN = "deptSubAdmin"  # 部门子管理员
    COMPANY_ADMIN = "companyAdmin"  # 公司管理员
    EMPLOYEE = "employee"  # 普通员工


class PermissionContext(BaseModel):
    """权限上下文"""
    user_id: str = Field(..., description="用户ID")
    entity_type: EntityType = Field(..., description="用户角色类型")
    department_id: Optional[str] = Field(None, description="用户所属部门ID")
    managed_departments: List[str] = Field(default_factory=list, description="管理的部门ID列表")
    managed_projects: List[str] = Field(default_factory=list, description="管理的项目ID列表")
    session_id: Optional[str] = Field(None, description="会话ID")
    auth_token: Optional[str] = Field(None, description="认证令牌（JWT）")

    class Config:
        use_enum_values = True


class DataFilter(BaseModel):
    """数据过滤器"""
    user_ids: Optional[Set[str]] = Field(None, description="允许访问的用户ID集合")
    project_ids: Optional[Set[str]] = Field(None, description="允许访问的项目ID集合")
    department_ids: Optional[Set[str]] = Field(None, description="允许访问的部门ID集合")
    is_unrestricted: bool = Field(False, description="是否无限制访问")


class PermissionResult(BaseModel):
    """权限验证结果"""
    allowed: bool = Field(..., description="是否允许访问")
    reason: str = Field(..., description="允许或拒绝的原因")
    data_filter: Optional[DataFilter] = Field(None, description="数据过滤器")


class PermissionValidator:
    """权限验证器"""
    
    def __init__(self):
        """初始化权限验证器"""
        logger.info("Permission Validator initialized")
    
    def can_access_user_data(
        self,
        context: PermissionContext,
        target_user_id: str
    ) -> PermissionResult:
        """
        验证是否可以访问指定用户的数据
        
        Args:
            context: 权限上下文
            target_user_id: 目标用户ID
            
        Returns:
            PermissionResult: 权限验证结果
        """
        try:
            # 超级管理员可以访问所有用户数据
            if context.entity_type == EntityType.SUPER_ADMIN:
                return PermissionResult(
                    allowed=True,
                    reason="超级管理员拥有全部数据访问权限",
                    data_filter=DataFilter(is_unrestricted=True)
                )
            
            # 用户可以访问自己的数据
            if context.user_id == target_user_id:
                return PermissionResult(
                    allowed=True,
                    reason="用户可以访问自己的数据",
                    data_filter=DataFilter(user_ids={target_user_id})
                )
            
            # 部门管理员可以访问本部门用户数据
            if context.entity_type in [EntityType.DEPT_ADMIN, EntityType.DEPT_SUB_ADMIN]:
                if context.department_id:
                    # 这里需要调用API检查目标用户是否属于该部门
                    # 简化实现，假设可以访问
                    return PermissionResult(
                        allowed=True,
                        reason="部门管理员可以访问本部门用户数据",
                        data_filter=DataFilter(
                            user_ids={target_user_id},
                            department_ids={context.department_id}
                        )
                    )
            
            # 大区管理员和公司管理员可以访问管辖范围内用户数据
            if context.entity_type in [EntityType.REGION_ADMIN, EntityType.COMPANY_ADMIN]:
                if context.managed_departments:
                    return PermissionResult(
                        allowed=True,
                        reason="管理员可以访问管辖范围内用户数据",
                        data_filter=DataFilter(
                            user_ids={target_user_id},
                            department_ids=set(context.managed_departments)
                        )
                    )
            
            # 默认拒绝访问
            return PermissionResult(
                allowed=False,
                reason="没有访问该用户数据的权限",
                data_filter=None
            )
            
        except Exception as e:
            logger.error(f"用户数据权限验证异常: {str(e)}")
            return PermissionResult(
                allowed=False,
                reason=f"权限验证异常: {str(e)}",
                data_filter=None
            )
    
    def can_access_project_data(
        self,
        context: PermissionContext,
        target_project_id: str
    ) -> PermissionResult:
        """
        验证是否可以访问指定项目的数据
        
        Args:
            context: 权限上下文
            target_project_id: 目标项目ID
            
        Returns:
            PermissionResult: 权限验证结果
        """
        try:
            # 超级管理员可以访问所有项目数据
            if context.entity_type == EntityType.SUPER_ADMIN:
                return PermissionResult(
                    allowed=True,
                    reason="超级管理员拥有全部项目访问权限",
                    data_filter=DataFilter(is_unrestricted=True)
                )
            
            # 项目经理可以访问管理的项目
            if context.managed_projects and target_project_id in context.managed_projects:
                return PermissionResult(
                    allowed=True,
                    reason="用户可以访问管理的项目数据",
                    data_filter=DataFilter(project_ids={target_project_id})
                )
            
            # 部门管理员可以访问本部门的项目
            if context.entity_type in [EntityType.DEPT_ADMIN, EntityType.DEPT_SUB_ADMIN]:
                if context.department_id:
                    # 这里需要调用API检查项目是否属于该部门
                    # 简化实现，假设可以访问
                    return PermissionResult(
                        allowed=True,
                        reason="部门管理员可以访问本部门项目数据",
                        data_filter=DataFilter(
                            project_ids={target_project_id},
                            department_ids={context.department_id}
                        )
                    )
            
            # 大区管理员和公司管理员可以访问管辖范围内的项目
            if context.entity_type in [EntityType.REGION_ADMIN, EntityType.COMPANY_ADMIN]:
                return PermissionResult(
                    allowed=True,
                    reason="管理员可以访问管辖范围内项目数据",
                    data_filter=DataFilter(
                        project_ids={target_project_id},
                        department_ids=set(context.managed_departments) if context.managed_departments else None
                    )
                )
            
            # 普通员工可以访问参与的项目
            if context.entity_type == EntityType.EMPLOYEE:
                # 这里需要调用API检查用户是否是项目成员
                # 简化实现，假设可以访问
                return PermissionResult(
                    allowed=True,
                    reason="员工可以访问参与的项目数据",
                    data_filter=DataFilter(
                        project_ids={target_project_id},
                        user_ids={context.user_id}
                    )
                )
            
            # 默认拒绝访问
            return PermissionResult(
                allowed=False,
                reason="没有访问该项目数据的权限",
                data_filter=None
            )
            
        except Exception as e:
            logger.error(f"项目数据权限验证异常: {str(e)}")
            return PermissionResult(
                allowed=False,
                reason=f"权限验证异常: {str(e)}",
                data_filter=None
            )
    
    def can_access_department_data(
        self,
        context: PermissionContext,
        target_department_id: str
    ) -> PermissionResult:
        """
        验证是否可以访问指定部门的数据
        
        Args:
            context: 权限上下文
            target_department_id: 目标部门ID
            
        Returns:
            PermissionResult: 权限验证结果
        """
        try:
            # 超级管理员可以访问所有部门数据
            if context.entity_type == EntityType.SUPER_ADMIN:
                return PermissionResult(
                    allowed=True,
                    reason="超级管理员拥有全部部门访问权限",
                    data_filter=DataFilter(is_unrestricted=True)
                )
            
            # 部门管理员可以访问管理的部门
            if context.entity_type in [EntityType.DEPT_ADMIN, EntityType.DEPT_SUB_ADMIN]:
                if target_department_id in context.managed_departments or target_department_id == context.department_id:
                    return PermissionResult(
                        allowed=True,
                        reason="部门管理员可以访问管理的部门数据",
                        data_filter=DataFilter(department_ids={target_department_id})
                    )
            
            # 大区管理员和公司管理员可以访问管辖范围内的部门
            if context.entity_type in [EntityType.REGION_ADMIN, EntityType.COMPANY_ADMIN]:
                if target_department_id in context.managed_departments:
                    return PermissionResult(
                        allowed=True,
                        reason="管理员可以访问管辖范围内部门数据",
                        data_filter=DataFilter(department_ids={target_department_id})
                    )
            
            # 用户可以访问自己所属部门的基本信息
            if context.department_id == target_department_id:
                return PermissionResult(
                    allowed=True,
                    reason="用户可以访问所属部门的基本信息",
                    data_filter=DataFilter(
                        department_ids={target_department_id},
                        user_ids={context.user_id}
                    )
                )
            
            # 默认拒绝访问
            return PermissionResult(
                allowed=False,
                reason="没有访问该部门数据的权限",
                data_filter=None
            )
            
        except Exception as e:
            logger.error(f"部门数据权限验证异常: {str(e)}")
            return PermissionResult(
                allowed=False,
                reason=f"权限验证异常: {str(e)}",
                data_filter=None
            )
    
    def get_data_filter(
        self,
        context: PermissionContext,
        operation: str = "read"
    ) -> DataFilter:
        """
        获取数据过滤器
        
        Args:
            context: 权限上下文
            operation: 操作类型（read, write, delete等）
            
        Returns:
            DataFilter: 数据过滤器
        """
        try:
            # 超级管理员无限制访问
            if context.entity_type == EntityType.SUPER_ADMIN:
                return DataFilter(is_unrestricted=True)
            
            # 部门管理员可以访问本部门数据
            if context.entity_type in [EntityType.DEPT_ADMIN, EntityType.DEPT_SUB_ADMIN]:
                filter_data = DataFilter()
                if context.department_id:
                    filter_data.department_ids = {context.department_id}
                if context.managed_departments:
                    if filter_data.department_ids:
                        filter_data.department_ids.update(context.managed_departments)
                    else:
                        filter_data.department_ids = set(context.managed_departments)
                return filter_data
            
            # 大区管理员和公司管理员可以访问管辖范围内数据
            if context.entity_type in [EntityType.REGION_ADMIN, EntityType.COMPANY_ADMIN]:
                filter_data = DataFilter()
                if context.managed_departments:
                    filter_data.department_ids = set(context.managed_departments)
                if context.managed_projects:
                    filter_data.project_ids = set(context.managed_projects)
                return filter_data
            
            # 有项目管理权限的用户可以访问管理的项目数据
            if context.managed_projects:
                filter_data = DataFilter()
                filter_data.project_ids = set(context.managed_projects)
                # 也可以访问自己的数据
                filter_data.user_ids = {context.user_id}
                return filter_data
            
            # 普通员工只能访问自己的数据
            if context.entity_type == EntityType.EMPLOYEE:
                return DataFilter(user_ids={context.user_id})
            
            # 默认只能访问自己的数据
            return DataFilter(user_ids={context.user_id})
            
        except Exception as e:
            logger.error(f"获取数据过滤器异常: {str(e)}")
            # 异常情况下返回最严格的过滤器
            return DataFilter(user_ids={context.user_id})
    
    def validate_tool_access(
        self,
        context: PermissionContext,
        tool_name: str,
        tool_params: Dict[str, Any]
    ) -> PermissionResult:
        """
        验证工具访问权限
        
        Args:
            context: 权限上下文
            tool_name: 工具名称
            tool_params: 工具参数
            
        Returns:
            PermissionResult: 权限验证结果
        """
        try:
            # 根据工具类型进行权限验证
            if tool_name == "query_timesheet":
                target_user_id = tool_params.get("user_id")
                if target_user_id:
                    return self.can_access_user_data(context, target_user_id)
            
            elif tool_name == "query_project":
                target_project_id = tool_params.get("project_id")
                if target_project_id:
                    return self.can_access_project_data(context, target_project_id)
            
            elif tool_name == "compute_statistics":
                # 统计工具需要根据参数进行复合权限验证
                return self._validate_statistics_access(context, tool_params)
            
            # 默认允许访问（基于工具自身的权限要求）
            return PermissionResult(
                allowed=True,
                reason="工具访问权限验证通过",
                data_filter=self.get_data_filter(context)
            )
            
        except Exception as e:
            logger.error(f"工具访问权限验证异常: {str(e)}")
            return PermissionResult(
                allowed=False,
                reason=f"权限验证异常: {str(e)}",
                data_filter=None
            )
    
    def _validate_statistics_access(
        self,
        context: PermissionContext,
        tool_params: Dict[str, Any]
    ) -> PermissionResult:
        """验证统计工具访问权限"""
        statistics_type = tool_params.get("statistics_type")
        
        # 超级管理员可以访问所有统计
        if context.entity_type == EntityType.SUPER_ADMIN:
            return PermissionResult(
                allowed=True,
                reason="超级管理员可以访问所有统计数据",
                data_filter=DataFilter(is_unrestricted=True)
            )
        
        # 部门管理员可以访问部门相关统计
        if context.entity_type in [EntityType.DEPT_ADMIN, EntityType.DEPT_SUB_ADMIN]:
            if statistics_type in ["department_hours", "user_hours", "project_hours"]:
                return PermissionResult(
                    allowed=True,
                    reason="部门管理员可以访问部门相关统计",
                    data_filter=DataFilter(department_ids={context.department_id} if context.department_id else None)
                )
        
        # 大区管理员和公司管理员可以访问更广范围的统计
        if context.entity_type in [EntityType.REGION_ADMIN, EntityType.COMPANY_ADMIN]:
            if statistics_type in ["department_hours", "user_hours", "project_hours", "daily_hours", "weekly_hours", "monthly_hours"]:
                return PermissionResult(
                    allowed=True,
                    reason="管理员可以访问管辖范围内统计数据",
                    data_filter=DataFilter(department_ids=set(context.managed_departments) if context.managed_departments else None)
                )
        
        # 有项目管理权限的用户可以访问项目相关统计
        if context.managed_projects:
            if statistics_type in ["project_hours", "user_hours"]:
                return PermissionResult(
                    allowed=True,
                    reason="项目管理者可以访问项目相关统计",
                    data_filter=DataFilter(project_ids=set(context.managed_projects))
                )
        
        # 普通员工只能访问自己的统计
        if statistics_type == "user_hours" and tool_params.get("user_id") == context.user_id:
            return PermissionResult(
                allowed=True,
                reason="员工可以访问自己的工时统计",
                data_filter=DataFilter(user_ids={context.user_id})
            )
        
        return PermissionResult(
            allowed=False,
            reason="没有访问该统计数据的权限",
            data_filter=None
        )


# 全局权限验证器实例
permission_validator = PermissionValidator()