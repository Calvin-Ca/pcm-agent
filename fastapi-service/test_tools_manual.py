"""
快速验证 Phase 8 工具实际效果的脚本

运行方式:
    cd fastapi-service
    python test_tools_manual.py
"""

import asyncio
from datetime import date, timedelta

# 测试周报生成
async def test_weekly_report():
    """测试周报生成功能（不需要外部依赖）"""
    from app.tools.generate_weekly_report import _resolve_week_range, _week_label, _build_stats, _render_markdown

    print("=" * 60)
    print("测试周报生成工具")
    print("=" * 60)

    # 测试日期解析
    test_cases = [None, "thisWeek", "lastWeek", "2024-W01", "2024-03-15"]
    for tc in test_cases:
        start, end = _resolve_week_range(tc)
        label = _week_label(start, end)
        print(f"  输入: {tc or 'None'} => {label}")

    # 测试统计和渲染（模拟数据）
    mock_records = [
        {"project_name": "项目A", "duration": 20, "project_id": "1"},
        {"project_name": "项目B", "duration": 15, "project_id": "2"},
        {"project_name": "项目A", "duration": 5, "project_id": "1"},  # 同名项目合并
    ]
    stats = _build_stats(mock_records)
    print(f"\n  统计结果: 总工时={stats['total_hours']}h")
    for p in stats["projects"]:
        print(f"    - {p['project_name']}: {p['hours']}h ({p['percentage']}%)")

    # 渲染周报
    report = _render_markdown("2024年第10周（03/04-03/10）", "user_001", stats, None)
    print("\n  生成的周报预览:")
    print("-" * 40)
    print(report)
    print("-" * 40)


# 测试工时填报验证
async def test_save_workhour_validation():
    """测试工时填报的校验逻辑"""
    from app.tools.save_workhour import _validate_duration, _validate_date

    print("\n" + "=" * 60)
    print("测试工时填报校验")
    print("=" * 60)

    # 测试时长校验
    duration_tests = [0.5, 1.0, 1.5, 2.3, 0, 25, 8.5]
    for d in duration_tests:
        err = _validate_duration(d)
        status = "✅" if err is None else f"❌ {err}"
        print(f"  时长 {d}h => {status}")

    # 测试日期校验
    date_tests = ["2024-03-15", "2024-03-25", "invalid", "2024/03/15"]  # 假设今天是3月24日
    for ds in date_tests:
        err = _validate_date(ds)
        status = "✅" if err is None else f"❌ {err}"
        print(f"  日期 {ds} => {status}")


# 端到端测试（需要后端服务）
async def test_e2e():
    """端到端测试（需要 SpringBoot 后端运行）"""
    print("\n" + "=" * 60)
    print("端到端测试（需要后端服务）")
    print("=" * 60)

    try:
        from app.tools.generate_weekly_report import generate_weekly_report_handler
        from app.tools.save_workhour import save_workhour_handler

        # 测试生成周报（需要实际的工时数据）
        result = await generate_weekly_report_handler(user_id="1", week="thisWeek")
        print(f"\n  周报生成结果: success={result.get('success')}")
        if result.get('success'):
            print(f"  总工时: {result.get('total_hours')}h")
            print(f"  项目数: {len(result.get('projects', []))}")
            # 只显示前 500 字符
            report = result.get('report', '')
            print(f"  报告预览:\n{report[:500]}...")

    except Exception as e:
        print(f"  ⚠️  跳过端到端测试: {e}")


if __name__ == "__main__":
    asyncio.run(test_weekly_report())
    asyncio.run(test_save_workhour_validation())
    # asyncio.run(test_e2e())  # 取消注释以运行端到端测试
