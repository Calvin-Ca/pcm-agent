"""
Generate Weekly Report Tool - 周报生成工具

根据工时数据自动生成结构化周报，支持：
- 按自然语言指定周次（"本周"、"上周"、specific date）
- 按项目分组统计工时和占比
- 使用 LLM 生成 Markdown 格式工作总结

工具名：generate_weekly_report
"""

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

GENERATE_WEEKLY_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "user_id": {
            "type": "string",
            "description": "用户ID（可选，不填则查询当前登录用户）",
        },
        "week": {
            "type": "string",
            "description": (
                "周次参数（可选）。"
                "支持：'thisWeek'（本周）、'lastWeek'（上周）、"
                "'YYYY-WNN'（如 '2024-W01'）、"
                "'YYYY-MM-DD'（该日期所在周）。"
                "默认为本周。"
            ),
        },
    },
    "required": [],
    "additionalProperties": False,
}


# ─── 日期工具 ──────────────────────────────────────────────────────────────────

def _resolve_week_range(week: Optional[str]) -> tuple[date, date]:
    """
    将 week 参数解析为 (start_date, end_date)。

    支持：
    - None / "" / "thisWeek"  → 本周一到本周日
    - "lastWeek"              → 上周一到上周日
    - "YYYY-WNN"              → ISO 周（如 "2024-W01"）
    - "YYYY-MM-DD"            → 该日期所在周的周一到周日
    """
    today = date.today()

    if not week or week in ("thisWeek", "this_week", "本周"):
        monday = today - timedelta(days=today.weekday())
    elif week in ("lastWeek", "last_week", "上周"):
        monday = today - timedelta(days=today.weekday() + 7)
    elif week.startswith("2") and "-W" in week:
        # ISO 格式 "2024-W01"
        year_str, week_str = week.split("-W")
        monday = date.fromisocalendar(int(year_str), int(week_str), 1)
    else:
        # 尝试解析为日期，取所在周的周一
        try:
            ref_date = datetime.strptime(week, "%Y-%m-%d").date()
            monday = ref_date - timedelta(days=ref_date.weekday())
        except ValueError:
            monday = today - timedelta(days=today.weekday())

    sunday = monday + timedelta(days=6)
    return monday, sunday


def _week_label(start: date, end: date) -> str:
    """格式化周次标签，如 '2024年第3周（01/15-01/21）'"""
    year, week_num, _ = start.isocalendar()
    return f"{year}年第{week_num}周（{start.strftime('%m/%d')}-{end.strftime('%m/%d')}）"


# ─── 报告生成 ─────────────────────────────────────────────────────────────────

def _build_stats(records: List[Dict]) -> Dict[str, Any]:
    """从工时记录列表计算按项目分组的统计"""
    project_hours: Dict[str, float] = {}
    project_ids: Dict[str, str] = {}
    total = 0.0

    for r in records:
        name = r.get("project_name") or "未知项目"
        hours = float(r.get("duration", 0))
        project_hours[name] = project_hours.get(name, 0.0) + hours
        project_ids[name] = r.get("project_id", "")
        total += hours

    stats = []
    for name, hours in sorted(project_hours.items(), key=lambda x: -x[1]):
        pct = round(hours / total * 100, 1) if total > 0 else 0.0
        stats.append({
            "project_name": name,
            "project_id": project_ids[name],
            "hours": round(hours, 1),
            "percentage": pct,
        })

    return {"total_hours": round(total, 1), "projects": stats}


