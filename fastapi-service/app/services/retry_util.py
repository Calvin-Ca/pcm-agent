"""
异步重试工具（技术债 #3）

为工具调用提供指数退避重试。仅对"可重试异常"（网络错误 / 超时 / 5xx）
重试，业务错误 / 权限错误立即失败、不重试。

集中实现，避免重试逻辑硬编码散落在各处。
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 默认重试配置：最多 3 次尝试（初次 + 2 次重试），退避 0.5s / 1s / 2s ...
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5


def is_retriable_exception(exc: BaseException) -> bool:
    """判断异常是否可重试。

    可重试：
      - httpx 网络/连接/读写错误（TransportError 家族：ConnectError /
        ReadError / WriteError / NetworkError / ProtocolError 等）
      - httpx 超时（TimeoutException 家族）
      - asyncio.TimeoutError（wait_for 超时）
      - 5xx HTTPStatusError（服务端临时故障）

    不可重试（业务/权限/参数错误，重试无意义甚至有害）：
      - PermissionError
      - 4xx HTTPStatusError（客户端错误，重试结果一样）
      - ValueError / TypeError 等业务异常
    """
    # 4xx vs 5xx：状态码错误只对 5xx 重试
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600

    # 网络层 / 超时类异常可重试
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # 权限错误明确不重试
    if isinstance(exc, PermissionError):
        return False

    return False


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    op_name: str = "operation",
) -> T:
    """对异步可调用对象执行带指数退避的重试。

    Args:
        func: 无参异步可调用对象（用 functools.partial / lambda 绑定参数）
        max_attempts: 最大尝试次数（含首次）。3 表示初次 + 最多 2 次重试。
        base_delay: 首次退避秒数，之后指数递增（base * 2**(retry_index)）。
        op_name: 用于日志的操作名。

    Returns:
        func() 的返回值。

    Raises:
        最后一次尝试的异常（可重试异常耗尽重试次数后，或遇到不可重试异常时
        立即抛出）。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001 — 需按类型分流重试
            if not is_retriable_exception(exc):
                # 业务 / 权限 / 4xx 错误：不重试，立即抛出
                raise
            if attempt >= max_attempts:
                logger.warning(
                    "[retry] %s 重试 %d 次后仍失败，放弃: %s",
                    op_name,
                    attempt - 1,
                    exc,
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "[retry] %s 第 %d 次尝试失败（可重试: %s），%.2fs 后重试",
                op_name,
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
