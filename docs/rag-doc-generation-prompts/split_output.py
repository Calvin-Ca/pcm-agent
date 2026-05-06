"""
把 agent 的大输出文本切成独立的 .md 文件,落到 knowledge-base-v2/ 目录。

输入格式约定(每个 agent 的输出):
    ---FILE_START: <相对路径>---
    <完整 markdown,含 frontmatter>
    ---FILE_END---

    ---FILE_START: <下一个相对路径>---
    ...

用法:
    python split_output.py --input raw-output/output-A-工时与考勤.txt --output knowledge-base-v2/

校验:
    1. 必填 frontmatter 字段(title/category/genre/version/audience/tags/acl)
    2. category 在白名单内
    3. genre 在白名单内
    4. 文件路径不重复(跨 agent 也会检测)
    5. 字数下限(根据 genre)

退出码:0 全部成功 / 1 有警告但可用 / 2 严重错误
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 必填 frontmatter 字段
REQUIRED_FIELDS = ["title", "category", "genre", "version", "audience", "tags", "acl"]

# category 白名单
VALID_CATEGORIES = {
    "工时管理", "考勤管理",
    "假期与加班", "请假管理",
    "薪资福利",
    "项目管理流程", "通用制度",
}

# genre 白名单
VALID_GENRES = {"policy", "sop", "faq", "case"}

# 各体裁字数下限(防止 agent 偷懒)
MIN_CHARS = {"policy": 1000, "sop": 800, "faq": 1000, "case": 700}

# 分隔符正则
START_PAT = re.compile(r"^---FILE_START:\s*(.+?)\s*---\s*$", re.MULTILINE)
END_PAT = re.compile(r"^---FILE_END---\s*$", re.MULTILINE)
FRONTMATTER_PAT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> Dict[str, str]:
    """简易 YAML frontmatter 解析(只支持 key: value 单行)"""
    m = FRONTMATTER_PAT.match(content)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm


def split_blocks(text: str) -> List[Tuple[str, str]]:
    """把大文本切成 [(rel_path, body), ...]"""
    blocks = []
    pos = 0
    while True:
        m_start = START_PAT.search(text, pos)
        if not m_start:
            break
        rel_path = m_start.group(1).strip()
        body_start = m_start.end()
        m_end = END_PAT.search(text, body_start)
        if not m_end:
            print(f"[ERROR] 找到 FILE_START 但找不到对应的 FILE_END: {rel_path}", file=sys.stderr)
            break
        body = text[body_start:m_end.start()].strip("\n")
        blocks.append((rel_path, body))
        pos = m_end.end()
    return blocks


def validate_block(rel_path: str, body: str) -> List[str]:
    """返回错误/警告列表(空列表表示 OK)"""
    issues = []

    # 1. frontmatter 必填字段
    fm = parse_frontmatter(body)
    if not fm:
        issues.append(f"[ERROR] 缺失 frontmatter")
        return issues

    for field in REQUIRED_FIELDS:
        if field not in fm:
            issues.append(f"[ERROR] frontmatter 缺字段: {field}")

    # 2. category 白名单
    cat = fm.get("category", "").strip()
    if cat and cat not in VALID_CATEGORIES:
        issues.append(f"[ERROR] category 不在白名单: {cat}")

    # 3. genre 白名单
    genre = fm.get("genre", "").strip()
    if genre and genre not in VALID_GENRES:
        issues.append(f"[ERROR] genre 不在白名单: {genre}")

    # 4. 字数下限
    body_no_fm = FRONTMATTER_PAT.sub("", body, count=1)
    char_count = len(body_no_fm.strip())
    min_chars = MIN_CHARS.get(genre, 0)
    if char_count < min_chars:
        issues.append(f"[WARN] 正文字数 {char_count} 低于下限 {min_chars}({genre})")

    # 5. 文件名末尾必须 .md
    if not rel_path.endswith(".md"):
        issues.append(f"[ERROR] 路径不以 .md 结尾: {rel_path}")

    return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="agent 输出的大文本文件路径")
    parser.add_argument("--output", required=True, help="目标知识库根目录")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_root = Path(args.output)

    if not input_path.exists():
        print(f"[ERROR] 输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(2)

    text = input_path.read_text(encoding="utf-8")
    blocks = split_blocks(text)

    if not blocks:
        print(f"[ERROR] 没有解析到任何 FILE_START/FILE_END 块,检查输入格式", file=sys.stderr)
        sys.exit(2)

    print(f"=== 输入: {input_path}")
    print(f"=== 解析到 {len(blocks)} 个文档块")
    print(f"=== 输出目录: {output_root}")
    print()

    success_count = 0
    warn_count = 0
    error_count = 0
    by_category: Dict[str, int] = {}
    seen_paths = set()

    for rel_path, body in blocks:
        issues = validate_block(rel_path, body)

        # 检查路径重复
        if rel_path in seen_paths:
            issues.append(f"[ERROR] 路径重复: {rel_path}")
        seen_paths.add(rel_path)

        has_error = any(i.startswith("[ERROR]") for i in issues)
        has_warn = any(i.startswith("[WARN]") for i in issues)

        status = "OK"
        if has_error:
            status = "ERROR"
            error_count += 1
        elif has_warn:
            status = "WARN"
            warn_count += 1
        else:
            success_count += 1

        print(f"[{status}] {rel_path}")
        for issue in issues:
            print(f"       {issue}")

        # 按 category 计数
        fm = parse_frontmatter(body)
        cat = fm.get("category", "?")
        by_category[cat] = by_category.get(cat, 0) + 1

        # 写文件(除非 dry-run 或有 ERROR)
        if not args.dry_run and not has_error:
            target = output_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + "\n", encoding="utf-8")

    # 总结报告
    print()
    print("=" * 60)
    print(f"总计: {len(blocks)} 篇")
    print(f"  [OK] 成功: {success_count}")
    print(f"  [WARN] 警告: {warn_count}")
    print(f"  [ERR] 错误: {error_count}")
    print()
    print("按 category 分布:")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")
    print()

    if args.dry_run:
        print("[DRY-RUN] 未写入文件")
    else:
        print(f"已写入 {success_count + warn_count} 篇到 {output_root}")

    if error_count > 0:
        sys.exit(2)
    if warn_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
