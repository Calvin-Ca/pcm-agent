import pytest

from app.tools.sql_query import _build_permission_constraints, enforce_sql_permissions, validate_sql


def test_employee_constraint_uses_current_user():
    text = _build_permission_constraints({
        "user_id": "U_A", "entity_type": "employee", "department_id": "D_A"
    })
    assert "workhour.member_id IN ('U_A')" in text


def test_department_admin_constraint_uses_department():
    text = _build_permission_constraints({
        "user_id": "U_DA", "entity_type": "deptAdmin", "department_id": "D_A"
    })
    assert "sys_user.dept_id IN ('D_A')" in text
    assert "workhour.member_id" not in text


def test_region_admin_constraint_uses_managed_departments():
    text = _build_permission_constraints({
        "user_id": "U_RA", "entity_type": "regionAdmin",
        "managed_departments": ["D_A", "D_B"],
    })
    assert "sys_user.dept_id IN (" in text
    assert "'D_A'" in text and "'D_B'" in text


def test_employee_permission_is_injected_inside_source_before_or_clause():
    sql, params = enforce_sql_permissions(
        "SELECT wh.* FROM workhour wh WHERE 1=1 OR wh.member_id='U_B'",
        {"user_id": "U_A", "entity_type": "employee"},
    )
    assert "FROM (SELECT * FROM workhour AS __perm_src" in sql
    assert "__perm_src.member_id = :perm_user_id" in sql
    assert "WHERE 1=1 OR wh.member_id='U_B'" in sql
    assert params == {"perm_user_id": "U_A"}
    assert validate_sql(sql)[0] is True


def test_every_union_branch_gets_employee_scope():
    sql, _ = enforce_sql_permissions(
        "SELECT * FROM workhour UNION SELECT * FROM workhour_attendance",
        {"user_id": "U_A", "entity_type": "employee"},
    )
    assert sql.count(":perm_user_id") == 2


def test_department_scope_is_injected_with_bound_parameters():
    sql, params = enforce_sql_permissions(
        "SELECT SUM(wh.workhour) FROM workhour wh",
        {"user_id": "U_DA", "entity_type": "deptAdmin", "department_id": "D_A",
         "managed_departments": ["D_B"]},
    )
    assert "EXISTS (SELECT 1 FROM sys_user __perm_user" in sql
    assert "__perm_user.org_id IN (:perm_dept_0, :perm_dept_1)" in sql
    assert params == {"perm_dept_0": "D_A", "perm_dept_1": "D_B"}


def test_missing_context_fails_closed():
    with pytest.raises(PermissionError, match="缺少权限上下文"):
        enforce_sql_permissions("SELECT * FROM workhour", None)


def test_admin_without_scope_fails_closed():
    with pytest.raises(PermissionError, match="缺少管辖部门范围"):
        enforce_sql_permissions(
            "SELECT * FROM workhour",
            {"user_id": "U_RA", "entity_type": "regionAdmin"},
        )


def test_super_admin_sql_is_unchanged():
    original = "SELECT * FROM workhour"
    sql, params = enforce_sql_permissions(
        original, {"user_id": "ROOT", "entity_type": "superAdmin"}
    )
    assert sql == original and params == {}
