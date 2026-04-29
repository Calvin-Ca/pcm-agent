"""
Batch Save Workhour Tool - 批量工时填报工具

让用户粘贴自由文本或表格文本，AI 自动解析成 N 条工时记录，
预览确认后批量入库。

工具名：batch_save_workhour
"""

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.param_resolver import resolve_project_id, _fetch_user_recent_projects, _find_best_match
from app.services.work_type_resolver import resolve_work_type
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ─── 配置常量 ─────────────────────────────────────────────────────────────────

BATCH_MAX_RECORDS = 30
BATCH_TEXT_MAX_LEN = 5000
BATCH_DAILY_HOUR_LIMIT = float(os.getenv("BATCH_DAILY_HOUR_LIMIT", "8"))
BATCH_DAILY_HOUR_BLOCK = 24.0

# ─── JSON Schema ──────────────────────────────────────────────────────────────

BATCH_SAVE_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": (
                "用户提供的工时描述文本，可以是自由文本或表格文本。"
                "例如：'本周做的事：周一上午做了A项目，下午开B项目需求会...'"
            ),
        },
        "dry_run": {
            "type": "boolean",
            "description": "true=仅预览解析结果不写库；false=实际入库（必须先 dry_run 让用户确认）",
            "default": True,
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}

# ─── LLM 解析 Tool Schema（强类型输出）─────────────────────────────────────────

PARSE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_workhour_records",
        "description": "把用户文本解析为结构化工时记录数组",
        "parameters": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "ISO YYYY-MM-DD"},
                            "project_name": {"type": "string", "description": "项目名称（可模糊）"},
                            "hours": {"type": "number", "description": "工时数（0.5 倍数）"},
                            "work_type": {
                                "type": "string",
                                "enum": ["研发工作", "商务工作", "综合管理工作", "履约工作", "需求工作"],
                            },
                            "content": {"type": "string", "description": "工作内容（≤200字）"},
                            "confidence": {
                                "type": "number",
                                "description": "解析置信度 0~1，<0.7 时标黄提示用户确认",
                            },
                        },
                        "required": ["date", "project_name", "hours", "work_type", "content", "confidence"],
                    },
                },
                "unparsed_segments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "无法解析的原文片段，让用户感知到丢失",
                },
            },
            "required": ["records"],
        },
    },
}


# ─── LLM 解析阶段 ─────────────────────────────────────────────────────────────

async def _parse_text_to_records(text: str, today: str) -> Dict[str, Any]:
    """LLM 解析自由文本为结构化记录"""
    if len(text) > BATCH_TEXT_MAX_LEN:
        text = text[:BATCH_TEXT_MAX_LEN]

    llm_client = LLMClient(env_prefix="CHAT_LLM", temperature=0.1, max_tokens=2000)

    # 计算本周各天日期，辅助 LLM 解析
    from datetime import datetime as _dt, timedelta as _td
    _today_obj = _dt.strptime(today, "%Y-%m-%d").date()
    _weekday = _today_obj.weekday()  # 0=周一
    _monday = _today_obj - _td(days=_weekday)
    _week_days = [(_monday + _td(days=i)).isoformat() for i in range(7)]

    parse_prompt = f"""你是工时填报助手。把用户提供的工时描述文本解析为结构化记录数组。

**今天的日期是 {today}**
**本周各天对应日期：**
- 本周一 = {_week_days[0]}，本周二 = {_week_days[1]}，本周三 = {_week_days[2]}
- 本周四 = {_week_days[3]}，本周五 = {_week_days[4]}，本周六 = {_week_days[5]}，本周日 = {_week_days[6]}
- 上周一 = {(_monday - _td(days=7)).isoformat()}，下周一 = {(_monday + _td(days=7)).isoformat()}

**解析规则：**
1. 日期统一输出 ISO 格式 YYYY-MM-DD。用户说"这周"的"周一"=本周一({_week_days[0]})，不要算错。
2. "上午"=4h，"下午"=4h，"全天"=8h，"半天"=4h；优先采纳明确写出的小时数
3. 工时数必须是 0.5 的倍数
4. work_type 必须是这 5 个之一：研发工作 / 商务工作 / 综合管理工作 / 履约工作 / 需求工作；不确定时默认"研发工作"
5. 一天内有多条记录（如"上午做A，下午做B"）必须拆分为**多条独立记录**，每条有自己的 project_name 和 hours
6. 解析不到日期/项目/工时数任一字段时，仍输出该记录但 confidence<0.5
7. unparsed_segments **只放完全无法解析成任何记录的原文片段**，已成功解析的内容不要放入
8. 如果用户只提供了"周一到周五"这种范围描述但没有具体每天的内容，为每一天生成一条记录，使用相同的项目名和工时数
9. 表格文本中，表头行不要作为数据解析

用户文本：
{text}
"""

    try:
        result = await llm_client.generate_with_tools(
            messages=[
                {"role": "system", "content": "你是一个专业的工时文本解析助手。必须调用 parse_workhour_records 工具输出结构化结果。"},
                {"role": "user", "content": parse_prompt},
            ],
            tools=[PARSE_TOOL_SCHEMA],
            tool_choice="required",
            temperature=0.1,
            max_tokens=2000,
        )
    except Exception as e:
        logger.error(f"LLM 解析批量工时文本失败: {e}")
        return {"records": [], "unparsed_segments": [text], "parse_error": str(e)}

    if result.get("finish_reason") != "tool_calls":
        logger.warning(f"LLM 未返回 tool_calls，finish_reason={result.get('finish_reason')}")
        return {"records": [], "unparsed_segments": [text], "parse_error": "LLM 未能解析文本"}

    tool_calls = result.get("tool_calls", [])
    if not tool_calls:
        return {"records": [], "unparsed_segments": [text], "parse_error": "LLM 未返回解析结果"}

    args = tool_calls[0].get("arguments", {})
    records = args.get("records", [])
    unparsed = args.get("unparsed_segments", [])

    # 截断超过上限的记录
    truncated = False
    if len(records) > BATCH_MAX_RECORDS:
        records = records[:BATCH_MAX_RECORDS]
        truncated = True

    return {
        "records": records,
        "unparsed_segments": unparsed,
        "truncated": truncated,
    }


