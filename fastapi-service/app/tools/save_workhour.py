"""
Save Workhour Tool - 工时填报工具

填报工时到工时管理系统，支持：
- 参数校验（工时步长 0.5h、日期格式、当日合计 ≤ 24h）
- 调用 SpringBoot API 保存工时记录

工具名：save_workhour
"""

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry
from app.services.param_resolver import resolve_project_id
from app.services.work_type_resolver import resolve_work_type
from app.tools._write_guard import resolve_dry_run

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

SAVE_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目ID或项目名称（必填）",
        },
        "date": {
            "type": "string",
            "description": "工时日期，格式 YYYY-MM-DD（必填，用户未提供时必须追问）",
        },
        "duration": {
            "type": "number",
            "description": "工时时长（小时），必须为 0.5 的整数倍，取值范围 0.5 ~ 10",
            "minimum": 0.5,
            "maximum": 10,
        },
        "description": {
            "type": "string",
            "description": "工作内容描述（可选）",
        },
        "user_id": {
            "type": "string",
            "description": "用户ID（可选，不填则使用当前登录用户）",
        },
        "member_name": {
            "type": "string",
            "description": "成员姓名（可选，仅有代填权限时使用；Handler 会解析为 user_id）",
        },
        "dry_run": {
            "type": "boolean",
            "description": "true=仅预览校验不写入；缺省由调用环境决定（MCP 写工具端点会强制默认 true）",
        },
    },
    # 保持 FC Schema 向后兼容；Agent 编排层和 TaskExecutor 会在调用
    # handler 前确定性拦截缺失的写参数。
    "required": [],
    "additionalProperties": False,
}


# ─── 参数校验 ─────────────────────────────────────────────────────────────────

def _validate_duration(duration: float) -> Optional[str]:
    """
    校验工时时长：
    - 必须为 0.5 的整数倍
    - 取值范围 [0.5, 24]
    返回错误信息，校验通过返回 None。
    """
    if duration <= 0:
        return "工时时长必须大于 0"
    if duration > 10:
        return "单次填报工时不能超过 10 小时"
    # 检查是否为 0.5 的倍数：乘以 2 后应为整数
    if abs(round(duration * 2) - duration * 2) > 1e-9:
        return f"工时时长必须为 0.5 的整数倍（当前值：{duration}）"
    return None


def _validate_date(date_str: str) -> Optional[str]:
    """
    校验日期格式（YYYY-MM-DD），不能是未来日期。
    返回错误信息，校验通过返回 None。
    """
    try:
        record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return f"日期格式无效：{date_str}，请使用 YYYY-MM-DD 格式"

    if record_date > date.today():
        return f"不能填报未来日期的工时：{date_str}"
    if record_date < date.today() - timedelta(days=90):
        return f"不能填报超过 90 天的历史工时：{date_str}"
    return None


# ─── 工作日历查询 ─────────────────────────────────────────────────────────────

