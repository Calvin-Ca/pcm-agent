"""
单元测试: 技术债 #11 —— 上下文快照渐进式压缩（先汇总，再裁剪，最后截断）

覆盖:
- 短上下文不触发压缩，原样返回
- 超长上下文：先汇总较旧轮次 + 裁剪冗余，最终长度 <= 上限且保留最近若干轮关键信息
- 汇总阶段抛异常时降级到裁剪/截断，仍返回合法上下文不抛异常
"""

import json

import pytest

from app.services.context_compressor import compress_context_snapshot


def _size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


# ─── 短上下文：不压缩 ─────────────────────────────────────────────────────────


def test_small_snapshot_returned_unchanged():
    snap = {
        "history": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你"},
        ],
        "memories": "关于该用户的已知信息\n- 偏好简洁回答",
    }
    out = compress_context_snapshot(snap, max_chars=8000)
    assert out == snap  # 原样返回，未触发任何压缩


def test_none_snapshot_passthrough():
    assert compress_context_snapshot(None, max_chars=8000) is None


# ─── 超长上下文：汇总 + 裁剪 + 截断，长度收敛 ─────────────────────────────────


def test_long_snapshot_compressed_within_limit_and_keeps_recent():
    # 构造大量历史轮次，远超上限
    history = []
    for i in range(40):
        history.append({"role": "user", "content": f"问题{i} " + "x" * 300})
        history.append({"role": "assistant", "content": f"回答{i} " + "y" * 300})
    snap = {
        "history": history,
        "memories": "关于该用户的已知信息\n" + "\n".join(f"- 记忆条目 {i} " + "z" * 100 for i in range(50)),
    }
    assert _size(snap) > 8000  # 前置确认确实超长

    out = compress_context_snapshot(snap, max_chars=8000)

    # 最终长度收敛到上限内
    assert _size(out) <= 8000
    # 最近一轮关键信息保留（最后一条 assistant 回答的标识词）
    flat = json.dumps(out, ensure_ascii=False)
    assert "回答39" in flat
    assert "问题39" in flat
    # 应保留 history 结构（不是被整个丢弃）
    assert "history" in out
    assert isinstance(out["history"], list) and len(out["history"]) >= 1
    # 汇总阶段应产生对较旧轮次的摘要标记
    assert out.get("summary") or any(
        isinstance(m, dict) and m.get("role") == "summary" for m in out["history"]
    )


def test_long_history_no_memories_still_converges():
    history = [{"role": "user", "content": "q" + "a" * 500} for _ in range(60)]
    snap = {"history": history, "memories": None}
    out = compress_context_snapshot(snap, max_chars=4000)
    assert _size(out) <= 4000
    assert "history" in out


# ─── 汇总失败 → 降级到裁剪/截断仍合法 ────────────────────────────────────────


def test_summarizer_failure_degrades_to_trim_truncate(monkeypatch):
    import app.services.context_compressor as cc

    def boom(*args, **kwargs):
        raise RuntimeError("summarizer exploded")

    # 让汇总阶段抛异常，验证降级路径
    monkeypatch.setattr(cc, "_summarize_old_turns", boom)

    history = []
    for i in range(40):
        history.append({"role": "user", "content": f"问题{i} " + "x" * 300})
        history.append({"role": "assistant", "content": f"回答{i} " + "y" * 300})
    snap = {"history": history, "memories": "关于该用户的已知信息\n- " + "m" * 5000}

    # 不抛异常
    out = compress_context_snapshot(snap, max_chars=8000)
    assert isinstance(out, dict)
    assert _size(out) <= 8000
    assert "history" in out
    # 即便汇总失败，最近一轮仍保留
    assert "回答39" in json.dumps(out, ensure_ascii=False)
