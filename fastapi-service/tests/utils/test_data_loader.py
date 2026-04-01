"""
测试数据加载工具

递归加载 tests/data/ 下所有 JSON 文件，返回扁平化的用例列表。
支持按 category / sub_type / intent 过滤。
"""

import json
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).parent.parent / "data"


def load_all_cases(
    data_dir: Path | None = None,
    category: str | None = None,
    sub_type: str | None = None,
    intent: str | None = None,
) -> list[dict[str, Any]]:
    """
    递归加载所有测试用例，支持可选过滤条件。

    Args:
        data_dir:  数据根目录，默认 tests/data/
        category:  仅加载该 category 的用例（如 "query_timesheet"）
        sub_type:  仅加载该 sub_type 的用例（如 "query_self_today"）
        intent:    仅加载期望该 intent 的用例（如 "tool_execution"）

    Returns:
        用例字典列表，每条包含所有原始字段
    """
    root = data_dir or _DATA_DIR
    cases: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 跳过文件（读取失败）: {path}: {e}")
            continue

        if not isinstance(data, list):
            print(f"[warn] 跳过文件（非数组）: {path}")
            continue

        for case in data:
            if category and case.get("category") != category:
                continue
            if sub_type and case.get("sub_type") != sub_type:
                continue
            if intent and case.get("expected", {}).get("intent") != intent:
                continue
            cases.append(case)

    return cases


def load_tool_execution_cases(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """加载所有 intent=tool_execution 的用例（供 Layer 2 参数提取测试使用）"""
    return load_all_cases(data_dir=data_dir, intent="tool_execution")


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """返回用例集合的统计摘要"""
    from collections import Counter
    return {
        "total": len(cases),
        "by_category": dict(Counter(c.get("category") for c in cases)),
        "by_sub_type": dict(Counter(c.get("sub_type") for c in cases)),
        "by_intent": dict(Counter(c.get("expected", {}).get("intent") for c in cases)),
    }
