"""
Knowledge Base Navigator - 知识库底层导航工具集

为 A-RAG / 渐进式披露提供 4 个独立的纯函数:
- get_outline:      列出全部文档的 frontmatter + h1/h2 大纲
- keyword_search:   复用 langchain_rag 的 BM25Retriever 做关键词检索
- semantic_search:  复用 langchain_rag 的向量 retriever 做语义检索
- read_section:     按 file + h2 章节精读, 可附带前后相邻章节

设计要点:
- 不依赖 ToolRegistry / TaskExecutor 框架, 是纯业务函数, 由上层 Tool 包装调用
- get_outline 带进程级 mtime 缓存, 减少重复扫描成本
- read_section 做严格路径校验, 拒绝越权访问 knowledge-base/ 之外的文件
- 所有函数 try-except 失败返回明确错误结构, 不抛异常出来污染 agent loop
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── 常量 ─────────────────────────────────────────────────────────────────────

_KB_DEFAULT_DIRS: Tuple[str, ...] = (
    "knowledge-base",
    "../knowledge-base",
    "/app/knowledge-base",
)


def _resolve_kb_root() -> Optional[Path]:
    """
    定位 knowledge-base 目录, 兼容容器内 /app/knowledge-base 和源码相对路径。

    返回 Path 对象, 找不到时返回 None。
    """
    env_path = os.getenv("KB_PATH") or os.getenv("KNOWLEDGE_BASE_PATH")
    candidates = []
    if env_path:
        candidates.append(env_path)
    candidates.extend(_KB_DEFAULT_DIRS)

    for c in candidates:
        p = Path(c).resolve()
        if p.exists() and p.is_dir():
            return p

    return None


# ─── Outline 缓存 ─────────────────────────────────────────────────────────────

_OUTLINE_CACHE: Dict[str, Any] = {
    "kb_root": None,
    "mtime_signature": None,
    "documents": [],
}


def _scan_kb_signature(kb_root: Path) -> Tuple[float, int]:
    """
    扫描 knowledge-base/ 下所有 .md 文件, 取 (max_mtime, file_count) 作为缓存键。

    任意文件的修改/新增/删除都会让 max_mtime 或 count 变化, 缓存自然失效。
    """
    max_mtime = 0.0
    count = 0
    for f in kb_root.rglob("*.md"):
        try:
            m = f.stat().st_mtime
            if m > max_mtime:
                max_mtime = m
            count += 1
        except OSError:
            continue
    return max_mtime, count


# ─── Frontmatter 解析 ─────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """
    简化的 YAML frontmatter 解析, 只提取 key: value 与 key: [a, b] 两种格式。

    返回 (frontmatter_dict, body_without_frontmatter)。
    没有 frontmatter 时返回 ({}, text)。

    设计取舍: 不引入 PyYAML 依赖 (项目允许), 但实际项目已经依赖 pyyaml
    (PromptManager 用), 这里直接用 yaml 安全加载。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    fm_text = m.group(1)
    body = text[m.end():]

    try:
        import yaml  # type: ignore

        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
        return fm, body
    except Exception as e:
        logger.warning(f"frontmatter 解析失败: {e}; fm_text={fm_text[:100]}")
        return {}, body


def _category_from_path(file_rel: str) -> Optional[str]:
    """
    从相对路径推断 category。
    例如 '01-工时管理/policy/工时填报管理制度.md' → '工时管理'
    无目录前缀时返回 None。
    """
    parts = Path(file_rel).parts
    if not parts or len(parts) < 2:
        return None
    top = parts[0]
    # 形如 "01-工时管理" / "02-假期与加班"
    m = re.match(r"^\d+[-_]\s*(.+)$", top)
    if m:
        return m.group(1).strip()
    return top


# ─── 公共 API ──────────────────────────────────────────────────────────────────

