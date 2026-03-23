"""
直接测试 Phase 8 工具 handler（绕过 HTTP API）
用于验证工具逻辑本身是否正常工作
"""

import asyncio
import sys
sys.path.insert(0, '/app')

async def test_generate_weekly_report():
    """直接测试周报生成工具"""
    print("=" * 60)
    print("直接测试: generate_weekly_report")
    print("=" * 60)

    try:
        from app.tools.generate_weekly_report import generate_weekly_report_handler

        # 调用工具 handler
        result = await generate_weekly_report_handler(
            user_id="1",
            week="thisWeek"
        )

        print(f"\n结果:")
        print(f"  success: {result.get('success')}")
        print(f"  week: {result.get('week')}")
        print(f"  total_hours: {result.get('total_hours')}")
        print(f"  projects: {len(result.get('projects', []))}")

        if result.get('success'):
            print(f"\n  报告预览:")
            print(f"  {'-' * 50}")
            report = result.get('report', '')
            print(report[:800] if len(report) > 800 else report)
            print(f"  {'-' * 50}")
        else:
            print(f"\n  错误: {result.get('error')}")

        return result.get('success', False)

    except Exception as e:
        print(f"\n  ❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_save_workhour():
    """直接测试工时填报工具"""
    print("\n" + "=" * 60)
    print("直接测试: save_workhour")
    print("=" * 60)

    try:
        from app.tools.save_workhour import save_workhour_handler

        # 测试用例1: 正常填报
        print("\n  用例1: 正常填报")
        result = await save_workhour_handler(
            project_id="123",
            date="2024-03-20",
            duration=8.0,
            description="测试工作内容"
        )
        print(f"    success: {result.get('success')}")
        print(f"    message: {result.get('message', result.get('error', 'N/A'))}")

        # 测试用例2: 步长错误
        print("\n  用例2: 步长错误 (8.3小时)")
        result = await save_workhour_handler(
            project_id="123",
            date="2024-03-20",
            duration=8.3
        )
        print(f"    success: {result.get('success')}")
        print(f"    error: {result.get('error', 'N/A')}")

        # 测试用例3: 未来日期
        print("\n  用例3: 未来日期")
        result = await save_workhour_handler(
            project_id="123",
            date="2026-12-31",
            duration=8.0
        )
        print(f"    success: {result.get('success')}")
        print(f"    error: {result.get('error', 'N/A')}")

        return True

    except Exception as e:
        print(f"\n  ❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("\n" + "=" * 70)
    print(" Phase 8 工具直接测试（绕过 HTTP API）")
    print("=" * 70)

    # 测试周报生成
    report_ok = await test_generate_weekly_report()

    # 测试工时填报
    workhour_ok = await test_save_workhour()

    print("\n" + "=" * 70)
    print(" 测试完成 ")
    print("=" * 70)
    print(f"\n  周报生成: {'✅ 通过' if report_ok else '❌ 失败'}")
    print(f"  工时填报: {'✅ 通过' if workhour_ok else '❌ 失败'}")


if __name__ == "__main__":
    asyncio.run(main())
