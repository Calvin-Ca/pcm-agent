"""
测试卫生：bench raw log 强制 UTF-8

bench_progressive_rag.py 此前依赖 PowerShell tee 落盘，产出 UTF-16LE，
导致 grep 断言失配（曾踩坑）。本测试验证新增的自带 raw-log 落盘能力
（_RawLogTee）确实以 UTF-8 写出，且中文/emoji 可被 utf-8 正常读回。
"""

import sys
from pathlib import Path

import pytest

# 将 benchmark 目录加入 sys.path，便于直接 import bench 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_progressive_rag import _RawLogTee  # noqa: E402


def test_raw_log_tee_writes_utf8(tmp_path):
    log_path = tmp_path / "raw.log"
    sample_lines = [
        "=== Smoke 模式: 2 条 query ===\n",
        "[1/4] id=S01 mode=oneshot 中文内容 🎉\n",
        "汇总统计 coverage=100.0%\n",
    ]

    tee = _RawLogTee(str(log_path), original_stream=sys.stdout)
    try:
        for line in sample_lines:
            tee.write(line)
        tee.flush()
    finally:
        tee.close()

    # 关键断言：用 utf-8 能正常读回，且内容完整
    text = log_path.read_text(encoding="utf-8")
    for line in sample_lines:
        assert line.strip() in text

    # 显式确认不是 UTF-16：UTF-16LE 会在文件头有 BOM 或大量 \x00
    raw = log_path.read_bytes()
    assert b"\xff\xfe" not in raw[:2], "不应为 UTF-16LE BOM"
    assert raw.count(b"\x00") == 0, "UTF-8 文本不应出现 NUL 字节"


def test_raw_log_tee_roundtrip_python_open(tmp_path):
    """模拟踩坑验证命令：python -c open(p,encoding='utf-8').read()"""
    log_path = tmp_path / "raw2.log"
    tee = _RawLogTee(str(log_path), original_stream=sys.stdout)
    tee.write("方案 A 升级触发 model=qwen3.5-plus tool_calls=5\n")
    tee.close()

    with open(log_path, encoding="utf-8") as f:
        content = f.read()
    assert "qwen3.5-plus" in content
    assert "tool_calls=5" in content
