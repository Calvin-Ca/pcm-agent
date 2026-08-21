"""
LLM Client - 大语言模型客户端

提供统一的 LLM 调用接口，支持普通调用和流式调用。
使用 OpenAI 兼容格式，可对接 DashScope、OpenAI 等多种后端。
"""

import asyncio
import os
import json
import logging
import time
from typing import AsyncGenerator, Dict, Any, List, Optional

import aiohttp

from app.services.retry_util import retry_async

logger = logging.getLogger(__name__)


# 瞬时网络错误（aiohttp 家族）：连接断开 / 服务端提前关闭 / DNS 等。
# retry_util.is_retriable_exception 只识别 httpx / asyncio.TimeoutError，
# 不识别 aiohttp。为复用 retry_util（不改它），此处把 aiohttp 瞬时错误与
# 5xx 统一转换为 asyncio.TimeoutError（retry_util 视为可重试）；4xx / 鉴权 /
# 缺 key / 业务错误转/保持为 ValueError（retry_util 视为不可重试，立即抛）。
_TRANSIENT_AIOHTTP_ERRORS = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
    aiohttp.ClientPayloadError,
)


class _RetriableLLMError(asyncio.TimeoutError):
    """瞬时 LLM 调用错误（网络抖动 / 5xx）。

    继承 asyncio.TimeoutError 以便 retry_util.is_retriable_exception 直接
    识别为可重试，无需修改 retry_util。
    """


# ── 限流：进程级 LLM 并发上限 ────────────────────────────────────────────────
# retry_util 只管重试、不管限流。此处用一个进程级信号量限制同时在途的 LLM
# HTTP 调用数（所有 LLMClient 共享同一上游 DashScope 端点）。默认上限较宽松
# （16），正常负载下行为不变；高并发时排队、避免压垮上游触发 429/5xx。
# 通过环境变量 LLM_MAX_CONCURRENCY 调整；<=0 视为不限流。
_LLM_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_llm_semaphore() -> Optional[asyncio.Semaphore]:
    """惰性创建进程级信号量（须在事件循环内首次调用）。

    返回 None 表示不限流（LLM_MAX_CONCURRENCY <= 0）。
    """
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is not None:
        return _LLM_SEMAPHORE
    try:
        limit = int(os.getenv("LLM_MAX_CONCURRENCY", "16"))
    except (TypeError, ValueError):
        limit = 16
    if limit <= 0:
        return None
    _LLM_SEMAPHORE = asyncio.Semaphore(limit)
    return _LLM_SEMAPHORE


def _raise_for_status(model: str, status: int, error_text: str) -> None:
    """根据 HTTP 状态码分流：5xx → 可重试；4xx / 其它 → 不可重试。"""
    if 500 <= status < 600:
        raise _RetriableLLMError(
            f"LLM API 5xx ({model}): {status} - {error_text}"
        )
    raise ValueError(f"LLM API 错误 ({model}): {status} - {error_text}")


async def _limited_retry(func, *, op_name: str):
    """限流 + 重试组合：先取并发令牌，再走 retry_util.retry_async。

    令牌在整个重试周期内持有（一次逻辑调用算一个并发），重试不会额外占用
    更多令牌。不限流时直接走 retry_async。
    """
    sem = _get_llm_semaphore()
    if sem is None:
        return await retry_async(func, op_name=op_name)
    async with sem:
        return await retry_async(func, op_name=op_name)