# ─── 规范化 + 校验阶段 ────────────────────────────────────────────────────────

async def _resolve_project_for_record(
    record: Dict[str, Any],
    auth_token: Optional[str],
    base_url: str,
    user_id: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    解析项目名 → ID，失败时返回建议的项目名。
    Returns: (project_id, project_name, suggested_name)
    """
    project_name = record.get("project_name", "").strip()
    if not project_name:
        return None, project_name, None

    resolved_id, err = await resolve_project_id(project_name, auth_token, base_url, user_id=user_id)
    if resolved_id:
        return resolved_id, project_name, None

    # 尝试建议最接近的项目名
    suggested = await _suggest_similar_project(project_name, auth_token, base_url, user_id)
    return None, project_name, suggested


async def _suggest_similar_project(
    name: str,
    auth_token: Optional[str],
    base_url: str,
    user_id: Optional[str],
) -> Optional[str]:
    """为未匹配的项目名建议最接近的项目"""
    if not user_id:
        return None

    try:
        recent = await _fetch_user_recent_projects(user_id, auth_token, base_url, months_back=3)
        if recent:
            best = _find_best_match(name, recent)
            if best:
                return best.get("name")
    except Exception as e:
        logger.debug(f"建议相似项目失败: {e}")

    return None


def _is_half_step(value: float) -> bool:
    """检查是否为 0.5 的整数倍"""
    return abs(round(value * 2) - value * 2) < 1e-9


def _normalize_hours(raw_hours: Any) -> float:
    """归一化工时数，确保是 0.5 的倍数"""
    try:
        h = float(raw_hours)
    except (TypeError, ValueError):
        return 0.0

    # 四舍五入到最近的 0.5
    rounded = round(h * 2) / 2
    # 限制在合理范围
    if rounded <= 0:
        rounded = 0.5
    if rounded > 24:
        rounded = 24.0
    return rounded


async def _fetch_workhour_by_range(
    user_id: Optional[str],
    start_date: str,
    end_date: str,
    auth_token: Optional[str],
    base_url: str,
) -> List[Dict[str, Any]]:
    """调用 SpringBoot 查询日期范围内已有工时"""
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    params: Dict[str, Any] = {"startDate": start_date, "endDate": end_date}
    if user_id:
        params["memberId"] = user_id

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/api/workhour/by-date-range",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"查询已有工时失败: {e}")
        return []

    if isinstance(data, dict):
        data = data.get("data", data.get("content", []))
    if not isinstance(data, list):
        return []

    return data


async def _validate_records(
    records: List[Dict[str, Any]],
    user_id: Optional[str],
    auth_token: Optional[str],
    base_url: str,
) -> Dict[str, Any]:
    """规范化 + 重复检测 + 日上限校验"""

    # 1) 并发解析项目名 → ID
    resolved_records = []
    for r in records:
        proj_id, proj_name, suggested = await _resolve_project_for_record(
            r, auth_token, base_url, user_id
        )
        warnings: List[str] = []
        if not proj_id:
            if suggested:
                warnings.append(f"项目名'{proj_name}'未识别，最接近：'{suggested}'")
            else:
                warnings.append(f"项目名'{proj_name}'未识别")

        # 保存原始工时数（用于 blocker 检测）
        try:
            raw_hours = float(r.get("hours", 0))
        except (TypeError, ValueError):
            raw_hours = 0.0
            warnings.append("工时数无效，已设为默认值")

        # work_type 校验
        work_type = r.get("work_type", "研发工作")
        if work_type not in ("研发工作", "商务工作", "综合管理工作", "履约工作", "需求工作"):
            work_type = "研发工作"
            warnings.append("工时类型无效，已默认设为研发工作")

        resolved_records.append({
            "date": r.get("date", ""),
            "project_id": proj_id,
            "project_name": proj_name,
            "raw_hours": raw_hours,
            "work_type": work_type,
            "content": (r.get("content", "") or "")[:200],
            "confidence": float(r.get("confidence", 0.5)),
            "warnings": warnings,
            "suggested_project": suggested,
        })

    # 2) 拉现有工时（用于重复检测和日上限）
    dates = sorted({r["date"] for r in resolved_records if r["date"]})
    existing: List[Dict[str, Any]] = []
    if dates and user_id:
        existing = await _fetch_workhour_by_range(
            user_id, dates[0], dates[-1], auth_token, base_url
        )

    # 3) 重复检测 + 日上限计算
    daily_total: Dict[str, float] = defaultdict(float)
    duplicates: List[Dict[str, Any]] = []

    for r in resolved_records:
        if not r["date"]:
            continue

        # 累加该日已有工时（使用原始工时数做 blocker 检测）
        existing_today = sum(
            float(e.get("workhour", 0) or 0)
            for e in existing
            if e.get("workhourDate", "").startswith(r["date"]) or e.get("date") == r["date"]
        )
        daily_total[r["date"]] = existing_today + r["raw_hours"]

        # 重复检测（同日同项目）
        if r["project_id"]:
            for e in existing:
                e_date = e.get("workhourDate", "")[:10] if e.get("workhourDate") else e.get("date", "")
                if e_date == r["date"] and str(e.get("projectId", "")) == r["project_id"]:
                    duplicates.append({
                        "date": r["date"],
                        "project_id": r["project_id"],
                        "project_name": r["project_name"],
                        "existing_hours": float(e.get("workhour", 0) or 0),
                        "new_hours": r["raw_hours"],
                    })
                    r["warnings"].append(
                        f"该日期该项目已有 {e.get('workhour', 0)}h 记录"
                    )
                    break

    # 4) 阈值判定（基于原始工时数）
    daily_warnings: List[Dict[str, Any]] = []
    daily_blockers: List[Dict[str, Any]] = []

    for d, total in daily_total.items():
        if total > BATCH_DAILY_HOUR_BLOCK:
            daily_blockers.append({
                "date": d,
                "total_hours": total,
                "level": "blocker",
                "message": f"该日合计 {total}h 超过物理上限 {BATCH_DAILY_HOUR_BLOCK}h，必须修正后才能提交",
            })
        elif total > BATCH_DAILY_HOUR_LIMIT:
            daily_warnings.append({
                "date": d,
                "total_hours": total,
                "level": "warning",
                "message": f"该日合计 {total}h，超过建议上限 {BATCH_DAILY_HOUR_LIMIT}h",
            })

    # 5) 工时数归一化（blocker 检测后执行）
    for r in resolved_records:
        hours = _normalize_hours(r["raw_hours"])
        if hours <= 0:
            hours = 4.0
            r["warnings"].append("工时数无效，已设为默认值 4.0h")
        if not _is_half_step(r["raw_hours"]):
            r["warnings"].append(f"工时数已归一化为 {hours}h")
        r["hours"] = hours
        del r["raw_hours"]  # 清理临时字段

    return {
        "records": resolved_records,
        "duplicates": duplicates,
        "daily_warnings": daily_warnings,
        "daily_blockers": daily_blockers,
    }


# ─── 友好预览文本生成 ─────────────────────────────────────────────────────────

_WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _date_to_weekday_zh(date_str: str) -> str:
    """ISO 日期转中文星期"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return _WEEKDAY_ZH[d.weekday()]
    except ValueError:
        return ""


def _format_preview_text(
    parsed: Dict[str, Any],
    unparsed_segments: List[str],
    truncated: bool,
) -> str:
    """生成 emoji + 对齐的预览文本"""
    records = parsed.get("records", [])
    daily_warnings = parsed.get("daily_warnings", [])
    daily_blockers = parsed.get("daily_blockers", [])

    total_hours = sum(r["hours"] for r in records)
    lines = [f"📋 解析到 {len(records)} 条工时记录，预计总计 {total_hours} 小时", ""]

    for r in records:
        if r.get("warnings"):
            icon = "🟡" if r["confidence"] >= 0.7 else "⚠️"
        else:
            icon = "✅"

        weekday = _date_to_weekday_zh(r["date"])
        proj_display = r["project_name"] or "?"
        content = r["content"] or ""

        lines.append(
            f"{icon} {r['date']} ({weekday})  {proj_display:<12} {r['hours']:>5.1f}h  "
            f"{r['work_type']:<8}  \"{content}\""
        )

        for w in r["warnings"]:
            lines.append(f"     [{w}]")

    # 日上限警告
    if daily_warnings:
        lines.extend(["", "⚠️ 每日工时警告："])
        for dw in daily_warnings:
            lines.append(f"   - {dw['date']}：{dw['message']}")

    # 日上限拒绝
    if daily_blockers:
        lines.extend(["", "❌ 每日工时超限（必须修正）："])
        for db in daily_blockers:
            lines.append(f"   - {db['date']}：{db['message']}")

    # 未解析片段
    if unparsed_segments:
        lines.extend(["", "✏️ 未解析片段："])
        for s in unparsed_segments:
            lines.append(f"   - {s}")

    # 截断提示
    if truncated:
        lines.extend(["", f"⚠️ 超过单次上限 {BATCH_MAX_RECORDS} 条，仅解析前 {BATCH_MAX_RECORDS} 条，剩余请分批提交"])

    # 下一步引导
    blocked = bool(daily_blockers)
    if blocked:
        lines.extend(["", "❌ 存在超限记录，请修正后再提交。"])
    else:
        lines.extend(["", '请检查后回复"确认提交"或"取消"，或具体说明需要修改的条目。'])

    return "\n".join(lines)


# ─── 入库阶段 ─────────────────────────────────────────────────────────────────

async def _save_single_workhour(
    record: Dict[str, Any],
    user_id: Optional[str],
    auth_token: Optional[str],
    base_url: str,
) -> Dict[str, Any]:
    """单条工时入库，返回结果字典"""
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    # 查询工作日历
    workhour_type = await _get_workhour_type_for_date(record["date"], auth_token, base_url)

    # workType 推断
    resolved_work_type = await resolve_work_type(
        user_id or "", record["project_id"], record["content"], auth_token, base_url
    )

    payload = {
        "projectId": record["project_id"],
        "workhourDate": f"{record['date']}T00:00:00.000Z",
        "workhour": record["hours"],
        "workType": resolved_work_type,
        "workhourType": workhour_type,
        "workContent": record["content"],
    }
    if user_id:
        payload["memberId"] = user_id

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base_url}/api/workhour", json=payload, headers=headers)
            resp.raise_for_status()
            api_data = resp.json()

        saved_id = None
        if isinstance(api_data, dict):
            saved_id = (
                api_data.get("data", {}).get("id")
                if isinstance(api_data.get("data"), dict)
                else api_data.get("id")
            )

        return {
            "success": True,
            "record": record,
            "workhour_id": str(saved_id) if saved_id else None,
        }

    except httpx.HTTPStatusError as e:
        err_msg = _extract_error_message(e)
        suggested_fix = _suggest_fix(err_msg, record)
        return {
            "success": False,
            "record": record,
            "error_message": err_msg,
            "suggested_fix": suggested_fix,
        }
    except Exception as e:
        return {
            "success": False,
            "record": record,
            "error_message": f"网络异常: {e}",
            "suggested_fix": "请稍后重试，或联系管理员",
        }


