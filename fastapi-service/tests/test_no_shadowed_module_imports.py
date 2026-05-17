"""回归守卫：函数内不得用裸 `import X` 遮蔽模块级已导入的 X。

背景（2026-05-17 生产事故）：langgraph_agent.stream_agent_response 函数体内
有一处 `import os`（写在 os.getenv 调用之后）。Python 作用域规则下，函数体内
任意位置出现 `import os` 会把整个函数体的 `os` 视为局部名，导致该函数中位于
该 import 之前的 `os.getenv(...)` 抛 `UnboundLocalError`。chat 主路径每次必现。

此前技术债 #7 的 diff-scope 核验通过、9 单测通过，仍漏掉这一运行时陷阱
（单测未覆盖 streaming 主路径）。本测试用纯 ast 静态扫描堵住整类回归，
无需 import 重依赖。
"""

import ast
from pathlib import Path

import pytest

_TARGETS = [
    Path(__file__).resolve().parents[1] / "app" / "services" / "langgraph_agent.py",
]


def _module_level_imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:  # 仅模块顶层
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _shadowing_local_imports(tree: ast.Module, module_imports: set[str]):
    """返回 [(func_name, lineno, shadowed_name)]：函数内裸 import 遮蔽模块级名。"""
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for alias in sub.names:
                    bound = (alias.asname or alias.name).split(".")[0]
                    if bound in module_imports:
                        violations.append((node.name, sub.lineno, bound))
    return violations


@pytest.mark.parametrize("path", _TARGETS, ids=lambda p: p.name)
def test_no_function_local_import_shadows_module_import(path: Path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    module_imports = _module_level_imported_modules(tree)
    violations = _shadowing_local_imports(tree, module_imports)
    assert not violations, (
        f"{path.name} 存在函数内裸 import 遮蔽模块级导入（UnboundLocalError 风险）："
        + "; ".join(
            f"函数 {fn} 第 {ln} 行重复 import `{name}`（模块级已 import）"
            for fn, ln, name in violations
        )
    )
