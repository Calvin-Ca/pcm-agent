"""
Permission Validator属性测试

使用Hypothesis进行基于属性的测试，验证权限验证的核心属性：
- 幂等性：相同输入产生相同结果
- 超级管理员可以访问所有数据
- 用户可以访问自己的数据（无论角色）
- 权限规则一致性

属性2: 权限验证一致性
验证需求: 10.1-10.6
"""

import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from hypothesis.stateful import RuleBasedStateMachine, rule, precondition

from app.services.permission_validator import (
    PermissionValidator,
    PermissionContext,
    EntityType,
    DataFilter
)

# 全局验证器实例（Hypothesis测试中使用）
def get_validator():
    """获取验证器实例"""
    return PermissionValidator()


# 定义Hypothesis策略
entity_types = st.sampled_from(list(EntityType))

# 生成有效的ID字符串
id_strings = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=('L', 'N'))  # 字母和数字
)

# 生成权限上下文
@st.composite
def permission_contexts(draw):
    """生成随机的权限上下文"""
    user_id = draw(id_strings)
    entity_type = draw(entity_types)
    department_id = draw(st.one_of(st.none(), id_strings))
    managed_departments = draw(st.lists(id_strings, min_size=0, max_size=5))
    managed_projects = draw(st.lists(id_strings, min_size=0, max_size=5))

    return PermissionContext(
        user_id=user_id,
        entity_type=entity_type,
        department_id=department_id,
        managed_departments=managed_departments,
        managed_projects=managed_projects
    )


