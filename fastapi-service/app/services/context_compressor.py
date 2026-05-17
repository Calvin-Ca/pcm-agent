"""
上下文快照渐进式压缩（技术债 #11）

替代原先 langgraph_agent 中"超长直接粗暴截断只留最近 2 轮 + 记忆切 500 字"
的做法。采用三段策略，按信息损失从小到大依次施加，能在前一段达标后立即返回：

1. 汇总（summarize）：保留最近 N 轮逐字，较旧轮次压缩为一条 summary 文本，
   并对单条过长内容做温和缩写。
2. 裁剪（trim）：仍超限时，进一步收缩 memories、丢弃低信息字段、减少保留轮次。
3. 硬截断（truncate）：兜底，保证最终序列化长度 <= 上限，绝不溢出。

降级安全硬约束：汇总阶段使用纯规则（不依赖 LLM / 外部服务），且整体被
try/except 包裹——任一阶段异常都降级到下一段，最坏情况返回一个最小合法
快照，绝不向调用方抛出异常导致上下文构建崩溃。
"""
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 最近多少条 message 必须逐字保留（约等于最近 3 轮 user/assistant）
_KEEP_RECENT_MESSAGES = 6
# 单条 message 内容温和缩写阈值
_PER_MESSAGE_MAX = 600


def _size(obj: Any) -> int:
    """序列化字节长度（与写库前 json.dumps 口径一致）。"""
    try:
        return len(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return len(str(obj))


def _abbreviate(text: str, limit: int) -> str:
    if not isinstance(text, str) or len(text) <= limit:
        return text
    head = text[: limit - 60]
    tail = text[-40:]
    return f"{head} …[省略{len(text) - limit + 100}字]… {tail}"


def _summarize_old_turns(
    history: List[Dict[str, Any]], keep_recent: int
) -> Dict[str, Any]:
    """阶段 1：较旧轮次汇总为一条 summary，最近 keep_recent 条逐字保留。

    返回 {"summary": <str|None>, "recent": [...]}。纯规则实现，无外部调用。
    """
    if len(history) <= keep_recent:
        recent = [
            {**m, "content": _abbreviate(str(m.get("content", "")), _PER_MESSAGE_MAX)}
            if isinstance(m, dict)
            else m
            for m in history
        ]
        return {"summary": None, "recent": recent}

    old = history[:-keep_recent]
    recent = history[-keep_recent:]

    # 规则式摘要：统计 + 抽取每条前若干字，避免整段丢弃
    fragments: List[str] = []
    for m in old:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "?")
        content = str(m.get("content", "")).strip().replace("\n", " ")
        if content:
            fragments.append(f"{role}: {content[:80]}")
    summary_text = (
        f"[早期 {len(old)} 条对话已汇总] " + " | ".join(fragments[-12:])
    )
    summary_text = _abbreviate(summary_text, _PER_MESSAGE_MAX * 2)

    recent_abbrev = [
        {**m, "content": _abbreviate(str(m.get("content", "")), _PER_MESSAGE_MAX)}
        if isinstance(m, dict)
        else m
        for m in recent
    ]
    return {"summary": summary_text, "recent": recent_abbrev}


def _trim(snapshot: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    """阶段 2：裁剪——收缩 memories、减少保留轮次，去低信息字段。"""
    out = dict(snapshot)

    # memories 先按比例收缩
    mem = out.get("memories")
    if isinstance(mem, str) and mem:
        out["memories"] = _abbreviate(mem, max(400, max_chars // 8))

    history = out.get("history")
    if isinstance(history, list):
        # 逐步减少保留的 message 数量直到达标（最少保留最近 2 条）
        keep = len(history)
        while keep > 2 and _size(out) > max_chars:
            keep -= 2
            out["history"] = history[-keep:]
        # 仍超限则继续狠裁 memories
        if _size(out) > max_chars and isinstance(out.get("memories"), str):
            out["memories"] = _abbreviate(out["memories"], 200) or None
    return out


def _hard_truncate(snapshot: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    """阶段 3：兜底硬截断，保证返回结构合法且 <= 上限。"""
    history = snapshot.get("history")
    last = None
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, dict):
            last = {
                "role": last.get("role", "assistant"),
                "content": _abbreviate(str(last.get("content", "")), 1000),
            }
    minimal: Dict[str, Any] = {
        "history": [last] if last is not None else [],
        "memories": None,
        "truncated": True,
    }
    # 极端兜底：连最小结构都超限则砍内容
    if _size(minimal) > max_chars and minimal["history"]:
        c = minimal["history"][0]
        if isinstance(c, dict):
            c["content"] = str(c.get("content", ""))[: max(100, max_chars // 2)]
    return minimal


def compress_context_snapshot(
    snapshot: Optional[Dict[str, Any]], max_chars: int = 8000
) -> Optional[Dict[str, Any]]:
    """对上下文快照施加渐进式压缩。

    对外接口与原 inline 逻辑等价：入参为 dict（或 None），出参为 dict（或 None），
    调用方无感。短上下文原样返回；超长按 汇总→裁剪→截断 收敛到 max_chars。

    Args:
        snapshot: 原始上下文快照（含 history / memories 等）。
        max_chars: 序列化字符上限。

    Returns:
        压缩后的快照（保证 _size <= max_chars），None 透传。
    """
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        # 非预期类型：保守地原样返回，不在此处理无关逻辑
        return snapshot

    try:
        if _size(snapshot) <= max_chars:
            return snapshot  # 短上下文：不触发任何压缩
    except Exception:
        pass  # 计算失败也继续走压缩兜底，绝不抛出

    # ── 阶段 1：汇总 ──────────────────────────────────────────────────────
    working: Dict[str, Any] = dict(snapshot)
    try:
        history = working.get("history")
        if isinstance(history, list) and history:
            summarized = _summarize_old_turns(history, _KEEP_RECENT_MESSAGES)
            working["history"] = summarized["recent"]
            if summarized["summary"]:
                working["summary"] = summarized["summary"]
        if _size(working) <= max_chars:
            return working
    except Exception as e:
        logger.warning(f"上下文压缩-汇总阶段失败，降级到裁剪: {e}")
        working = dict(snapshot)  # 回退到原始，交给裁剪阶段

    # ── 阶段 2：裁剪 ──────────────────────────────────────────────────────
    try:
        trimmed = _trim(working, max_chars)
        if _size(trimmed) <= max_chars:
            return trimmed
        working = trimmed
    except Exception as e:
        logger.warning(f"上下文压缩-裁剪阶段失败，降级到硬截断: {e}")

    # ── 阶段 3：硬截断（兜底） ────────────────────────────────────────────
    try:
        return _hard_truncate(working, max_chars)
    except Exception as e:
        logger.error(f"上下文压缩-硬截断兜底失败，返回空快照: {e}")
        return {"history": [], "memories": None, "truncated": True}