async def get_outline(category: Optional[str] = None) -> Dict[str, Any]:
    """
    列出 knowledge-base 下所有 .md 文档的大纲。

    Args:
        category: 可选, 限定主题域 (与 frontmatter.category 或目录推断的 category 匹配)。
                  传 "ALL" / None / "" 时返回全部。

    Returns:
        {"documents": [
            {"file": "<相对路径>", "title": "<h1>", "h2": [...],
             "tags": [...], "category": "...", "genre": "...",
             "audience": "...", "acl": "..."},
            ...
        ]}
        失败时返回 {"documents": [], "error": "..."}。
    """
    try:
        kb_root = _resolve_kb_root()
        if not kb_root:
            logger.info("knowledge-base 目录不存在, get_outline 返回空")
            return {"documents": []}

        # ── 缓存校验 ────────────────────────────────────────────────────────
        sig = _scan_kb_signature(kb_root)
        cached_root = _OUTLINE_CACHE.get("kb_root")
        cached_sig = _OUTLINE_CACHE.get("mtime_signature")
        if cached_root == str(kb_root) and cached_sig == sig:
            documents = _OUTLINE_CACHE["documents"]
        else:
            documents = _scan_outline(kb_root)
            _OUTLINE_CACHE["kb_root"] = str(kb_root)
            _OUTLINE_CACHE["mtime_signature"] = sig
            _OUTLINE_CACHE["documents"] = documents

        # ── category 过滤 ───────────────────────────────────────────────────
        cat = (category or "").strip()
        if cat and cat.upper() != "ALL":
            documents = [d for d in documents if d.get("category") == cat]

        return {"documents": documents}

    except Exception as e:
        logger.error(f"get_outline 失败: {e}", exc_info=True)
        return {"documents": [], "error": str(e)}


