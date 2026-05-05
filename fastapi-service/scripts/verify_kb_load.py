"""临时脚本:验证扩库后 langchain_rag 能正常加载所有 100 篇文档"""
import sys
from pathlib import Path
from collections import Counter

# 把 app 目录加入路径
sys.path.insert(0, str(Path(__file__).parent))

from app.services.langchain_rag import _load_documents_from_dir

KB_PATH = Path(__file__).parent.parent.parent / "knowledge-base"

print(f"知识库路径: {KB_PATH}")
print(f"路径存在: {KB_PATH.exists()}")
print()

docs = _load_documents_from_dir(str(KB_PATH))
print(f"加载文档总数: {len(docs)}")

# 按 source 分组
by_source = Counter(d.metadata.get("source", "?") for d in docs)
print(f"唯一文档源数: {len(by_source)}")
print()

# 按文件格式分组
by_ext = Counter(Path(s).suffix for s in by_source.keys())
print(f"按格式分布: {dict(by_ext)}")
print()

# 前 10 个 source
print("前 10 个 source:")
for src, count in list(by_source.items())[:10]:
    print(f"  {src}: {count} chunks")

# 验证: 统计实际文件数（去重 source 路径）
# .md/.pdf/.docx 每个文件对应一个 source
# .csv 每行对应一个 source，但文件只有 2 个
file_count = len(list(KB_PATH.rglob("*.md"))) + \
             len(list(KB_PATH.rglob("*.pdf"))) + \
             len(list(KB_PATH.rglob("*.docx"))) + \
             len(list(KB_PATH.rglob("*.csv")))
assert file_count == 102, f"期望 102 个文件(100文档+2CSV)，实际 {file_count}"

# 验证格式分布
assert by_ext.get('.md', 0) == 72, f"期望 72 个 .md，实际 {by_ext.get('.md', 0)}"
assert by_ext.get('.docx', 0) == 18, f"期望 18 个 .docx，实际 {by_ext.get('.docx', 0)}"
assert by_ext.get('.pdf', 0) == 10, f"期望 10 个 .pdf，实际 {by_ext.get('.pdf', 0)}"
assert by_ext.get('.csv', 0) == 2, f"期望 2 个 .csv，实际 {by_ext.get('.csv', 0)}"

print()
print("[PASS] 验证通过!")
print(f"  文件总数: {file_count}")
print(f"  总 chunks 数: {len(docs)}")
print(f"  唯一源数: {len(by_source)} (含 CSV 行拆分)")
print(f"  格式分布: md={by_ext.get('.md',0)}, docx={by_ext.get('.docx',0)}, pdf={by_ext.get('.pdf',0)}, csv={by_ext.get('.csv',0)}")
