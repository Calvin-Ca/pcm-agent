"""
LLM Client - 大语言模型客户端

提供统一的 LLM 调用接口，支持普通调用和流式调用。
使用 OpenAI 兼容格式，可对接 DashScope、OpenAI 等多种后端。
"""

import os
import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


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
    ):
        self.api_key = api_key or os.getenv(f"{env_prefix}_API_KEY", "")
        self.api_base = api_base or os.getenv(
            f"{env_prefix}_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = model or os.getenv(f"{env_prefix}_MODEL", "qwen-plus")
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
    ) -> str:
        """
        非流式调用 LLM。

        Args:
            prompt: 用户消息（简单模式）
            messages: 完整消息列表（高级模式，优先于 prompt）
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 系统提示词

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
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    error_text = await resp.text()
                    raise ValueError(
                        f"LLM API 错误 ({self.model}): {resp.status} - {error_text}"
                    )

    async def stream_generate(
        self,
        prompt: str = None,
        messages: List[Dict[str, str]] = None,
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: str = None,
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
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise ValueError(
                        f"LLM API 错误 ({self.model}): {resp.status} - {error_text}"
                    )

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
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
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
                    return {"finish_reason": "stop", "content": msg.get("content", ""), "tool_calls": []}
                else:
                    err = await resp.text()
                    raise ValueError(f"Function Calling API 错误 ({self.model}): {resp.status} - {err}")

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