async def _get_workhour_type_for_date(
    date_str: str,
    auth_token: Optional[str],
    base_url: str,
) -> str:
    """根据日期查询工作日历，返回应填报的工时类别"""
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        day_start = f"{date_str}T00:00:00Z"
        day_end = f"{date_str}T23:59:59Z"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/work-calendars/list",
                params={
                    "dateValue.greaterThanOrEqual": day_start,
                    "dateValue.lessThanOrEqual": day_end,
                    "isDeleted.equals": "0",
                    "page": 0,
                    "size": 1,
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        calendars = data if isinstance(data, list) else data.get("content", [])
        if calendars:
            is_work_day = str(calendars[0].get("isWorkDay", "1"))
            return "正常工时" if is_work_day == "1" else "其他工时"
    except Exception as e:
        logger.warning(f"查询工作日历失败({date_str}): {e}")

    return "正常工时"


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    """从 HTTP 错误中提取 SpringBoot 错误信息"""
    try:
        err_body = e.response.json()
        return (
            err_body.get("title")
            or (err_body.get("detail") if err_body.get("detail") != "null" else None)
            or err_body.get("message")
            or str(err_body)
        ) or f"服务调用失败 (HTTP {e.response.status_code})"
    except Exception:
        return e.response.text or f"服务调用失败 (HTTP {e.response.status_code})"


def _suggest_fix(error_message: str, record: Dict[str, Any]) -> str:
    """根据错误信息生成修复建议"""
    msg = error_message.lower()

    if "已存在" in msg or "重复" in msg:
        return f"建议先删除 {record['date']} 的已有记录，或修改日期"
    if "项目" in msg or "project" in msg:
        if record.get("suggested_project"):
            return f"项目名可能不准确，建议改为：'{record['suggested_project']}'"
        return "请确认项目名称是否正确或提供项目ID"
    if "超过" in msg or "上限" in msg:
        return f"建议减少工时数，当前 {record['hours']}h"
    if "未来" in msg:
        return "不能填报未来日期，请修改日期"
    if "权限" in msg:
        return "请确认您有权限填报该项目工时"

    return "请检查输入信息是否正确"


async def _save_records(
    records: List[Dict[str, Any]],
    user_id: Optional[str],
    auth_token: Optional[str],
    base_url: str,
) -> Dict[str, Any]:
    """循环调 POST /api/workhour，逐条入库，部分失败逐条返回原因"""
    succeeded: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for r in records:
        # 跳过无 project_id 的记录
        if not r.get("project_id"):
            failed.append({
                "date": r["date"],
                "project_name": r["project_name"],
                "hours": r["hours"],
                "error_message": f"项目名'{r['project_name']}'未识别",
                "suggested_fix": f"建议改为：'{r.get('suggested_project') or '确认项目名称'}'",
            })
            continue

        result = await _save_single_workhour(r, user_id, auth_token, base_url)
        if result["success"]:
            succeeded.append({
                "date": r["date"],
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "hours": r["hours"],
                "work_type": r["work_type"],
                "content": r["content"],
                "workhour_id": result.get("workhour_id"),
            })
        else:
            failed.append({
                "date": r["date"],
                "project_name": r["project_name"],
                "hours": r["hours"],
                "error_message": result["error_message"],
                "suggested_fix": result["suggested_fix"],
            })

    success_count = len(succeeded)
    failed_count = len(failed)
    total_hours = sum(s["hours"] for s in succeeded)

    summary_text = f"✅ 成功填报 {success_count} 条共 {total_hours} 小时"
    if failed_count > 0:
        summary_text += f"；❌ {failed_count} 条失败，详情见 failed_items"

    return {
        "success": failed_count == 0 or success_count > 0,
        "dry_run": False,
        "success_count": success_count,
        "failed_count": failed_count,
        "succeeded_items": succeeded,
        "failed_items": failed,
        "summary_text": summary_text,
    }


# ─── 主 Handler ───────────────────────────────────────────────────────────────

async def batch_save_workhour_handler(**kwargs) -> Dict[str, Any]:
    """
    批量工时填报工具处理函数。

    流程：
    1. LLM 解析文本为结构化记录
    2. 规范化 + 重复检测 + 日上限校验
    3. dry_run=true：返回预览文本
    4. dry_run=false：逐条入库
    """
    auth_token = kwargs.pop("auth_token", None)
    context = kwargs.pop("context", {}) or {}
    user_id = context.get("user_id") or kwargs.get("user_id")

    text = kwargs.get("text", "").strip()
    dry_run = kwargs.get("dry_run", True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ("true", "1", "yes")

    if not text:
        return {"success": False, "error": "文本（text）不能为空"}

    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )

    today = date.today().isoformat()

    # ── 1. LLM 解析 ──────────────────────────────────────────────────────────
    parse_result = await _parse_text_to_records(text, today)
    records = parse_result.get("records", [])
    unparsed_segments = parse_result.get("unparsed_segments", [])
    truncated = parse_result.get("truncated", False)
    parse_error = parse_result.get("parse_error")

    if parse_error:
        return {"success": False, "error": f"文本解析失败：{parse_error}"}

    if not records:
        return {
            "success": True,
            "dry_run": True,
            "preview_text": "未能从文本中解析到工时记录，请检查输入格式。",
            "parsed_records": [],
            "summary": {"total_records": 0, "estimated_total_hours": 0, "blocked": False},
        }

    # ── 2. 规范化 + 校验 ─────────────────────────────────────────────────────
    validation = await _validate_records(records, user_id, auth_token, base_url)
    validated_records = validation["records"]
    duplicates = validation["duplicates"]
    daily_warnings = validation["daily_warnings"]
    daily_blockers = validation["daily_blockers"]

    # ── 3. dry_run=true：返回预览 ────────────────────────────────────────────
    if dry_run:
        total_hours = sum(r["hours"] for r in validated_records)
        blocked = bool(daily_blockers)

        preview_text = _format_preview_text(
            {"records": validated_records, "daily_warnings": daily_warnings, "daily_blockers": daily_blockers},
            unparsed_segments,
            truncated,
        )

        return {
            "success": True,
            "dry_run": True,
            "preview_text": preview_text,
            "parsed_records": [
                {
                    "date": r["date"],
                    "project_id": r["project_id"],
                    "project_name": r["project_name"],
                    "hours": r["hours"],
                    "work_type": r["work_type"],
                    "content": r["content"],
                    "confidence": r["confidence"],
                    "warnings": r["warnings"],
                }
                for r in validated_records
            ],
            "duplicates": duplicates,
            "daily_warnings": daily_warnings,
            "daily_blockers": daily_blockers,
            "unparsed_segments": unparsed_segments,
            "summary": {
                "total_records": len(validated_records),
                "estimated_total_hours": total_hours,
                "blocked": blocked,
                "next_action": "请确认无误后调用 batch_save_workhour(dry_run=false)" if not blocked else "请先修正超限记录",
            },
        }

    # ── 4. dry_run=false：实际入库 ────────────────────────────────────────────
    # 过滤掉有 blockers 的记录（不允许提交）
    blocker_dates = {b["date"] for b in daily_blockers}
    records_to_save = [r for r in validated_records if r["date"] not in blocker_dates and r.get("project_id")]

    if not records_to_save:
        return {
            "success": False,
            "error": "所有记录均因超限或项目未识别而无法提交，请先修正。",
            "daily_blockers": daily_blockers,
        }

    save_result = await _save_records(records_to_save, user_id, auth_token, base_url)
    save_result["daily_blockers"] = daily_blockers
    save_result["unparsed_segments"] = unparsed_segments

    return save_result


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_batch_save_workhour_tool():
    """注册批量工时填报工具"""
    try:
        tool_registry.register_tool(
            name="batch_save_workhour",
            description=(
                "批量工时填报：解析用户提供的自然语言文本或表格，识别多条工时记录并批量入库。"
                "用户提到'批量填报/把这周/帮我把这段记录填了/这是我本月的工时清单'等场景调用。"
                "首次调用必须 dry_run=true 让用户预览，确认后才能 dry_run=false 实际入库。"
            ),
            json_schema=BATCH_SAVE_WORKHOUR_SCHEMA,
            handler=batch_save_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=120,
            requires_permission=True,
        )
        logger.info("批量工时填报工具注册成功")
    except Exception as e:
        logger.error(f"批量工时填报工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_batch_save_workhour_tool()