def _parse_text_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """从文本格式的 <tool_call>{...}</tool_call> 中解析工具调用。

    部分 vLLM 版本不走标准 tool_calls 字段，而是把工具调用以文本形式
    塞进 content。此处解析该 fallback 格式。

    解析失败时返回 None 并 logger.warning（技术债 #12：此前 except: pass
    静默吞掉，导致 tool_call 丢失却无任何线索可排查）。

    Args:
        content: 已去除 <think> 的模型输出文本

    Returns:
        {"name": ..., "arguments": {...}}；无 <tool_call> 或解析失败时 None。
    """
    import re as _re
    import json as _json

    if "<tool_call>" not in content:
        return None
    m = _re.search(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", content, _re.DOTALL)
    if not m:
        return None
    try:
        tc_data = _json.loads(m.group(1))
    except Exception as e:
        logger.warning(
            "文本格式 tool_call JSON 解析失败，已忽略该工具调用: %s; 原文片段=%s",
            e,
            m.group(1)[:200],
        )
        return None
    name = tc_data.get("name") or tc_data.get("tool")
    if not name:
        logger.warning("文本格式 tool_call 缺少 name/tool 字段，已忽略: %s", tc_data)
        return None
    args = tc_data.get("arguments") or tc_data.get("parameters") or {}
    return {"name": name, "arguments": args}


class LLMClient:
    """
    LLM 客户端，使用 OpenAI 兼容格式。

    通过环境变量配置，支持按功能模块使用不同前缀：
    - CHAT_LLM_*: 主对话
    - INTENT_LLM_*: 意图识别
    - PLANNER_LLM_*: 任务规划
    """

    def __init__(
        self,
        api_key: str = None,
        api_base: str = None,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        env_prefix: str = "CHAT_LLM",
        extra: dict = None,
    ):
        self.api_key = api_key or os.getenv(f"{env_prefix}_API_KEY", "")
        self.api_base = api_base or os.getenv(
            f"{env_prefix}_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = model or os.getenv(f"{env_prefix}_MODEL", "qwen-plus")
        self.extra = extra or {}
        self.default_temperature = temperature
        self.default_max_tokens = max_tokens

        if not self.api_key:
            logger.warning(f"LLM API Key 未配置 (env_prefix={env_prefix})")

    async def generate(
        self,
        prompt: str = None,
        messages: List[Dict[str, str]] = None,
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: str = None,
        extra: dict = None,
    ) -> str:
        """
        非流式调用 LLM。

        Args:
            prompt: 用户消息（简单模式）
            messages: 完整消息列表（高级模式，优先于 prompt）
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 系统提示词
            extra: 额外参数（如 num_ctx、think 等）

        Returns:
            str: LLM 返回的文本
        """
        if not self.api_key:
            raise ValueError("LLM API Key 未配置")

        msgs = self._build_messages(prompt, messages, system_prompt)
        url = f"{self.api_base.rstrip('/')}/chat/completions"

        payload = {
            "model": self.model,
            "messages": msgs,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
            **self.extra,
            **(extra or {}),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        _start = time.monotonic()
        _status = "success"
        _usage_holder: dict = {}

        # Lazy import to avoid circular dependency at module load
        try:
            from app.core.metrics import LLM_CALL_COUNT, LLM_CALL_LATENCY, LLM_TOKENS
            _has_metrics = True
        except ImportError:
            _has_metrics = False

        async def _attempt() -> str:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            usage = data.get("usage", {})
                            _usage_holder.update(usage or {})
                            if _has_metrics:
                                LLM_TOKENS.labels(model=self.model, token_type="prompt").inc(usage.get("prompt_tokens", 0))
                                LLM_TOKENS.labels(model=self.model, token_type="completion").inc(usage.get("completion_tokens", 0))
                            content = data["choices"][0]["message"]["content"]
                            if content:
                                import re
                                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                                if "<think>" in content:
                                    content = re.sub(r"<think>.*", "", content, flags=re.DOTALL).strip()
                            return content
                        error_text = await resp.text()
                        _raise_for_status(self.model, resp.status, error_text)
            except _TRANSIENT_AIOHTTP_ERRORS as exc:
                raise _RetriableLLMError(f"LLM 网络瞬时错误 ({self.model}): {exc}") from exc

        from app.services.langfuse_client import log_generation
        with log_generation(
            name="generate",
            model=self.model,
            inputs=msgs,
            model_parameters={
                "temperature": temperature if temperature is not None else self.default_temperature,
                "max_tokens": max_tokens or self.default_max_tokens,
            },
        ) as _gen:
            try:
                _content = await _limited_retry(_attempt, op_name=f"llm.generate[{self.model}]")
                _gen.set_output(
                    _content,
                    usage={
                        "input": _usage_holder.get("prompt_tokens", 0),
                        "output": _usage_holder.get("completion_tokens", 0),
                    } if _usage_holder else None,
                )
                return _content
            except Exception:
                _status = "error"
                raise
            finally:
                if _has_metrics:
                    duration = time.monotonic() - _start
                    LLM_CALL_COUNT.labels(model=self.model, call_type="generate", status=_status).inc()
                    LLM_CALL_LATENCY.labels(model=self.model, call_type="generate").observe(duration)

    async def stream_generate(
        self,
        prompt: str = None,
        messages: List[Dict[str, str]] = None,
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: str = None,
        extra: dict = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式调用 LLM，逐 token 返回。

        Args:
            prompt: 用户消息（简单模式）
            messages: 完整消息列表（高级模式，优先于 prompt）
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 系统提示词

        Yields:
            str: 每次返回一小段文本
        """
        if not self.api_key:
            raise ValueError("LLM API Key 未配置")

        msgs = self._build_messages(prompt, messages, system_prompt)
        url = f"{self.api_base.rstrip('/')}/chat/completions"

        payload = {
            "model": self.model,
            "messages": msgs,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
            "stream": True,
            **self.extra,
            **(extra or {}),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 流式调用：仅对"建立连接 + 首个响应状态"阶段重试（此时尚未 yield
        # 任何 token，重连安全）。一旦开始接收数据流则不再重试，避免重复 token。
        async def _open_session():
            session = aiohttp.ClientSession()
            try:
                resp = await session.post(url, headers=headers, json=payload)
            except _TRANSIENT_AIOHTTP_ERRORS as exc:
                await session.close()
                raise _RetriableLLMError(
                    f"LLM 流式连接瞬时错误 ({self.model}): {exc}"
                ) from exc
            if resp.status != 200:
                error_text = await resp.text()
                resp.release()
                await session.close()
                _raise_for_status(self.model, resp.status, error_text)
            return session, resp

        session, resp = await _limited_retry(
            _open_session, op_name=f"llm.stream_generate[{self.model}]"
        )
        async with session, resp:
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue

    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 500,
        extra: dict = None,
    ) -> Dict[str, Any]:
        """
        Function Calling 调用，一次完成意图识别 + 参数提取。

        Returns:
            {"finish_reason": "tool_calls", "content": None, "tool_calls": [{"name": ..., "arguments": {...}}]}
            或
            {"finish_reason": "stop", "content": "...", "tool_calls": []}
        """
        if not self.api_key:
            raise ValueError("LLM API Key 未配置")

        url = f"{self.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self.extra,
            **(extra or {}),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        _start = time.monotonic()
        _status = "success"
        _usage_holder: dict = {}

        try:
            from app.core.metrics import LLM_CALL_COUNT, LLM_CALL_LATENCY
            _has_metrics = True
        except ImportError:
            _has_metrics = False

        async def _attempt() -> Dict[str, Any]:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # OpenAI 兼容接口由模型服务端返回的 token 用量统计：
                            # - prompt_tokens：完整输入 token 数，包含 system prompt、对话消息、
                            #   工具定义以及 chat template 等服务端附加内容。
                            # - completion_tokens：模型本次生成的 token 数；tool_calls 的名称和参数也计入。
                            # - total_tokens：本次请求总 token 数，通常为前两项之和。
                            # - prompt_tokens_details.cached_tokens：命中前缀缓存的输入 token 数，
                            # 已包含在 prompt_tokens 中，不应重复累加；部分兼容服务可能不返回该字段。
                            # 9932token
                            _usage_holder.update(data.get("usage") or {})
                            choice = data["choices"][0]
                            finish_reason = choice.get("finish_reason")
                            msg = choice.get("message", {})
                            if finish_reason == "tool_calls":
                                calls = [
                                    {
                                        "name": tc["function"]["name"],
                                        "arguments": json.loads(tc["function"]["arguments"]),
                                    }
                                    for tc in msg.get("tool_calls", [])
                                ]
                                return {"finish_reason": "tool_calls", "content": None, "tool_calls": calls}
                            content = msg.get("content", "")
                            if content:
                                import re as _re, json as _json
                                content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
                                if "<think>" in content:
                                    content = _re.sub(r"<think>.*", "", content, flags=_re.DOTALL).strip()
                                # fallback：部分 vLLM 版本以文本格式输出工具调用
                                _parsed_tc = _parse_text_tool_call(content)
                                if _parsed_tc is not None:
                                    return {
                                        "finish_reason": "tool_calls",
                                        "content": None,
                                        "tool_calls": [_parsed_tc],
                                    }
                            return {"finish_reason": "stop", "content": content, "tool_calls": []}
                        err = await resp.text()
                        _raise_for_status(self.model, resp.status, err)
            except _TRANSIENT_AIOHTTP_ERRORS as exc:
                raise _RetriableLLMError(
                    f"Function Calling 网络瞬时错误 ({self.model}): {exc}"
                ) from exc

        from app.services.langfuse_client import log_generation
        with log_generation(
            name="generate_with_tools",
            model=self.model,
            inputs=messages,
            model_parameters={"temperature": temperature, "tool_choice": tool_choice, "max_tokens": max_tokens},
            metadata={"tool_names": [t.get("function", {}).get("name") for t in tools]},
        ) as _gen:
            try:
                _result = await _limited_retry(
                    _attempt, op_name=f"llm.generate_with_tools[{self.model}]"
                )
                _gen.set_output(
                    _result,
                    usage={
                        "input": _usage_holder.get("prompt_tokens", 0),
                        "output": _usage_holder.get("completion_tokens", 0),
                    } if _usage_holder else None,
                )
                return _result
            except Exception:
                _status = "error"
                raise
            finally:
                if _has_metrics:
                    duration = time.monotonic() - _start
                    LLM_CALL_COUNT.labels(model=self.model, call_type="function_calling", status=_status).inc()
                    LLM_CALL_LATENCY.labels(model=self.model, call_type="function_calling").observe(duration)

    def _build_messages(
        self,
        prompt: str = None,
        messages: List[Dict[str, str]] = None,
        system_prompt: str = None,
    ) -> List[Dict[str, str]]:
        """构建消息列表"""
        if messages:
            msgs = list(messages)
        elif prompt:
            msgs = [{"role": "user", "content": prompt}]
        else:
            raise ValueError("必须提供 prompt 或 messages 参数")

        if system_prompt and (not msgs or msgs[0].get("role") != "system"):
            msgs.insert(0, {"role": "system", "content": system_prompt})

        return msgs


def get_planner_llm_client(
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> LLMClient:
    """推理层 LLM 工厂。

    复杂场景（多步规划 / 批量解析 / A-RAG 多步导航）专用。
    PLANNER_LLM_* 已配置则用之（通常指向托管 API）；
    未配置时回退 CHAT_LLM_*（本地 8b），保证不改 .env 时行为不变。
    """
    prefix = "PLANNER_LLM" if os.getenv("PLANNER_LLM_API_KEY") else "CHAT_LLM"
    return LLMClient(env_prefix=prefix, temperature=temperature, max_tokens=max_tokens)
