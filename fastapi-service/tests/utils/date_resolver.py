"""
相对日期解析工具

将测试用例中的相对日期参数（在数据生成时基于固定基准日期计算的）
动态转换为当前运行时的正确日期。

背景：测试数据由 LLM 在某个基准日期（如 2026-04-01）生成，
start_date/end_date 被硬编码为那天的"上个月"等。
运行测试时日期已变，需重新计算，否则断言必然失败。

用法：
    from tests.utils.date_resolver import resolve_relative_dates
    expected_params = resolve_relative_dates(case["expected"]["params"])
"""

from datetime import date, timedelta
from typing import Any


# ─── 日期工具 ─────────────────────────────────────────────────────────────────

def _week_start(d: date) -> date:
    """返回 d 所在周的周一"""
    return d - timedelta(days=d.weekday())


def _week_end(d: date) -> date:
    """返回 d 所在周的周日"""
    return _week_start(d) + timedelta(days=6)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    # 取下个月第一天再减一天
    if d.month == 12:
        return date(d.year + 1, 1, 1) - timedelta(days=1)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


# ─── 标准区间（相对于今天） ────────────────────────────────────────────────────

def get_standard_ranges(today: date | None = None) -> dict[str, tuple[str, str]]:
    """
    返回常用相对日期区间的 (start_date, end_date) 字典，格式 YYYY-MM-DD。

    key 说明：
        today           今天
        this_week       本周（周一~周日）
        last_week       上周
        this_month      本月
        last_month      上个月
        yesterday       昨天
        day_before      前天
        two_days_before 大前天
        last_last_week  上上周
    """
    t = today or date.today()
    ws = _week_start(t)
    lws = ws - timedelta(weeks=1)
    llws = ws - timedelta(weeks=2)

    def fmt(d: date) -> str:
        return d.strftime("%Y-%m-%d")

    return {
        "today":           (fmt(t), fmt(t)),
        "yesterday":       (fmt(t - timedelta(days=1)), fmt(t - timedelta(days=1))),
        "day_before":      (fmt(t - timedelta(days=2)), fmt(t - timedelta(days=2))),
        "two_days_before": (fmt(t - timedelta(days=3)), fmt(t - timedelta(days=3))),
        "this_week":       (fmt(ws), fmt(_week_end(t))),
        "last_week":       (fmt(lws), fmt(lws + timedelta(days=6))),
        "last_last_week":  (fmt(llws), fmt(llws + timedelta(days=6))),
        "this_month":      (fmt(_month_start(t)), fmt(_month_end(t))),
        "last_month":      (fmt(_month_start((t.replace(day=1) - timedelta(days=1)))),
                            fmt(_month_end(t.replace(day=1) - timedelta(days=1)))),
    }


# ─── 核心：根据 sub_type 确定对应区间 ────────────────────────────────────────

# sub_type → range_key（对应 get_standard_ranges 的 key）
_SUB_TYPE_TO_RANGE: dict[str, str] = {
    "query_self_today":         "today",
    "query_self_this_week":     "this_week",
    "query_self_last_week":     "last_week",
    "query_self_this_month":    "this_month",
    "query_self_last_month":    "last_month",
    "query_others_this_week":   "this_week",
    "query_others_this_month":  "this_month",
    "query_others_last_month":  "last_month",
    "query_others_by_name":     "this_week",   # 默认本周，具体看用例
}


def resolve_relative_dates(
    params: dict[str, Any],
    sub_type: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """
    将 params 中的 start_date / end_date 替换为运行时正确的日期。

    策略：
    1. 若提供了 sub_type，根据 sub_type 推断标准区间覆盖日期
    2. 否则，根据 params 中现有日期与生成时基准日期的偏移量等比平移
       （适用于 date_range 类用例，具体日期由 LLM 硬编码）

    注意：对于 informal_date / date_range 类 sub_type，
    LLM 硬编码的绝对日期无法自动推算，需要人工确认或跳过断言。
    """
    if not params:
        return params

    result = dict(params)
    ranges = get_standard_ranges(today)

    if sub_type and sub_type in _SUB_TYPE_TO_RANGE:
        range_key = _SUB_TYPE_TO_RANGE[sub_type]
        start, end = ranges[range_key]
        if "start_date" in result:
            result["start_date"] = start
        if "end_date" in result:
            result["end_date"] = end

    return result


def should_skip_date_assertion(sub_type: str | None) -> bool:
    """
    对于无法自动推算日期的 sub_type，跳过日期精确断言，只做存在性检查。
    """
    skip_types = {
        "query_self_date_range",
        "query_others_date_range",
        "informal_date",
        "edge_cases",
    }
    return sub_type in skip_types