def _scan_outline(kb_root: Path) -> List[Dict[str, Any]]:
    """实际扫描 knowledge-base/ 下所有 .md, 返回 outline 列表"""
    documents: List[Dict[str, Any]] = []
    for f in sorted(kb_root.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取 {f} 失败: {e}")
            continue

        fm, body = _parse_frontmatter(text)

        h1_match = _H1_RE.search(body)
        title = (
            fm.get("title")
            or (h1_match.group(1).strip() if h1_match else f.stem)
        )

        h2_list = [m.group(1).strip() for m in _H2_RE.finditer(body)]

        rel_path = str(f.relative_to(kb_root)).replace("\\", "/")

        category = fm.get("category") or _category_from_path(rel_path)
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        documents.append({
            "file": rel_path,
            "title": str(title),
            "h2": h2_list,
            "tags": tags,
            "category": category,
            "genre": fm.get("genre"),
            "audience": fm.get("audience"),
            "acl": fm.get("acl"),
        })
    return documents


async def keyword_search(
    query: str,
    category: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    BM25 关键词检索, 复用 langchain_rag._rag_service.bm25_retriever。

    Args:
        query: 检索关键词
        category: 可选, 用 metadata 过滤
        top_k: 返回 chunk 数

    Returns:
        [{"file": "...", "section": "...", "score": ..., "snippet": "..."}, ...]
        服务未初始化时返回 []。
    """
    return await _retriever_search("bm25", query, category, top_k)


async def semantic_search(
    query: str,
    category: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    向量语义检索, 复用 langchain_rag._rag_service.vector_store。
    """
    return await _retriever_search("semantic", query, category, top_k)


async def _retriever_search(
    kind: str,
    query: str,
    category: Optional[str],
    top_k: int,
) -> List[Dict[str, Any]]:
    """统一的 retriever 调用骨架"""
    if not query or not query.strip():
        return []
    try:
        from app.services import langchain_rag as _lr

        rag = getattr(_lr, "_rag_service", None)
        if not rag or not getattr(rag, "_initialized", False):
            logger.info(f"_retriever_search({kind}): RAG 服务未就绪, 返回 []")
            return []

        retriever = None
        if kind == "bm25":
            retriever = getattr(rag, "bm25_retriever", None)
        elif kind == "semantic":
            vs = getattr(rag, "vector_store", None)
            if vs is not None:
                retriever = vs.as_retriever(search_kwargs={"k": max(top_k * 2, 5)})
        if retriever is None:
            return []

        # 同步调用（invoke 实测 0.001~0.03s），绕过 anyio ThreadPoolExecutor 死锁
        docs = retriever.invoke(query)
    except Exception as e:
        logger.warning(f"_retriever_search({kind}) 失败: {e}")
        return []

    cat = (category or "").strip()
    results: List[Dict[str, Any]] = []
    for doc in docs:
        meta = getattr(doc, "metadata", {}) or {}
        if cat and cat.upper() != "ALL":
            doc_cat = meta.get("category") or _category_from_path(
                _normalize_source(meta.get("source", ""))
            )
            if doc_cat != cat:
                continue

        rel_path = _normalize_source(meta.get("source", ""))
        section = meta.get("h2") or meta.get("h1") or ""
        snippet = (getattr(doc, "page_content", "") or "")[:500]
        results.append({
            "file": rel_path,
            "section": str(section),
            "score": meta.get("score"),
            "snippet": snippet,
        })
        if len(results) >= top_k:
            break
    return results


def _normalize_source(source: str) -> str:
    """把绝对路径转成相对 kb_root 的形式; 失败时返回原路径文件名。"""
    if not source:
        return ""
    try:
        kb_root = _resolve_kb_root()
        if kb_root:
            p = Path(source).resolve()
            try:
                return str(p.relative_to(kb_root)).replace("\\", "/")
            except ValueError:
                pass
        return Path(source).name
    except Exception:
        return source


async def read_section(
    file: str,
    section: str,
    include_neighbors: bool = True,
) -> Dict[str, Any]:
    """
    读取指定文档的 h2 章节内容; 可选附带前后相邻章节。

    Args:
        file: 相对 knowledge-base/ 的路径 (从 outline / search 返回里取)
        section: h2 标题文本 (不含 '## ' 前缀)
        include_neighbors: 是否附带前后各一个 h2 块

    Returns:
        {"file": "...", "section": "...", "content": "...",
         "neighbors": [{"section": "...", "content": "..."}, ...]}
        失败时返回 {"error": "..."}。
    """
    try:
        kb_root = _resolve_kb_root()
        if not kb_root:
            return {"error": "knowledge-base 目录不存在"}

        if not file or not section:
            return {"error": "file 和 section 都必须提供"}

        # ── 安全校验: 必须在 kb_root 之下 ────────────────────────────────────
        target = (kb_root / file).resolve()
        try:
            target.relative_to(kb_root)
        except ValueError:
            logger.warning(f"read_section 越权拒绝: file={file}")
            return {"error": "文件路径越权: 必须在 knowledge-base/ 之下"}

        if not target.exists() or not target.is_file():
            return {"error": f"文档不存在: {file}"}

        text = target.read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)

        sections = _split_into_h2_sections(body)
        if not sections:
            return {"error": "文档内未找到任何 h2 章节"}

        # 找到目标 section (允许前后空白宽松匹配)
        target_section = section.strip()
        idx = -1
        for i, (h2, _content) in enumerate(sections):
            if h2.strip() == target_section:
                idx = i
                break
        # fallback: 模糊匹配 (包含)
        if idx < 0:
            for i, (h2, _content) in enumerate(sections):
                if target_section in h2 or h2 in target_section:
                    idx = i
                    break
        if idx < 0:
            available = [s[0] for s in sections]
            return {
                "error": f"章节 '{section}' 不存在",
                "available_sections": available,
            }

        h2, content = sections[idx]
        result: Dict[str, Any] = {
            "file": file,
            "section": h2,
            "content": f"## {h2}\n{content}".strip(),
            "neighbors": [],
        }

        if include_neighbors:
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(sections):
                    nh2, ncontent = sections[j]
                    result["neighbors"].append({
                        "section": nh2,
                        "content": f"## {nh2}\n{ncontent}".strip(),
                    })

        return result

    except Exception as e:
        logger.error(f"read_section 失败: {e}", exc_info=True)
        return {"error": str(e)}


def _split_into_h2_sections(body: str) -> List[Tuple[str, str]]:
    """
    用正则把 body 切成 [(h2_title, content), ...] 列表。
    h1 上方的内容会被忽略。
    """
    sections: List[Tuple[str, str]] = []

    # 找到所有 h2 起始位置
    matches = list(_H2_RE.finditer(body))
    if not matches:
        return sections

    for i, m in enumerate(matches):
        h2_title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        # body[start:end] 是 h2 之后到下一个 h2 之前的文本
        # 但 m.start() 才是 '##' 这一行的起点; 我们从 line 末尾开始取内容
        line_end = body.find("\n", m.start())
        content_start = line_end + 1 if line_end >= 0 else m.end()
        content = body[content_start:end].rstrip()
        sections.append((h2_title, content))

    return sections


# ─── 工具函数 (供 Tool 层用) ──────────────────────────────────────────────────

def clear_outline_cache() -> None:
    """清空 outline 缓存 (单元测试用)。"""
    _OUTLINE_CACHE["kb_root"] = None
    _OUTLINE_CACHE["mtime_signature"] = None
    _OUTLINE_CACHE["documents"] = []
