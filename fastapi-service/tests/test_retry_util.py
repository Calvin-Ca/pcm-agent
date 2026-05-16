"""
技术债 #3：工具调用无重试

测试 retry_util.retry_async()：
- 前两次抛网络错、第三次成功 → 最终成功且重试 2 次
- 抛权限错（PermissionError）→ 不重试，立即失败
- 抛业务异常（非可重试）→ 不重试
- 5xx HTTPStatusError → 可重试；4xx → 不可重试
- 超过最大重试次数 → 抛出最后一次异常
"""

import asyncio

import httpx
import pytest

from app.services.retry_util import retry_async, is_retriable_exception


pytestmark = pytest.mark.asyncio


class TestIsRetriable:
    def test_network_errors_retriable(self):
        assert is_retriable_exception(httpx.ConnectError("boom"))
        assert is_retriable_exception(httpx.ReadTimeout("slow"))
        assert is_retriable_exception(httpx.TimeoutException("slow"))
        assert is_retriable_exception(asyncio.TimeoutError())

    def test_5xx_retriable_4xx_not(self):
        req = httpx.Request("GET", "http://x")
        resp_500 = httpx.Response(503, request=req)
        resp_404 = httpx.Response(404, request=req)
        e5xx = httpx.HTTPStatusError("err", request=req, response=resp_500)
        e4xx = httpx.HTTPStatusError("err", request=req, response=resp_404)
        assert is_retriable_exception(e5xx)
        assert not is_retriable_exception(e4xx)

    def test_permission_and_business_errors_not_retriable(self):
        assert not is_retriable_exception(PermissionError("no access"))
        assert not is_retriable_exception(ValueError("bad param"))


class TestRetryAsync:
    async def test_retries_then_succeeds(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("transient")
            return {"ok": True, "attempt": calls["n"]}

        result = await retry_async(
            flaky, max_attempts=3, base_delay=0.0
        )
        assert result == {"ok": True, "attempt": 3}
        assert calls["n"] == 3  # 初次 + 重试 2 次

    async def test_permission_error_not_retried(self):
        calls = {"n": 0}

        async def denied():
            calls["n"] += 1
            raise PermissionError("无权限")

        with pytest.raises(PermissionError):
            await retry_async(denied, max_attempts=3, base_delay=0.0)
        assert calls["n"] == 1  # 立即失败，不重试

    async def test_business_error_not_retried(self):
        calls = {"n": 0}

        async def bad():
            calls["n"] += 1
            raise ValueError("业务错误")

        with pytest.raises(ValueError):
            await retry_async(bad, max_attempts=3, base_delay=0.0)
        assert calls["n"] == 1

    async def test_exhausts_attempts_raises_last(self):
        calls = {"n": 0}

        async def always_down():
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            await retry_async(always_down, max_attempts=3, base_delay=0.0)
        assert calls["n"] == 3  # 初次 + 重试 2 次后放弃

    async def test_backoff_delays_are_exponential(self, monkeypatch):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr("app.services.retry_util.asyncio.sleep", fake_sleep)

        async def always_down():
            raise httpx.ReadTimeout("slow")

        with pytest.raises(httpx.ReadTimeout):
            await retry_async(always_down, max_attempts=3, base_delay=0.5)

        # 2 次重试 → 2 次 sleep，指数退避 0.5, 1.0
        assert sleeps == [0.5, 1.0]
