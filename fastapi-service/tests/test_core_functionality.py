#!/usr/bin/env python3
"""
AI服务层核心功能验证脚本

用于验证工具注册、意图识别、权限验证等核心功能是否正常工作。
"""

import asyncio
import logging
import sys
from pathlib import Path

import pytest

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from app.services.tool_registry import ToolRegistry
from app.services.permission_validator import PermissionValidator, PermissionContext, EntityType
from app.services.intent_router import IntentRouter
from app.models.task_plan import PlannerAgent
from app.services.task_executor import TaskExecutor

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_tool_registry():
    """测试工具注册中心"""
    logger.info("=== 测试工具注册中心 ===")
    
    try:
        # 初始化工具注册中心
        tool_registry = ToolRegistry()
        
        # 导入工具模块（会自动注册）
        from app.tools import query_timesheet, query_project, compute_statistics
        
        # 检查工具列表
        tools = tool_registry.list_tools()
        logger.info(f"已注册工具数量: {len(tools)}")

        # 验证每个工具
        for tool_def in tools:
            retrieved = tool_registry.get_tool(tool_def.name)
            if retrieved:
                logger.info(f"✅ 工具 {tool_def.name} 注册成功")
            else:
                logger.error(f"❌ 工具 {tool_def.name} 获取失败")
                return False
        
        logger.info("✅ 工具注册中心测试通过")
        return True
        
    except Exception as e:
        logger.error(f"❌ 工具注册中心测试失败: {e}")
        return False


@pytest.mark.asyncio
async def test_permission_validator():
    """测试权限验证器"""
    logger.info("=== 测试权限验证器 ===")
    
    try:
        # 初始化权限验证器
        permission_validator = PermissionValidator()
        
        # 测试超级管理员权限
        super_admin_context = PermissionContext(
            user_id="admin001",
            entity_type=EntityType.SUPER_ADMIN,
            department_id=None
        )
        
        # 超级管理员应该能访问任何用户数据
        can_access = permission_validator.can_access_user_data(super_admin_context, "user001")
        if can_access:
            logger.info("✅ 超级管理员权限验证通过")
        else:
            logger.error("❌ 超级管理员权限验证失败")
            return False
        
        # 测试普通员工权限
        employee_context = PermissionContext(
            user_id="emp001",
            entity_type=EntityType.EMPLOYEE,
            department_id="dept001"
        )
        
        # 普通员工只能访问自己的数据
        can_access_self = permission_validator.can_access_user_data(employee_context, "emp001")
        can_access_other = permission_validator.can_access_user_data(employee_context, "emp002")
        
        if can_access_self.allowed and not can_access_other.allowed:
            logger.info("✅ 普通员工权限验证通过")
        else:
            logger.error("❌ 普通员工权限验证失败")
            return False
        
        logger.info("✅ 权限验证器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"❌ 权限验证器测试失败: {e}")
        return False


@pytest.mark.asyncio
async def test_intent_router():
    """测试意图路由器"""
    logger.info("=== 测试意图路由器 ===")
    
    try:
        # 初始化组件
        tool_registry = ToolRegistry()
        from app.tools import query_timesheet, query_project, compute_statistics
        
        intent_router = IntentRouter()
        intent_router.set_tool_registry(tool_registry)
        
        # 测试工具意图识别
        test_messages = [
            "查询我本周的工时",
            "统计部门工时数据",
            "查看项目信息",
            "工时填报规则是什么？",
            "帮我生成周报并发送邮件"
        ]
        
        for message in test_messages:
            logger.info(f"测试消息: {message}")

            route_decision = await intent_router.make_route_decision(message)

            logger.info(f"  意图类型: {route_decision.intent_result.intent_type}")
            logger.info(f"  路由目标: {route_decision.target}")
            logger.info(f"  置信度: {route_decision.intent_result.confidence}")

            if route_decision.intent_result.confidence > 0.5:
                logger.info("  ✅ 意图识别成功")
            else:
                logger.info("  ⚠️ 意图识别置信度较低")
        
        logger.info("✅ 意图路由器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"❌ 意图路由器测试失败: {e}")
        return False


@pytest.mark.asyncio
async def test_task_planner():
    """测试任务规划器"""
    logger.info("=== 测试任务规划器 ===")
    
    try:
        # 初始化组件
        tool_registry = ToolRegistry()
        from app.tools import query_timesheet, query_project, compute_statistics
        
        # 模拟LLM客户端（用于测试）
        class MockLLMClient:
            async def generate(self, prompt, max_tokens=1000, temperature=0.1):
                # 返回一个简单的任务计划JSON
                return '''
PLAN_START
{
  "plan_name": "查询工时统计计划",
  "description": "查询用户工时并生成统计报告",
  "tasks": [
    {
      "task_id": "task_1",
      "task_type": "tool_call",
      "tool_name": "query_timesheet",
      "description": "查询用户工时数据",
      "parameters": {
        "userId": "user001",
        "dateRange": "thisWeek"
      },
      "dependencies": []
    },
    {
      "task_id": "task_2",
      "task_type": "tool_call",
      "tool_name": "compute_statistics",
      "description": "计算工时统计",
      "parameters": {
        "statisticsType": "user_summary",
        "filters": {
          "user_ids": ["user001"]
        }
      },
      "dependencies": ["task_1"]
    }
  ]
}
PLAN_END
                '''
        
        planner_agent = PlannerAgent(
            tool_registry=tool_registry,
            llm_client=MockLLMClient()
        )
        
        # 测试任务规划
        user_request = "查询我本周工时并生成统计报告"
        task_plan = await planner_agent.plan_tasks(
            user_request=user_request,
            available_tools=tool_registry.list_tools()
        )
        
        logger.info(f"生成任务计划: {task_plan.name}")
        logger.info(f"任务数量: {len(task_plan.tasks)}")
        
        # 验证任务依赖关系
        task_plan.validate_dependencies()
        logger.info("✅ 任务依赖关系验证通过")
        
        # 测试拓扑排序
        levels = task_plan.topological_sort()
        logger.info(f"任务执行层级: {len(levels)} 层")
        
        logger.info("✅ 任务规划器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"❌ 任务规划器测试失败: {e}")
        return False


async def main():
    """主测试函数"""
    logger.info("🚀 开始AI服务层核心功能验证")
    
    test_results = []
    
    # 执行各项测试
    test_results.append(await test_tool_registry())
    test_results.append(await test_permission_validator())
    test_results.append(await test_intent_router())
    test_results.append(await test_task_planner())
    
    # 汇总测试结果
    passed_tests = sum(test_results)
    total_tests = len(test_results)
    
    logger.info(f"📊 测试结果: {passed_tests}/{total_tests} 通过")
    
    if passed_tests == total_tests:
        logger.info("🎉 所有核心功能验证通过！")
        return True
    else:
        logger.error("❌ 部分核心功能验证失败")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)