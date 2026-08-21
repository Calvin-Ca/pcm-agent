"""
Approve Workhour Tool - 工时审核工具

审核（通过）工时记录，支持批量操作。
权限：仅 deptAdmin 及以上角色，或项目负责人可调用。

工具名：approve_workhour
"""

import logging
import os
from typing import Any, Dict, List, Union

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

APPROVE_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "workhour_ids": {
            "oneOf": [
                {"type": "string", "description": "单条工时记录ID"},
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "工时记录ID列表（批量审核）",
                    "minItems": 1,
                },
            ],
            "description": "要审核的工时记录ID，支持单个ID（字符串）或多个ID（数组）",
        },
        "action": {
            "type": "string",
            "enum": ["approve"],
            "description": "审核动作：approve（通过）",
        },
    },
    "required": ["workhour_ids", "action"],
    "additionalProperties": False,
}


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def approve_workhour_handler(**kwargs) -> Dict[str, Any]:
    """
    工时审核工具处理函数。

    流程：
    1. 标准化 workhour_ids 为列表
    2. 校验 action（目前只支持 approve）
    3. 调用 POST /api/workhour/batch-approve
    """
    auth_token = kwargs.pop("auth_token", None)

    raw_ids: Union[str, List[str]] = kwargs.get("workhour_ids", [])
    action: str = kwargs.get("action", "approve")

    # 1. 标准化 ID 列表
    if isinstance(raw_ids, str):
        workhour_ids = [raw_ids]
    else:
        workhour_ids = list(raw_ids)

    if not workhour_ids:
        return {"success": False, "error": "工时记录ID（workhour_ids）不能为空"}

    # 2. 校验 action
    if action != "approve":
        return {
            "success": False,
            "error": f"暂不支持的审核动作：{action}。目前仅支持 approve（通过）。",
        }

    # 3. 调用后端
    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )
    headers: Dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = auth_token

    try:
        url = f"{base_url}/api/workhour/batch-approve"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=workhour_ids, headers=headers)
            response.raise_for_status()

        count = len(workhour_ids)
        msg = f"工时审核成功：已通过 {count} 条工时记录"
        return {
            "success": True,
            "message": msg,
            "approved_count": count,
            "workhour_ids": workhour_ids,
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"工时审核 API 调用失败: {e}")
        if e.response.status_code == 403:
            return {"success": False, "error": "权限不足：您没有审核工时的权限"}
        if e.response.status_code >= 500:
            return {
                "success": False,
                "status": "unknown",
                "error_code": "WRITE_RESULT_UNKNOWN",
                "error": "提交结果未知，请查询确认",
                "message": "提交结果未知，请查询确认",
            }
        return {"success": False, "error": f"服务调用失败: HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        logger.error(f"工时审核网络错误: {e}")
        return {
            "success": False,
            "status": "unknown",
            "error_code": "WRITE_RESULT_UNKNOWN",
            "error": "提交结果未知，请查询确认",
            "message": "提交结果未知，请查询确认",
        }
    except Exception as e:
        logger.error(f"工时审核异常: {e}", exc_info=True)
        return {
            "success": False,
            "status": "unknown",
            "error_code": "WRITE_RESULT_UNKNOWN",
            "error": "提交结果未知，请查询确认",
            "message": "提交结果未知，请查询确认",
        }


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_approve_workhour_tool():
    """注册工时审核工具"""
    try:
        tool_registry.register_tool(
            name="approve_workhour",
            description=(
                "审核（通过）工时记录（适用：审核工时/批准工时/通过工时申请）。"
                "部门管理员及以上角色（deptAdmin+）可审核；"
                "项目负责人（project_manager）可审核其负责项目的工时。"
                "支持单条或批量审核，传入工时记录ID即可。"
            ),
            json_schema=APPROVE_WORKHOUR_SCHEMA,
            handler=approve_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=30,
            requires_permission=True,
            is_write=True,
        )
        logger.info("工时审核工具注册成功")
    except Exception as e:
        logger.error(f"工时审核工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_approve_workhour_tool()
