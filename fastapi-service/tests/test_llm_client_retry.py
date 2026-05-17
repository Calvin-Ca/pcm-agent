"""
技术债 #6：LLM 调用缺少统一重试/限流

测试 LLMClient 的对外 HTTP 调用复用 retry_util.retry_async：
- 前两次抛瞬时网络错、第三次成功 → 最终成功且共调用 3 次（重试 2 次）
- 抛 4xx 鉴权错（401/403）→ 不重试，立即抛
- 5xx 服务端错 → 重试
- generate_with_tools 同样走重试
- 限流：并发上限信号量（若实现）

mock aiohttp，不真打网络。
"""

import asyncio

import aiohttp
import pytest

from app.services.llm_client import LLMClient


pytestmark = pytest.mark.asyncio


# ── aiohttp mock 基础设施 ────────────────────────────────────────────────────

class _FakeResp:
    """模拟 aiohttp 响应（async context manager）。"""

    def __init__(self, status, json_data=None, text_data=""):
        self.status = status
        self._json = json_data or {}
        self._text = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class _FakeSession:
    """模拟 aiohttp.ClientSession：按脚本依次返回响应或抛异常。"""

    def __init__(self, script):
        # script: list，元素为 _FakeResp 或 Exception 实例（抛出）
        self._script = list(script)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, *args, **kwargs):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _patch_session(monkeypatch, script):
    """让 aiohttp.ClientSession() 返回受控的 _FakeSession。"""
    fake = _FakeSession(script)

    def _factory(*args, **kwargs):
        return fake

    monkeypatch.setattr(aiohttp, "ClientSession", _factory)
    return fake


def _patch_nosleep(monkeypatch):
    """把 retry_util 的退避 sleep 替换为真正的无操作（避免测试变慢、
    避免 lambda 递归调用被 patch 后的 asyncio.sleep）。"""

    async def _noop(_d):
        return None

    monkeypatch.setattr("app.services.retry_util.asyncio.sleep", _noop)


def _ok_chat(content="hello"):
    return _FakeResp(
        200,
        json_data={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def _ok_tools_stop(content="done"):
    return _FakeResp(
        200,
        json_data={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        },
    )


# ── 债 #6 重试测试 ────────────────────────────────────────────────────────────

class TestGenerateRetry:
    async def test_transient_then_success(self, monkeypatch):
        """前两次瞬时网络错，第三次成功 → 成功且共调 3 次。"""
        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        fake = _patch_session(
            monkeypatch,
            [
                aiohttp.ClientConnectionError("transient 1"),
                aiohttp.ClientConnectionError("transient 2"),
                _ok_chat("recovered"),
            ],
        )
        # 退避置零，避免测试变慢
        _patch_nosleep(monkeypatch)

        result = await client.generate(prompt="hi")

        assert result == "recovered"
        assert fake.calls == 3  # 初次 + 重试 2 次

    async def test_auth_4xx_not_retried(self, monkeypatch):
        """401 鉴权错 → 不重试，立即抛。"""
        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        fake = _patch_session(
            monkeypatch,
            [_FakeResp(401, text_data="unauthorized")],
        )

        with pytest.raises(Exception) as ei:
            await client.generate(prompt="hi")

        # 立即失败，仅调用一次（未重试）
        assert fake.calls == 1
        assert "401" in str(ei.value)

    async def test_5xx_retried(self, monkeypatch):
        """503 服务端错 → 重试，最终成功。"""
        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        fake = _patch_session(
            monkeypatch,
            [
                _FakeResp(503, text_data="overloaded"),
                _ok_chat("after503"),
            ],
        )
        _patch_nosleep(monkeypatch)

        result = await client.generate(prompt="hi")

        assert result == "after503"
        assert fake.calls == 2


class TestGenerateWithToolsRetry:
    async def test_transient_then_success(self, monkeypatch):
        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        fake = _patch_session(
            monkeypatch,
            [
                aiohttp.ClientConnectionError("blip"),
                _ok_tools_stop("answer"),
            ],
        )
        _patch_nosleep(monkeypatch)

        result = await client.generate_with_tools(
            messages=[{"role": "user", "content": "x"}],
            tools=[],
        )

        assert result["finish_reason"] == "stop"
        assert result["content"] == "answer"
        assert fake.calls == 2

    async def test_auth_4xx_not_retried(self, monkeypatch):
        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        fake = _patch_session(
            monkeypatch,
            [_FakeResp(403, text_data="forbidden")],
        )

        with pytest.raises(Exception) as ei:
            await client.generate_with_tools(
                messages=[{"role": "user", "content": "x"}],
                tools=[],
            )

        assert fake.calls == 1
        assert "403" in str(ei.value)


class TestConcurrencyLimiter:
    async def test_semaphore_bounds_inflight_calls(self, monkeypatch):
        """LLM_MAX_CONCURRENCY=2 时，最多 2 个 LLM 调用同时在途。"""
        import app.services.llm_client as mod

        monkeypatch.setattr(mod, "_LLM_SEMAPHORE", None)
        monkeypatch.setenv("LLM_MAX_CONCURRENCY", "2")

        inflight = {"now": 0, "max": 0}

        class _SlowResp(_FakeResp):
            async def json(self):
                inflight["now"] += 1
                inflight["max"] = max(inflight["max"], inflight["now"])
                await asyncio.sleep(0.02)
                inflight["now"] -= 1
                return self._json

        def _factory(*a, **k):
            return _FakeSession([_SlowResp(200, json_data={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            })])

        monkeypatch.setattr(aiohttp, "ClientSession", _factory)

        client = LLMClient(api_key="k", env_prefix="CHAT_LLM")
        await asyncio.gather(*[client.generate(prompt="hi") for _ in range(6)])

        assert inflight["max"] <= 2  # 信号量上限生效

    async def test_no_limit_when_zero(self, monkeypatch):
        """LLM_MAX_CONCURRENCY<=0 → 不限流，_get_llm_semaphore 返回 None。"""
        import app.services.llm_client as mod

        monkeypatch.setattr(mod, "_LLM_SEMAPHORE", None)
        monkeypatch.setenv("LLM_MAX_CONCURRENCY", "0")
        assert mod._get_llm_semaphore() is None


class TestNoApiKeyStillRaisesImmediately:
    async def test_missing_key(self, monkeypatch):
        # conftest 已 load .env，须清掉环境变量才能命中"无 key"分支
        monkeypatch.delenv("CHAT_LLM_API_KEY", raising=False)
        client = LLMClient(api_key="", env_prefix="CHAT_LLM")
        with pytest.raises(ValueError):
            await client.generate(prompt="hi")
