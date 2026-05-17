"""
内部工具接口 — 仅供 MCP server / 内部脚本调用，不对外暴露。

注意：生产环境必须用 nginx 限制源 IP，禁止公网直接访问。
写工具额外受：写白名单 + dry_run 强制默认 + 结构化审计 约束（MCP Phase 3）。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("audit")

router = APIRouter(prefix="/api/internal/tools", tags=["internal"])

# 允许经内部端点调用的写工具白名单。新增写工具必须显式加入（G5）。
INTERNAL_WRITE_TOOLS = {"save_workhour"}

# 审计仅记录的业务参数字段白名单，绝不含 auth_token / user_context（G3）。
_AUDIT_PARAM_FIELDS = ("project_id", "date", "duration", "description", "user_id")


def _emit_audit(
    tool_name: str,
    phase: str,
    x_user_id: str,
    x_entity_type: str,
    params: Dict[str, Any],
    dry_run: Optional[bool] = None,
    success: Optional[bool] = None,
    error: Optional[str] = None,
    record_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """落一条结构化审计行。失败不得阻断主流程。"""
    try:
        safe_params = {k: params.get(k) for k in _AUDIT_PARAM_FIELDS if k in params}
        _audit_logger.info(json.dumps({
            "tag": "AUDIT",
            "ts": datetime.now().isoformat(),
            "tool": tool_name,
            "phase": phase,
            "user_id": x_user_id,
            "entity_type": x_entity_type,
            "dry_run": dry_run,
            "params": safe_params,
            "success": success,
            "error": error,
            "record_id": record_id,
            "reason": reason,
        }, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[audit] emit failed (non-blocking): {e}")


@router.post("/{tool_name}")
async def call_internal_tool(
    tool_name: str,
    params: Dict[str, Any],
    x_user_id: str = Header(..., alias="X-User-ID"),
    x_entity_type: str = Header(..., alias="X-Entity-Type"),
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
) -> Dict[str, Any]:
    """转发工具调用到 ToolRegistry，带身份头。

    只读工具：行为与历史一致（无 dry_run 注入、无审计）。
    写工具：白名单校验 → dry_run 强制默认 → 审计(attempt/result)。
    """
    logger.info(
        f"[internal] tool={tool_name}, user={x_user_id}, entity_type={x_entity_type}"
    )

    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {tool_name}")

    handler = tool_registry.get_handler(tool_name)
    if not handler:
        raise HTTPException(
            status_code=500, detail=f"tool handler not found: {tool_name}"
        )

    is_write = tool_registry.is_write_tool(tool_name)

    if is_write and tool_name not in INTERNAL_WRITE_TOOLS:
        _emit_audit(tool_name, "blocked", x_user_id, x_entity_type, params,
                    success=False, reason="not_whitelisted")
        raise HTTPException(
            status_code=403,
            detail=f"write tool not whitelisted for internal endpoint: {tool_name}",
        )

    # 写工具：未显式 dry_run=False 一律强制 dry_run=True（G2，不论调用方）
    if is_write:
        if params.get("dry_run") is not False:
            params["dry_run"] = True

    # 把 user context 注进 params（ai-service 现有的 user_context 协议）
    params.setdefault("user_context", {})
    params["user_context"]["user_id"] = x_user_id
    params["user_context"]["entity_type"] = x_entity_type
    params["user_context"]["auth_token"] = x_auth_token

    # 直接注入 auth_token 到顶层（兼容 query_timesheet 等现有工具的参数协议）
    params["auth_token"] = x_auth_token

    if is_write:
        _emit_audit(tool_name, "attempt", x_user_id, x_entity_type, params,
                    dry_run=bool(params.get("dry_run")))

    try:
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(handler):
            result = await handler(**params)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: handler(**params))

        if is_write:
            inner = result if isinstance(result, dict) else {}
            _emit_audit(
                tool_name, "result", x_user_id, x_entity_type, params,
                dry_run=bool(params.get("dry_run")),
                success=bool(inner.get("success", True)),
                error=inner.get("error"),
                record_id=inner.get("record_id"),
            )
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        if is_write:
            _emit_audit(tool_name, "result", x_user_id, x_entity_type, params,
                        dry_run=bool(params.get("dry_run")),
                        success=False, error=str(e))
        logger.error(f"[internal] tool={tool_name} execution failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"tool execution failed: {str(e)}"
        )