async def _get_workhour_type_for_date(
    date_str: str,
    auth_token: Optional[str],
    base_url: str,
) -> str:
    """
    根据日期查询工作日历，返回应填报的工时类别。
    工作日 -> '正常工时'，非工作日 -> '其他工时'。
    查询失败时默认返回 '正常工时'。
    """
    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        # 构造当天 00:00:00 和 23:59:59 的 ISO 字符串用于范围查询
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


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def save_workhour_handler(**kwargs) -> Dict[str, Any]:
    """
    工时填报工具处理函数。

    流程：
    1. 参数校验（格式、步长、范围）
    2. 查询当日已有工时，验证总量不超过 24h
    3. 调用 SpringBoot POST /api/workhour 保存
    """
    auth_token = kwargs.pop("auth_token", None)

    project_id: str = kwargs.get("project_id", "")
    date_str: str = kwargs.get("date", "")
    duration: float = float(kwargs.get("duration", 0))
    description: str = kwargs.get("description", "")
    # user_id 可能来自顶层参数（chat 接口），也可能来自 user_context（internal 工具端点）
    user_ctx = kwargs.get("user_context", {}) or {}
    user_id: Optional[str] = kwargs.get("user_id") or user_ctx.get("user_id")
    member_name: Optional[str] = kwargs.get("member_name")
    # 默认直写；WRITE_DRY_RUN_DEFAULT=true 时强制预览（本地测试防误写生产）
    dry_run: bool = resolve_dry_run(kwargs, fallback=False)

    if member_name and not kwargs.get("user_id"):
        from app.services.param_resolver import resolve_member_id

        user_id, resolve_error = await resolve_member_id(member_name, auth_token)
        if resolve_error:
            return {"success": False, "error": resolve_error}

    # 1. 基础参数校验
    if not project_id:
        return {"success": False, "error": "项目ID（project_id）不能为空"}

    if not date_str:
        return {"success": False, "error": "日期（date）不能为空"}

    date_err = _validate_date(date_str)
    if date_err:
        return {"success": False, "error": date_err}

    duration_err = _validate_duration(duration)
    if duration_err:
        return {"success": False, "error": duration_err}

    # 2. 查询当日已有工时，防止超过 24h
    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )
    request_headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        request_headers["Authorization"] = token

    # 2a. 解析 project_id：LLM 可能填入项目名称（如"AI平台"），需转换为数字 ID
    original_project_name = project_id  # 保留用户输入的项目名称，用于展示
    resolved_project_id, project_err = await resolve_project_id(project_id, auth_token, base_url, user_id=user_id)
    if project_err:
        return {"success": False, "error": f"项目解析失败：{project_err}"}
    project_id = resolved_project_id

    # 注：每日工时上限由 SpringBoot 后端根据 work_calendar 精确校验
    # ai-service 不做预校验，避免上限值不准（如 7.5h 工作日上限是 8h）
    # SpringBoot 超限会返回 "当天正常工时总和不能超过X小时"，B1 修复已保证错误信息透出

    # 2b. 查询工作日历，确定工时类别
    workhour_type = await _get_workhour_type_for_date(date_str, auth_token, base_url)

    # 2c. 智能推断 workType（(user, project) 二维众数 → user 单维 → LLM → 默认）
    resolved_work_type = await resolve_work_type(
        user_id or "", project_id, description, auth_token, base_url
    )

    # 3. 构建请求体并调用 SpringBoot API
    # workhourDate 需要 ISO instant 格式（SpringBoot Instant 类型）
    payload: Dict[str, Any] = {
        "projectId": project_id,
        "workhourDate": f"{date_str}T00:00:00Z",
        "workhour": duration,
        "workType": resolved_work_type,
        "workhourType": workhour_type,
    }
    if description:
        payload["workContent"] = description
    if user_id:
        payload["memberId"] = user_id

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "preview": {
                "payload": payload,
                "summary": (
                    f"预览（未写入）：{date_str} {duration}h，"
                    f"项目 {original_project_name}，类别 {workhour_type}/{resolved_work_type}"
                ),
            },
            "message": "以上为预览，确认无误后再提交。",
        }

    try:
        url = f"{base_url}/api/workhour"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=request_headers)
            response.raise_for_status()

            api_data = response.json()

        # 兼容 SpringBoot 两种响应格式
        if isinstance(api_data, dict):
            if not api_data.get("success", True):
                return {
                    "success": False,
                    "error": api_data.get("message", "工时填报失败"),
                }
            saved_id = (
                api_data.get("data", {}).get("id")
                if isinstance(api_data.get("data"), dict)
                else api_data.get("id")
            )
        else:
            # 假定直接返回保存的对象
            saved_id = getattr(api_data, "id", None) if not isinstance(api_data, (int, str)) else api_data

        return {
            "success": True,
            "message": f"工时填报成功：{date_str} {duration}h（项目 {original_project_name}）",
            "record_id": str(saved_id) if saved_id else None,
            "project_name": original_project_name,
            "date": date_str,
            "duration": duration,
            "description": description,
        }

    except httpx.HTTPStatusError as e:
        try:
            err_body = e.response.json()
            # SpringBoot ProblemDetail 格式（JHIPSTER）：
            # - title: 用户友好的错误文案（BadRequestAlertException 的标题）
            # - detail: 可能为 "null" 字符串
            # - message: i18n key（如 error.dailyWorkhourExceeded）
            # 优先级：title > detail(过滤 "null") > message
            err_msg = (
                err_body.get("title")
                or (err_body.get("detail") if err_body.get("detail") != "null" else None)
                or err_body.get("message")
                or str(err_body)
            )
        except Exception:
            err_msg = e.response.text or ""
        logger.error(f"工时填报 API 调用失败: HTTP {e.response.status_code}, body: {err_msg}, payload: {payload}")
        if e.response.status_code >= 500:
            return {
                "success": False,
                "status": "unknown",
                "error_code": "WRITE_RESULT_UNKNOWN",
                "error": "提交结果未知，请查询确认",
                "message": "提交结果未知，请查询确认",
            }
        # 直接透传 SpringBoot 的原始错误文案，不包 HTTP 前缀
        return {"success": False, "error": err_msg or f"服务调用失败 (HTTP {e.response.status_code})"}
    except httpx.HTTPError as e:
        logger.error(f"工时填报网络错误: {e}")
        return {
            "success": False,
            "status": "unknown",
            "error_code": "WRITE_RESULT_UNKNOWN",
            "error": "提交结果未知，请查询确认",
            "message": "提交结果未知，请查询确认",
        }
    except Exception as e:
        logger.error(f"工时填报异常: {e}", exc_info=True)
        return {
            "success": False,
            "status": "unknown",
            "error_code": "WRITE_RESULT_UNKNOWN",
            "error": "提交结果未知，请查询确认",
            "message": "提交结果未知，请查询确认",
        }


async def _get_daily_total(
    user_id: Optional[str],
    date_str: str,
    base_url: str,
    headers: Dict[str, str],
) -> float:
    """
    查询指定日期已填报的工时合计（小时）。
    查询失败时返回 0，不阻断主流程。
    """
    try:
        url = f"{base_url}/api/workhour/by-date-range"
        params: Dict[str, Any] = {"startDate": date_str, "endDate": date_str}
        if user_id:
            params["memberId"] = user_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            records = resp.json()

        if not isinstance(records, list):
            records = records.get("data", []) if isinstance(records, dict) else []

        return sum(float(r.get("workhour", 0) or 0) for r in records)

    except Exception as e:
        logger.warning(f"查询当日工时合计失败（跳过验证）: {e}")
        return 0.0


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_save_workhour_tool():
    """注册工时填报工具"""
    try:
        tool_registry.register_tool(
            name="save_workhour",
            description="填报/记录单条工时（适用：填工时/记录工时/登记工作时间/帮我填）。只有用户已明确提供项目、日期和时长时才能调用；缺少任一项时必须先追问，不得自行补默认值。project_id 可直接填项目名称，系统内部自动解析为ID。",
            json_schema=SAVE_WORKHOUR_SCHEMA,
            handler=save_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=30,
            requires_permission=True,
            is_write=True,
        )
        logger.info("工时填报工具注册成功")
    except Exception as e:
        logger.error(f"工时填报工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_save_workhour_tool()
