"""
Save Workhour Tool - 工时填报工具

填报工时到工时管理系统，支持：
- 参数校验（工时步长 0.5h、日期格式、当日合计 ≤ 24h）
- 调用 SpringBoot API 保存工时记录

工具名：save_workhour
"""

import logging
import os
from datetime import datetime, date
from typing import Any, Dict, Optional

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

SAVE_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目ID（必填）",
        },
        "date": {
            "type": "string",
            "description": "工时日期，格式 YYYY-MM-DD（必填）",
        },
        "duration": {
            "type": "number",
            "description": "工时时长（小时），必须为 0.5 的整数倍，取值范围 0.5 ~ 24",
            "minimum": 0.5,
            "maximum": 24,
        },
        "description": {
            "type": "string",
            "description": "工作内容描述（可选）",
        },
        "user_id": {
            "type": "string",
            "description": "用户ID（可选，不填则使用当前登录用户）",
        },
    },
    "required": ["project_id", "date", "duration"],
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
    if duration > 24:
        return "单次填报工时不能超过 24 小时"
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
    return None


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
    user_id: Optional[str] = kwargs.get("user_id")

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
        request_headers["Authorization"] = auth_token

    existing_hours = await _get_daily_total(
        user_id=user_id,
        date_str=date_str,
        base_url=base_url,
        headers=request_headers,
    )
    if existing_hours + duration > 24:
        return {
            "success": False,
            "error": (
                f"填报后当日工时合计将超过 24 小时（已有 {existing_hours}h，"
                f"本次 {duration}h，合计 {existing_hours + duration}h）"
            ),
        }

    # 3. 构建请求体并调用 SpringBoot API
    payload: Dict[str, Any] = {
        "projectId": project_id,
        "workhourDate": date_str,
        "workhour": duration,
    }
    if description:
        payload["description"] = description
    if user_id:
        payload["memberId"] = user_id

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
            "message": f"工时填报成功：{date_str} {duration}h（项目 {project_id}）",
            "record_id": str(saved_id) if saved_id else None,
            "project_id": project_id,
            "date": date_str,
            "duration": duration,
            "description": description,
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"工时填报 API 调用失败: {e}")
        return {"success": False, "error": f"服务调用失败: HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        logger.error(f"工时填报网络错误: {e}")
        return {"success": False, "error": f"网络请求失败: {e}"}
    except Exception as e:
        logger.error(f"工时填报异常: {e}", exc_info=True)
        return {"success": False, "error": f"填报异常: {e}"}


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
            description="填报工时记录，将指定项目的工时保存到系统，支持时长校验（0.5h 步长，日合计 ≤ 24h）",
            json_schema=SAVE_WORKHOUR_SCHEMA,
            handler=save_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=30,
            requires_permission=True,
        )
        logger.info("工时填报工具注册成功")
    except Exception as e:
        logger.error(f"工时填报工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_save_workhour_tool()