class TestPermissionValidatorProperties:
    """Permission Validator属性测试类"""

    # ============================================================================
    # 属性1: 幂等性 - 相同输入产生相同结果
    # ============================================================================

    @given(permission_contexts(), id_strings)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_user_access_idempotency(self, context, target_user_id):
        """测试用户数据访问验证的幂等性"""
        validator = get_validator()
        result1 = validator.can_access_user_data(context, target_user_id)
        result2 = validator.can_access_user_data(context, target_user_id)

        assert result1.allowed == result2.allowed
        assert result1.reason == result2.reason
        assert result1.data_filter == result2.data_filter

    @given(permission_contexts(), id_strings)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_project_access_idempotency(self, context, target_project_id):
        """测试项目数据访问验证的幂等性"""
        validator = get_validator()
        result1 = validator.can_access_project_data(context, target_project_id)
        result2 = validator.can_access_project_data(context, target_project_id)

        assert result1.allowed == result2.allowed
        assert result1.reason == result2.reason
        assert result1.data_filter == result2.data_filter

    @given(permission_contexts(), id_strings)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_department_access_idempotency(self, context, target_dept_id):
        """测试部门数据访问验证的幂等性"""
        validator = get_validator()
        result1 = validator.can_access_department_data(context, target_dept_id)
        result2 = validator.can_access_department_data(context, target_dept_id)

        assert result1.allowed == result2.allowed
        assert result1.reason == result2.reason
        assert result1.data_filter == result2.data_filter

    # ============================================================================
    # 属性2: 超级管理员可以访问所有数据
    # ============================================================================

    @given(id_strings, id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_super_admin_can_access_any_user(self, user_id, target_user_id, dept_id):
        """测试超级管理员可以访问任何用户数据"""
        validator = get_validator()
        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.SUPER_ADMIN,
            department_id=dept_id,
            managed_departments=[],
            managed_projects=[]
        )

        result = validator.can_access_user_data(context, target_user_id)

        assert result.allowed is True
        assert result.data_filter.is_unrestricted is True
        assert "超级管理员" in result.reason

    @given(id_strings, id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_super_admin_can_access_any_project(self, user_id, target_project_id, dept_id):
        """测试超级管理员可以访问任何项目数据"""
        validator = get_validator()
        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.SUPER_ADMIN,
            department_id=dept_id,
            managed_departments=[],
            managed_projects=[]
        )

        result = validator.can_access_project_data(context, target_project_id)

        assert result.allowed is True
        assert result.data_filter.is_unrestricted is True
        assert "超级管理员" in result.reason

    @given(id_strings, id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_super_admin_can_access_any_department(self, user_id, target_dept_id, dept_id):
        """测试超级管理员可以访问任何部门数据"""
        validator = get_validator()
        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.SUPER_ADMIN,
            department_id=dept_id,
            managed_departments=[],
            managed_projects=[]
        )

        result = validator.can_access_department_data(context, target_dept_id)

        assert result.allowed is True
        assert result.data_filter.is_unrestricted is True
        assert "超级管理员" in result.reason

    @given(id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_super_admin_data_filter_unrestricted(self, user_id):
        """测试超级管理员获取的数据过滤器是无限制的"""
        validator = get_validator()
        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.SUPER_ADMIN
        )

        data_filter = validator.get_data_filter(context)

        assert data_filter.is_unrestricted is True

    # ============================================================================
    # 属性3: 用户总是可以访问自己的数据（无论角色）
    # ============================================================================

    @given(entity_types, id_strings)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_user_can_always_access_own_data(self, entity_type, user_id):
        """测试任何角色的用户都可以访问自己的数据"""
        validator = get_validator()
        context = PermissionContext(
            user_id=user_id,
            entity_type=entity_type
        )

        result = validator.can_access_user_data(context, user_id)

        assert result.allowed is True, f"角色 {entity_type} 应该可以访问自己的数据"
        assert result.data_filter is not None

        # 超级管理员的 data_filter 是无限制的，不需要检查 user_ids
        if entity_type != EntityType.SUPER_ADMIN:
            assert result.data_filter.user_ids == {user_id}
            assert "自己" in result.reason
        else:
            assert result.data_filter.is_unrestricted is True
            assert "超级管理员" in result.reason

    # ============================================================================
    # 属性4: 数据过滤器的一致性
    # ============================================================================

    @given(permission_contexts())
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_data_filter_consistency(self, context):
        """测试数据过滤器的一致性"""
        validator = get_validator()
        data_filter = validator.get_data_filter(context)

        # 超级管理员必须是无限制的
        if context.entity_type == EntityType.SUPER_ADMIN:
            assert data_filter.is_unrestricted is True
        else:
            assert data_filter.is_unrestricted is False

        # 数据过滤器必须是有效的
        assert data_filter is not None

    @given(id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_employee_data_filter_limited(self, user_id, target_user_id):
        """测试员工的数据过滤器是受限制的"""
        validator = get_validator()
        assume(user_id != target_user_id)  # 假设不是访问自己的数据

        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.EMPLOYEE
        )

        data_filter = validator.get_data_filter(context)

        assert data_filter.is_unrestricted is False
        assert data_filter.user_ids == {user_id}

    # ============================================================================
    # 属性5: 部门管理员的权限范围
    # ============================================================================

    @given(id_strings, id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_dept_admin_can_access_own_department(self, user_id, dept_id, other_dept_id):
        """测试部门管理员可以访问自己管理的部门"""
        validator = get_validator()
        assume(dept_id != other_dept_id)

        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.DEPT_ADMIN,
            department_id=dept_id,
            managed_departments=[dept_id]
        )

        result = validator.can_access_department_data(context, dept_id)

        assert result.allowed is True
        assert result.data_filter.department_ids == {dept_id}

    @given(id_strings, id_strings, id_strings)
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_dept_admin_cannot_access_other_departments(self, user_id, dept_id, other_dept_id):
        """测试部门管理员不能访问其他部门"""
        validator = get_validator()
        assume(dept_id != other_dept_id)

        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.DEPT_ADMIN,
            department_id=dept_id,
            managed_departments=[dept_id]
        )

        result = validator.can_access_department_data(context, other_dept_id)

        # 应该被拒绝访问（非管理的部门）
        assert result.allowed is False

    # ============================================================================
    # 属性6: 大区管理员的权限范围
    # ============================================================================

    @given(id_strings, st.lists(id_strings, min_size=1, max_size=5))
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_region_admin_can_access_managed_departments(self, user_id, managed_depts):
        """测试大区管理员可以访问管辖的部门"""
        validator = get_validator()
        assume(len(managed_depts) > 0)

        context = PermissionContext(
            user_id=user_id,
            entity_type=EntityType.REGION_ADMIN,
            managed_departments=managed_depts
        )

        # 测试可以访问每个管辖部门
        for dept_id in managed_depts:
            result = validator.can_access_department_data(context, dept_id)
            assert result.allowed is True, f"大区管理员应该可以访问管辖部门 {dept_id}"

    # ============================================================================
    # 属性7: 工具访问权限的一致性
    # ============================================================================

    @given(permission_contexts())
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_tool_access_consistency(self, context):
        """测试工具访问权限的一致性"""
        validator = get_validator()
        # 测试query_timesheet工具
        result = validator.validate_tool_access(
            context,
            "query_timesheet",
            {"user_id": context.user_id}
        )

        # 用户应该可以访问自己的工时数据
        assert result.allowed is True
        assert result.data_filter is not None

    # ============================================================================
    # 边界情况测试
    # ============================================================================

    @given(st.text(min_size=0, max_size=100))
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_empty_or_special_strings_handling(self, special_id):
        """测试空字符串或特殊字符的处理"""
        validator = get_validator()
        context = PermissionContext(
            user_id="test_user",
            entity_type=EntityType.EMPLOYEE
        )

        # 不应该抛出异常
        result = validator.can_access_user_data(context, special_id)
        assert result is not None
        assert isinstance(result.allowed, bool)


