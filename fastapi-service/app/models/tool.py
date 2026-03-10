"""
Tool Registry数据模型

定义工具注册中心使用的数据模型，包括工具定义、参数、结果等。
"""

from typing import Any, Dict, List, Optional, Callable
from datetime import datetime
from pydantic import BaseModel, Field, validator
from enum import Enum


class ToolCategory(str, Enum):
    """工具类别枚举"""
    DATA_QUERY = "data_query"
    STATISTICS = "statistics"
    REPORT = "report"
    KNOWLEDGE = "knowledge"
    WORKHOUR = "workhour"


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str = Field(..., description="参数名称")
    type: str = Field(..., description="参数类型: string, number, boolean, object, array")
    description: str = Field(..., description="参数描述")
    required: bool = Field(default=False, description="是否必需")
    default: Optional[Any] = Field(default=None, description="默认值")
    enum: Optional[List[Any]] = Field(default=None, description="枚举值列表")
    
    @validator('type')
    def validate_type(cls, v):
        """验证参数类型"""
        valid_types = ['string', 'number', 'integer', 'boolean', 'object', 'array']
        if v not in valid_types:
            raise ValueError(f"参数类型必须是以下之一: {', '.join(valid_types)}")
        return v


class ToolDefinition(BaseModel):
    """工具定义"""
    name: str = Field(..., description="工具名称，必须唯一")
    description: str = Field(..., description="工具描述")
    category: ToolCategory = Field(..., description="工具类别")
    json_schema: Dict[str, Any] = Field(..., description="JSON Schema参数定义")
    timeout: int = Field(default=30, description="超时时间（秒）", ge=1, le=300)
    requires_permission: bool = Field(default=True, description="是否需要权限验证")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    
    @validator('name')
    def validate_name(cls, v):
        """验证工具名称格式"""
        if not v or not v.strip():
            raise ValueError("工具名称不能为空")
        if not v.replace('_', '').isalnum():
            raise ValueError("工具名称只能包含字母、数字和下划线")
        return v.strip()
    
    @validator('json_schema')
    def validate_json_schema(cls, v):
        """验证JSON Schema格式"""
        if not isinstance(v, dict):
            raise ValueError("json_schema必须是字典类型")
        
        # 验证必需字段
        if 'type' not in v:
            raise ValueError("json_schema必须包含type字段")
        
        if v.get('type') == 'object':
            if 'properties' not in v:
                raise ValueError("object类型的json_schema必须包含properties字段")
        
        return v
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ToolResult(BaseModel):
    """工具执行结果"""
    success: bool = Field(..., description="执行是否成功")
    data: Any = Field(default=None, description="返回数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间（秒）")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="元数据")
    
    @validator('error')
    def validate_error(cls, v, values):
        """验证错误信息"""
        if not values.get('success') and not v:
            raise ValueError("执行失败时必须提供错误信息")
        return v


class ToolExecutionContext(BaseModel):
    """工具执行上下文"""
    user_id: str = Field(..., description="用户ID")
    entity_type: str = Field(..., description="用户角色类型")
    department_id: Optional[str] = Field(default=None, description="部门ID")
    managed_projects: List[str] = Field(default_factory=list, description="管理的项目ID列表")
    session_id: Optional[str] = Field(default=None, description="会话ID")
    trace_id: Optional[str] = Field(default=None, description="追踪ID")