def _render_markdown(
    week_label: str,
    user_id: Optional[str],
    stats: Dict[str, Any],
    llm_summary: Optional[str],
) -> str:
    """渲染最终 Markdown 周报"""
    lines = [
        f"## 📋 周报 — {week_label}",
        "",
    ]
    if user_id:
        lines += [f"> 员工：{user_id}", ""]

    lines += [
        "### 工时统计",
        "",
        f"**本周合计：{stats['total_hours']} 小时**",
        "",
        "| 项目 | 工时(h) | 占比 |",
        "|------|---------|------|",
    ]
    for p in stats["projects"]:
        lines.append(f"| {p['project_name']} | {p['hours']} | {p['percentage']}% |")

    if llm_summary:
        lines += [
            "",
            "### 工作总结",
            "",
            llm_summary,
        ]

    return "\n".join(lines)


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def generate_weekly_report_handler(**kwargs) -> Dict[str, Any]:
    """
    周报生成工具处理函数。

    流程：
    1. 解析 week 参数 → 日期范围
    2. 调用 query_timesheet 获取工时记录
    3. 计算项目分组统计
    4. 调用 LLM 生成工作总结（可选，失败时跳过）
    5. 渲染 Markdown 周报
    """
    auth_token = kwargs.pop("auth_token", None)
    user_id: Optional[str] = kwargs.get("user_id")
    week: Optional[str] = kwargs.get("week")

    # 1. 解析日期范围
    start, end = _resolve_week_range(week)
    week_label = _week_label(start, end)

    # 2. 查询工时数据（复用 query_timesheet_handler）
    from app.tools.query_timesheet import query_timesheet_handler

    timesheet_result = await query_timesheet_handler(
        user_id=user_id,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        auth_token=auth_token,
    )

    if not timesheet_result.get("success"):
        return {
            "success": False,
            "error": timesheet_result.get("error", "工时查询失败"),
            "report": None,
        }

    records: List[Dict] = timesheet_result.get("records", [])

    # 3. 统计
    stats = _build_stats(records)

    # 4. LLM 生成工作总结（可选）
    llm_summary: Optional[str] = None
    if stats["total_hours"] > 0:
        try:
            llm_summary = await _generate_summary(stats, week_label)
        except Exception as e:
            logger.warning(f"LLM 周报摘要生成失败（跳过）: {e}")

    # 5. 渲染 Markdown
    report_md = _render_markdown(week_label, user_id, stats, llm_summary)

    return {
        "success": True,
        "week": week_label,
        "total_hours": stats["total_hours"],
        "projects": stats["projects"],
        "report": report_md,
    }


async def _generate_summary(stats: Dict[str, Any], week_label: str) -> str:
    """调用 LLM 生成工作总结段落"""
    from app.services.llm_client import LLMClient

    llm = LLMClient(env_prefix="CHAT_LLM")

    project_lines = "\n".join(
        f"- {p['project_name']}：{p['hours']}小时（{p['percentage']}%）"
        for p in stats["projects"]
    )

    pm = get_prompt_manager()
    stats_text = (
        f"以下是员工 {week_label} 的工时数据：\n\n"
        f"{project_lines}\n\n"
        f"总工时：{stats['total_hours']} 小时"
    )
    prompt = pm.format("weekly_report", stats_text=stats_text) or stats_text + "\n\n请用2-3句话撰写专业工作总结。"
    system_prompt_text = pm.get_str("weekly_report_system", file="weekly_report") or "你是一个专业的企业工时管理助手，帮助员工撰写简洁的工作总结。"

    return await llm.generate(
        prompt=prompt,
        system_prompt=system_prompt_text,
        temperature=0.5,
        max_tokens=200,
    )


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_generate_weekly_report_tool():
    """注册周报生成工具"""
    try:
        tool_registry.register_tool(
            name="generate_weekly_report",
            description="根据工时数据自动生成 Markdown 格式周报，包含项目分布统计和工作总结",
            json_schema=GENERATE_WEEKLY_REPORT_SCHEMA,
            handler=generate_weekly_report_handler,
            category=ToolCategory.REPORT,
            timeout=60,
            requires_permission=True,
        )
        logger.info("周报生成工具注册成功")
    except Exception as e:
        logger.error(f"周报生成工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_generate_weekly_report_tool()
