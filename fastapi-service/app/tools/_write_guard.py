"""
写操作 dry_run 解析（写工具共用）

把「这次调用要不要真写库」的决策集中到一处：

    WRITE_DRY_RUN_DEFAULT 安全阀（开启时一票否决）
      > 调用方显式传的 dry_run
        > 工具自身默认

安全阀开启时**无条件预览，且不可被调用方覆盖**——它的用途是本地开发接生产
隧道时兜底，若 LLM 显式传 `dry_run=false` 就能绕过，这个兜底等于没有。
为 false（生产默认）时不介入，各工具沿用自身语义：`save_workhour` 直写、
`batch_save_workhour` 先预览。因此它是单向阀，只会让行为更安全。
"""

import logging
from typing import Any, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)

# 与 batch_save_workhour 原有解析保持一致，容忍 LLM 吐出的字符串布尔值
_TRUTHY = ("true", "1", "yes")


def _to_bool(value: Any) -> bool:
    """把 LLM 可能吐出的字符串布尔值转成 bool。"""
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


def resolve_dry_run(kwargs: Dict[str, Any], fallback: bool) -> bool:
    """
    解析本次调用是否走 dry_run 预览。

    Args:
        kwargs: 工具 handler 收到的原始参数
        fallback: 该工具自身的默认值（save_workhour=False / batch=True）

    Returns:
        True 表示只预览不写库。
    """
    value = kwargs.get("dry_run")

    # 安全阀一票否决：开启时忽略调用方意图，一律预览
    if settings.WRITE_DRY_RUN_DEFAULT:
        if value is not None and not _to_bool(value):
            logger.warning(
                "WRITE_DRY_RUN_DEFAULT=true，已忽略调用方传入的 dry_run=%r，强制预览不写库",
                value,
            )
        return True

    if value is not None:
        return _to_bool(value)
    return fallback
