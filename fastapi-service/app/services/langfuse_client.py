"""
Langfuse 可观测性接入（v3/v4 SDK，OTel 架构）。

设计原则：**完全旁路、绝不影响主链路**。
- 由 LANGFUSE_TRACING 开关控制；未开或缺 key → 返回 no-op，调用方无需判空。
- 任何 langfuse 异常都被吞掉（只记 debug 日志），不得让埋点拖垮 Agent。
- 主链路 LLM 调用在 llm_client.py 里用 log_generation() 包一层；
  每次请求在 stream_agent_response 里用 trace_context() 分组并打 user_id/session_id。

环境变量（生产 172 上的 Langfuse v3.202.1）：
    LANGFUSE_TRACING=true
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_BASE_URL=http://172.19.3.136:3030   # 映射到 SDK 的 host
"""
import logging
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_client = None            # 单例；None 表示未初始化或已禁用
_initialized = False


def _enabled() -> bool:
    return os.getenv("LANGFUSE_TRACING", "false").strip().lower() in ("1", "true", "yes")


def get_langfuse():
    """返回 Langfuse 客户端单例；禁用 / 缺 key / 导入失败时返回 None。"""
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True

    if not _enabled():
        logger.info("Langfuse 未启用 (LANGFUSE_TRACING != true)")
        return None
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or ""
    if not (pk and sk and host):
        logger.warning("Langfuse 已开启但 key/host 不全，降级为 no-op")
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(public_key=pk, secret_key=sk, host=host)
        # 非阻塞地验证一次连通性（失败不影响主链路）
        try:
            if not _client.auth_check():
                logger.warning("Langfuse auth_check 失败，埋点仍会尝试上报")
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse auth_check 异常", exc_info=True)
        logger.info(f"Langfuse 已启用 → {host}")
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse SDK 初始化失败，降级为 no-op", exc_info=True)
        _client = None
    return _client


class _NoopGen:
    """无操作占位，让调用方无需判空。"""

    def set_output(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Generation:
    """包一次 LLM 调用为 Langfuse generation；set_output 在退出时写回。"""

    def __init__(self, lf, name, model, inputs, model_parameters, metadata):
        self._lf = lf
        self._name = name
        self._model = model
        self._input = inputs
        self._params = model_parameters
        self._metadata = metadata
        self._cm = None
        self._gen = None
        self._output = None
        self._usage = None

    def set_output(self, output: Any, usage: Optional[Dict[str, int]] = None):
        self._output = output
        self._usage = usage

    def __enter__(self):
        try:
            self._cm = self._lf.start_as_current_observation(
                name=self._name,
                as_type="generation",
                model=self._model,
                input=self._input,
                model_parameters=self._params or None,
                metadata=self._metadata or None,
            )
            self._gen = self._cm.__enter__()
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse start generation 失败", exc_info=True)
            self._gen = None
        return self

    def __exit__(self, *exc):
        try:
            if self._gen is not None:
                upd = {}
                if self._output is not None:
                    upd["output"] = self._output
                if self._usage:
                    upd["usage_details"] = self._usage
                if upd:
                    self._gen.update(**upd)
            if self._cm is not None:
                self._cm.__exit__(*exc)
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse end generation 失败", exc_info=True)
        return False  # 不吞主链路异常


class _Span:
    """包一段非 LLM 的处理（工具执行、参数解析）为 Langfuse span，失败可标 ERROR level。"""

    def __init__(self, lf, name, inputs, metadata):
        self._lf = lf
        self._name = name
        self._input = inputs
        self._metadata = metadata
        self._cm = None
        self._span = None
        self._output = None
        self._level = None
        self._status_message = None

    def set_output(self, output: Any, level: Optional[str] = None, status_message: Optional[str] = None):
        self._output = output
        self._level = level
        self._status_message = status_message

    def __enter__(self):
        try:
            self._cm = self._lf.start_as_current_observation(
                name=self._name,
                as_type="span",
                input=self._input,
                metadata=self._metadata or None,
            )
            self._span = self._cm.__enter__()
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse start span 失败", exc_info=True)
            self._span = None
        return self

    def __exit__(self, *exc):
        try:
            if self._span is not None:
                upd: Dict[str, Any] = {}
                if self._output is not None:
                    upd["output"] = self._output
                if self._level:
                    upd["level"] = self._level
                if self._status_message:
                    upd["status_message"] = self._status_message
                if upd:
                    self._span.update(**upd)
            if self._cm is not None:
                self._cm.__exit__(*exc)
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse end span 失败", exc_info=True)
        return False


def log_span(name: str, inputs: Any, metadata: Optional[Dict[str, Any]] = None):
    """
    用法（工具执行 / 参数解析等）：
        with log_span("tool:save_workhour", params, {"tool_name": ...}) as sp:
            result = await run()
            sp.set_output(result, level=None if result.get("success") else "ERROR")
    禁用时返回 no-op。
    """
    lf = get_langfuse()
    if lf is None:
        return _NoopGen()
    return _Span(lf, name, inputs, metadata)


def log_generation(
    name: str,
    model: str,
    inputs: Any,
    model_parameters: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    用法：
        with log_generation("generate_with_tools", model, messages, {...}) as gen:
            result = await call_llm()
            gen.set_output(result, usage={"input": pt, "output": ct})
    禁用时返回 no-op，无需判空。
    """
    lf = get_langfuse()
    if lf is None:
        return _NoopGen()
    return _Generation(lf, name, model, inputs, model_parameters, metadata)


@contextmanager
def trace_context(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    trace_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    在一次请求最外层调用，建一个根 span 并把内部所有 generation/span 归到同一条 trace，
    同时打 user_id/session_id/tags/metadata。禁用或异常时是空上下文，行为不变。

    注意：propagate_attributes 只传属性、不建根节点；单独用它会让每个顶层观测各自成一条
    trace。所以这里额外建一个根 span 作为父节点，子观测才能嵌到同一条 trace 下。
    """
    lf = get_langfuse()
    root_cm = None
    prop_cm = None
    if lf is not None:
        try:
            from langfuse import propagate_attributes

            clean_meta = None
            if metadata:
                clean_meta = {k: str(v)[:500] for k, v in metadata.items() if v is not None}
            root_input = (metadata or {}).get("user_message")
            # 1) 根 span：所有子观测的父节点 → 一条 trace
            root_cm = lf.start_as_current_observation(
                name=trace_name or "chat",
                as_type="span",
                input=root_input,
                metadata=clean_meta,
            )
            root_cm.__enter__()
            # 2) 传播 trace 级属性到根 span 及其后代
            prop_cm = propagate_attributes(
                user_id=(user_id or None),
                session_id=(session_id or None),
                tags=tags or None,
                trace_name=trace_name or None,
            )
            prop_cm.__enter__()
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse trace_context setup 失败，降级为空上下文", exc_info=True)
            for cm in (prop_cm, root_cm):
                try:
                    if cm is not None:
                        cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            prop_cm = root_cm = None
    try:
        yield
    finally:
        for cm in (prop_cm, root_cm):
            try:
                if cm is not None:
                    cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.debug("Langfuse trace_context teardown 失败", exc_info=True)


def flush():
    """请求结束时尽力 flush（后台线程上报，失败无妨）。"""
    lf = get_langfuse()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse flush 失败", exc_info=True)
