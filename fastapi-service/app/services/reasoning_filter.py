"""用户可见响应中的模型推理标签清洗。"""

import re
from typing import Optional


REASONING_FILTER_FALLBACK = "响应生成异常，请重试。"

_THINK_BLOCK_RE = re.compile(
    r"<think\b[^>]*>[\s\S]*?</think\s*>",
    re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


def strip_reasoning_trace(text: Optional[str]) -> Optional[str]:
    """剥离闭合或未闭合的 ``<think>`` 内容，避免内部推理泄露。"""
    if not text or "<think" not in text.lower():
        return text

    cleaned = _THINK_BLOCK_RE.sub("", text)
    unclosed = _THINK_OPEN_RE.search(cleaned)
    if unclosed:
        cleaned = cleaned[:unclosed.start()]

    cleaned = cleaned.strip()
    return cleaned or REASONING_FILTER_FALLBACK


def _suffix_prefix_length(value: str, token: str) -> int:
    """返回 value 后缀与 token 前缀相同的最大长度。"""
    max_length = min(len(value), len(token) - 1)
    for length in range(max_length, 0, -1):
        if value[-length:].lower() == token[:length]:
            return length
    return 0


class ReasoningTraceStreamFilter:
    """跨流式分片过滤 ``<think>...</think>``，包括未闭合区块。"""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_reasoning = False

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""

        self._buffer += chunk
        visible_parts = []

        while self._buffer:
            token = self._CLOSE if self._inside_reasoning else self._OPEN
            lowered = self._buffer.lower()
            index = lowered.find(token)

            if index >= 0:
                if not self._inside_reasoning:
                    visible_parts.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(token):]
                self._inside_reasoning = not self._inside_reasoning
                continue

            pending_length = _suffix_prefix_length(lowered, token)
            if self._inside_reasoning:
                # 推理区内容立即丢弃，只保留可能跨 chunk 的闭合标签前缀。
                self._buffer = self._buffer[-pending_length:] if pending_length else ""
            else:
                emit_length = len(self._buffer) - pending_length
                if emit_length:
                    visible_parts.append(self._buffer[:emit_length])
                self._buffer = self._buffer[emit_length:]
            break

        return "".join(visible_parts)

    def finish(self) -> str:
        """结束流；未闭合推理区的剩余内容一律丢弃。"""
        if self._inside_reasoning:
            self._buffer = ""
            return ""

        remaining = self._buffer
        self._buffer = ""
        return remaining
