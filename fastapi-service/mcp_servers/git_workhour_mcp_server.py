"""
Git Workhour MCP Server — 把本地 git 历史转成「按天聚合的工时草稿」。

用法:
    在 Claude Code CLI 中接入（stdio transport）。MCP 子进程就地跑在用户仓库
    目录里，直接读本地 git，无需访问 ai-service，无需鉴权。
    不直接命令行运行（会卡住等 stdio 输入）。

设计决策:
    - 纯本地只读：只跑 `git log`，不写任何东西，不连 ai-service。
    - 薄 + 厚兼顾：既返回原始 commit 数据（供 Claude 按上下文判断），也给出
      内置启发式 `estimated_hours` 作基线（时间跨度 + commit 数，clamp 0.5~8，
      取 0.5 整数倍）。Claude 可在基线上调整。
    - 不做项目映射、不做写库：项目名由用户/Claude 每次指定，写入复用现有
      `workhour-save` MCP（save_workhour：dry_run 预览 → 二段确认 → 入库）。

完整工作流（Claude 编排）:
    1. collect_git_activity(since, until) → 拿到每日草稿
    2. Claude 归纳每天的工作内容，让用户确认/指定工时项目
    3. 对每条草稿调用 workhour-save 的 save_workhour(confirm=False) 预览
    4. 用户确认后 save_workhour(confirm=True) 真写
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SERVICE_ROOT))

# 日志写文件（不写 stderr），避免管道缓冲区满导致子进程阻塞
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "git_workhour_mcp_server.log"),
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("git-workhour-mcp")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("git-workhour")

# git log pretty 里用不可打印分隔符，避免 message 里出现 | 之类字符导致解析错位
_FIELD_SEP = "\x1f"      # commit 头字段分隔
_COMMIT_MARK = "@@C@@"   # commit 头行标记（numstat 行不带此标记）


def _run_git(args: list[str], repo_path: str) -> str:
    """在 repo_path 下跑 git，返回 stdout。非零退出抛 RuntimeError。"""
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (code {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _resolve_repo(repo_path: str | None) -> str:
    """确定仓库目录，默认当前工作目录（Claude Code 的仓库根）。"""
    import os

    path = repo_path or os.getcwd()
    # 校验确为 git 仓库；顺带把 path 归一到仓库根，避免子目录歧义
    top = _run_git(["rev-parse", "--show-toplevel"], path).strip()
    return top or path


def _default_author(repo_path: str) -> str:
    """仓库配置的 user.email，作为默认「本人」过滤条件。取不到则空（不过滤）。"""
    try:
        return _run_git(["config", "user.email"], repo_path).strip()
    except Exception:  # noqa: BLE001
        return ""


def _estimate_hours(span_hours: float, commit_count: int) -> tuple[float, str]:
    """启发式基线：时间跨度 + commit 数下限，clamp 0.5~8，取 0.5 整数倍。

    - span_hours：当天首末 commit 时间差，代表「在场时长」的下界
    - + 0.5：首个 commit 前的启动/思考时间补偿
    - max(.., 0.5*commit_count)：单 commit 那天也至少给 0.5*commit 数
    这是**基线**，不是权威；Claude 应结合改动量/内容判断后覆盖。
    """
    raw = span_hours + 0.5
    raw = max(raw, 0.5 * commit_count)
    raw = min(max(raw, 0.5), 8.0)
    rounded = round(raw * 2) / 2
    basis = (
        f"span {span_hours:.1f}h + 0.5h 启动补偿，"
        f"下限 0.5×{commit_count} commit，clamp[0.5,8] → {rounded}h（基线，可调）"
    )
    return rounded, basis


def _finalize_days(raw: str) -> list[dict[str, Any]]:
    """解析并产出干净的每日草稿列表（按日期升序）。"""
    days: dict[str, dict[str, Any]] = {}
    current_commit: dict[str, Any] | None = None
    current_day: dict[str, Any] | None = None

    for line in raw.splitlines():
        if line.startswith(_COMMIT_MARK):
            parts = line[len(_COMMIT_MARK):].split(_FIELD_SEP)
            if len(parts) < 5:
                continue
            full_hash, iso_date, name, email, subject = parts[:5]
            dt = datetime.fromisoformat(iso_date)
            date_key = dt.strftime("%Y-%m-%d")
            current_day = days.setdefault(date_key, {
                "date": date_key,
                "commit_count": 0,
                "insertions": 0,
                "deletions": 0,
                "_files": set(),
                "_times": [],
                "commits": [],
            })
            current_commit = {
                "hash": full_hash[:8],
                "time": dt.strftime("%H:%M"),
                "author": name,
                "subject": subject,
                "insertions": 0,
                "deletions": 0,
                "files": 0,
            }
            current_day["commit_count"] += 1
            current_day["_times"].append(dt)
            current_day["commits"].append(current_commit)
        elif line.strip() and current_commit is not None and current_day is not None:
            cols = line.split("\t")
            if len(cols) >= 3:
                add = int(cols[0]) if cols[0].isdigit() else 0
                dele = int(cols[1]) if cols[1].isdigit() else 0
                path = cols[2]
                current_commit["insertions"] += add
                current_commit["deletions"] += dele
                current_commit["files"] += 1
                current_day["insertions"] += add
                current_day["deletions"] += dele
                current_day["_files"].add(path)

    result: list[dict[str, Any]] = []
    for date_key in sorted(days.keys()):
        day = days[date_key]
        times = sorted(day.pop("_times"))
        files = day.pop("_files")
        span_hours = (times[-1] - times[0]).total_seconds() / 3600 if len(times) > 1 else 0.0
        est, basis = _estimate_hours(span_hours, day["commit_count"])
        day["files_changed"] = len(files)
        day["first_commit"] = times[0].strftime("%H:%M") if times else ""
        day["last_commit"] = times[-1].strftime("%H:%M") if times else ""
        day["span_hours"] = round(span_hours, 1)
        day["estimated_hours"] = est
        day["estimate_basis"] = basis
        result.append(day)
    return result


@mcp.tool()
async def collect_git_activity(
    since: str,
    until: str = "",
    author: str | None = None,
    repo_path: str | None = None,
    include_merges: bool = False,
) -> str:
    """读取本地 git 历史，按天聚合成工时草稿（只读，不写库，不连后端）。

    拿到结果后请这样编排：
    1. 按每天的 commits/改动量归纳「工作内容」（简洁，≤200 字）。
    2. `estimated_hours` 只是基线，请结合改动量与内容判断后调整。
    3. 让用户为每条草稿指定「工时项目」（本工具不做项目映射）。
    4. 逐条调用 workhour-save 的 save_workhour(confirm=False) 预览，
       用户确认后再 confirm=True 真写。

    Args:
        since: 起始日期，支持 YYYY-MM-DD 或 git 相对时间（如 "1 week ago"）。必填。
        until: 截止日期，同上格式；留空表示到现在。
        author: 作者过滤（姓名或邮箱子串）。留空默认取仓库 user.email（本人）；
                传 "*" 表示不过滤（全员）。
        repo_path: 仓库目录，留空默认当前工作目录（Claude Code 的仓库根）。
        include_merges: 是否包含 merge commit，默认 False。
    """
    logger.info(
        "collect_git_activity since=%r until=%r author=%r repo=%r merges=%s",
        since, until, author, repo_path, include_merges,
    )
    try:
        repo = _resolve_repo(repo_path)
    except Exception as e:  # noqa: BLE001
        return json.dumps({
            "error": f"不是有效的 git 仓库: {e}",
            "hint": "请在 git 仓库目录下使用，或用 repo_path 指定仓库路径。",
        }, ensure_ascii=False, indent=2)

    # author 语义：None→本人默认；"*"→不过滤；其它→按该值过滤
    if author is None:
        author_filter = _default_author(repo)
    elif author == "*":
        author_filter = ""
    else:
        author_filter = author

    pretty = f"{_COMMIT_MARK}%H{_FIELD_SEP}%aI{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%s"
    args = [
        "log",
        f"--since={since}",
        "--numstat",
        "--date=iso-strict",
        f"--pretty=format:{pretty}",
    ]
    if until:
        args.append(f"--until={until}")
    if author_filter:
        args.append(f"--author={author_filter}")
    if not include_merges:
        args.append("--no-merges")

    try:
        raw = _run_git(args, repo)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"git log 执行失败: {e}"}, ensure_ascii=False, indent=2)

    days = _finalize_days(raw)
    total_est = round(sum(d["estimated_hours"] for d in days), 1)
    total_commits = sum(d["commit_count"] for d in days)

    payload = {
        "repo": repo,
        "author_filter": author_filter or "(全员)",
        "range": {"since": since, "until": until or "(现在)"},
        "days": days,
        "summary": {
            "total_days": len(days),
            "total_commits": total_commits,
            "total_estimated_hours": total_est,
        },
        "next_step": (
            "请按每天 commits 归纳工作内容并让用户指定工时项目，再经 "
            "workhour-save 的 save_workhour 二段确认写入。estimated_hours 为基线可调。"
        ),
    }
    if not days:
        payload["hint"] = "该范围/作者下没有 commit。检查 since/until 或把 author 设为 '*'。"
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    logger.info("Starting git_workhour MCP server (local, read-only)")
    mcp.run()
