"""
Export Report Tool - 工时报表导出工具

导出工时汇总报表为 Excel 文件。
权限：仅 deptAdmin 及以上角色可调用。

工具名：export_report
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)

EXPORT_DIR = Path("/tmp/workhour_exports")


# ─── JSON Schema ──────────────────────────────────────────────────────────────

EXPORT_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "start_date": {
            "type": "string",
            "description": "报表开始日期，格式 YYYY-MM-DD",
        },
        "end_date": {
            "type": "string",
            "description": "报表结束日期，格式 YYYY-MM-DD",
        },
        "title": {
            "type": "string",
            "description": "报表标题（可选，默认为'工时汇总表'）",
        },
        "org_id": {
            "type": "string",
            "description": "部门/组织ID（可选，不传则导出当前用户所在组织）",
        },
        "auth_token": {
            "type": "string",
            "description": "用户认证 token（系统自动注入，无需用户填写）",
        },
    },
    "required": ["start_date", "end_date"],
    "additionalProperties": False,
}


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def export_report_handler(**kwargs) -> Dict[str, Any]:
    """
    导出工时报表为 Excel 文件。

    返回格式：
    {
        "success": True,
        "file_name": "workhour_export_xxx.xlsx",
        "file_path": "/tmp/workhour_exports/workhour_export_xxx.xlsx",
        "message": "报表已生成，文件名：workhour_export_xxx.xlsx"
    }
    """
    start_date = kwargs.get("start_date")
    end_date = kwargs.get("end_date")
    title = kwargs.get("title", "工时汇总表")
    org_id = kwargs.get("org_id")
    auth_token = kwargs.pop("auth_token", None)

    if not start_date or not end_date:
        return {"success": False, "error": "缺少必填参数：start_date 和 end_date"}

    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )
    url = f"{base_url}/api/workhour/export/project-simple"

    query_params: Dict[str, str] = {
        "startDate": start_date,
        "endDate": end_date,
        "title": title,
    }
    if org_id:
        query_params["orgId"] = org_id

    headers: Dict[str, str] = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, params=query_params, headers=headers)

        if response.status_code == 403:
            return {"success": False, "error": "权限不足，仅部门管理员及以上可导出报表"}

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"导出失败，服务返回状态码 {response.status_code}",
            }

        # 保存文件
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        file_name = f"workhour_export_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
        file_path = EXPORT_DIR / file_name

        file_path.write_bytes(response.content)

        logger.info(f"报表已导出：{file_path}，大小：{len(response.content)} bytes")

        return {
            "success": True,
            "file_name": file_name,
            "file_path": str(file_path),
            "size_bytes": len(response.content),
            "message": f"报表已生成：{file_name}（{start_date} 至 {end_date}）",
        }

    except httpx.TimeoutException:
        return {"success": False, "error": "导出超时，请稍后重试（大范围报表生成可能较慢）"}
    except Exception as e:
        logger.error(f"导出报表异常: {e}", exc_info=True)
        return {"success": False, "error": f"导出失败：{str(e)}"}


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_export_report_tool():
    """注册工时报表导出工具"""
    try:
        tool_registry.register_tool(
            name="export_report",
            description=(
                "导出工时汇总报表为 Excel 文件（适用：导出报表/下载工时表/生成Excel/工时汇总导出）。"
                "需指定时间范围（开始日期和结束日期）。仅部门管理员及以上角色可使用。"
            ),
            json_schema=EXPORT_REPORT_SCHEMA,
            handler=export_report_handler,
            category=ToolCategory.DATA_QUERY,
            timeout=60,
            requires_permission=True,
        )
        logger.info("工时报表导出工具注册成功")
    except Exception as e:
        logger.error(f"工时报表导出工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_export_report_tool()