class TestPermissionRulesConsistency:
    """权限规则一致性测试"""

    @pytest.fixture
    def validator(self):
        return PermissionValidator()

    def test_all_roles_can_access_own_data(self, validator):
        """测试所有角色都可以访问自己的数据"""
        roles = list(EntityType)
        user_id = "test_user_001"

        for role in roles:
            context = PermissionContext(
                user_id=user_id,
                entity_type=role
            )

            result = validator.can_access_user_data(context, user_id)
            assert result.allowed is True, f"角色 {role.value} 应该可以访问自己的数据"
            assert "自己" in result.reason or "超级管理员" in result.reason

    def test_permission_hierarchy(self, validator):
        """测试权限层级关系"""
        # 超级管理员 > 大区管理员 > 部门管理员 > 普通员工

        target_user = "target_user_001"
        target_project = "target_project_001"
        target_dept = "target_dept_001"

        # 测试各类角色对用户数据的访问
        contexts = [
            ("super_admin", PermissionContext(user_id="admin", entity_type=EntityType.SUPER_ADMIN)),
            ("region_admin", PermissionContext(user_id="region_admin", entity_type=EntityType.REGION_ADMIN, managed_departments=[target_dept])),
            ("dept_admin", PermissionContext(user_id="dept_admin", entity_type=EntityType.DEPT_ADMIN, department_id=target_dept, managed_departments=[target_dept])),
            ("employee", PermissionContext(user_id="employee", entity_type=EntityType.EMPLOYEE)),
        ]

        for role_name, context in contexts:
            result = validator.can_access_user_data(context, context.user_id)
            assert result.allowed is True, f"{role_name} 应该可以访问自己的数据"

    def test_data_filter_restriction_levels(self, validator):
        """测试数据过滤器的限制级别"""

        # 超级管理员 - 无限制
        super_admin = PermissionContext(user_id="admin", entity_type=EntityType.SUPER_ADMIN)
        filter1 = validator.get_data_filter(super_admin)
        assert filter1.is_unrestricted is True

        # 普通员工 - 只能访问自己的数据
        employee = PermissionContext(user_id="emp", entity_type=EntityType.EMPLOYEE)
        filter2 = validator.get_data_filter(employee)
        assert filter2.is_unrestricted is False
        assert filter2.user_ids == {"emp"}

        # 部门管理员 - 可以访问部门数据
        dept_admin = PermissionContext(user_id="dept_admin", entity_type=EntityType.DEPT_ADMIN, department_id="dept1")
        filter3 = validator.get_data_filter(dept_admin)
        assert filter3.is_unrestricted is False

