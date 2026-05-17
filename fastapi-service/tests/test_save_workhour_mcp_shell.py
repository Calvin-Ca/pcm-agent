"""save_workhour MCP 薄壳：confirm→dry_run 映射 + 不接受任意 user_id（G4）。"""
import importlib


def test_confirm_false_maps_dry_run_true():
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    assert mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=False)["dry_run"] is True


def test_confirm_true_maps_dry_run_false():
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    assert mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=True)["dry_run"] is False


def test_params_have_no_target_user_id():
    """薄壳不接受任意目标 user_id，杜绝跨人写（G4）。"""
    mod = importlib.import_module("mcp_servers.save_workhour_mcp_server")
    p = mod._build_params("AI平台", "2026-05-10", 8, "开发", confirm=True)
    assert "user_id" not in p and "memberId" not in p
