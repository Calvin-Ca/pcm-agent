# [Agent E 派单] Phase 1:知识库整合 + 多格式转换 + 重建索引

> **使用方式**:把下面 `==== PROMPT START ====` 到 `==== PROMPT END ====` 之间的全部内容复制给 IDE coding agent。
> **何时启动**:**等 4 个文档生成 agent 全部产出**(`docs/rag-doc-generation-prompts/raw-output/` 下 4 个 .txt 文件就绪)**之后**。
> **预估工时**:2-3 小时。

==== PROMPT START ====

# 角色

你是工时管理系统 ai-service 仓库的运维/工程师。任务是把 4 个文档生成 agent 的输出整合成最终的 `knowledge-base/` 目录,并完成多格式转换和索引重建。

# 项目根目录

`E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service`

# 必读文件

1. `docs/rag-progressive-disclosure-design.md` §3(知识库重组与扩库)— **必读**
2. `docs/rag-doc-generation-prompts/README.md`(产物来源)
3. `docs/rag-doc-generation-prompts/split_output.py`(切分脚本,直接用)

# 前置检查

确认以下 4 个文件存在(都是 4 个文档生成 agent 的产出):

```
docs/rag-doc-generation-prompts/raw-output/output-A-工时与考勤.txt
docs/rag-doc-generation-prompts/raw-output/output-B-假期与请假.txt
docs/rag-doc-generation-prompts/raw-output/output-C-薪资福利.txt
docs/rag-doc-generation-prompts/raw-output/output-D-项目流程与通用制度.txt
```

如果某个缺失,**停止并报告**给用户,不要继续。

# 任务清单

## 1. 切分 4 个 raw-output 到 knowledge-base-v2/

```bash
cd "E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service"

# 先 dry-run 校验
python docs/rag-doc-generation-prompts/split_output.py \
    --input docs/rag-doc-generation-prompts/raw-output/output-A-工时与考勤.txt \
    --output knowledge-base-v2/ \
    --dry-run

# 4 个文件都 dry-run 一遍,看校验报告
# 如果 ERROR 数 = 0(允许 WARN),正式切分:
python docs/rag-doc-generation-prompts/split_output.py \
    --input docs/rag-doc-generation-prompts/raw-output/output-A-工时与考勤.txt \
    --output knowledge-base-v2/

# 对其他 3 个文件做同样处理
```

**预期**:`knowledge-base-v2/` 下应有 7 个主题域子目录,合计 100 篇 `.md`。

**校验**:
```bash
find knowledge-base-v2/ -name "*.md" | wc -l   # 应该 = 100
find knowledge-base-v2/ -mindepth 1 -maxdepth 1 -type d | sort  # 应该 7 个目录
```

## 2. 多格式转换(P0,体现多格式 RAG 能力)

按 design doc §3.2,分布:`.md 70 / .docx 18 / .pdf 10 / .csv 2`。

### 2.1 用 pandoc 转 .docx 和 .pdf

先确认 pandoc 已安装:
```bash
pandoc --version
# 没装的话 Windows 用 choco install pandoc / Linux 用 apt install pandoc
```

**.docx 转换 18 篇**(从所有体裁里随机抽,但保证每个主题域至少 1 篇):

```bash
# 选 18 篇 .md(分布到 7 个主题域),转成 .docx
# 转换后:
#   - 删除原 .md(避免重复)
#   - .docx 文件名约定:<原文件名>.docx(同名不同扩展)
#   - frontmatter 的 metadata 要在文件名末尾编码,例如:
#     原:01-工时管理/policy/工时填报管理制度.md
#     新:01-工时管理/policy/工时填报管理制度__policy_工时管理.docx
#     这样 langchain_rag 加载时能从文件名解析回 category/genre

pandoc input.md -o output.docx
```

**.pdf 转换 10 篇**(中文支持):

```bash
# 中文 PDF 需要 xelatex + 中文字体
pandoc input.md -o output.pdf --pdf-engine=xelatex \
    -V CJKmainfont="SimSun" -V mainfont="SimSun"
```

如果 xelatex/中文字体不可用,改用 wkhtmltopdf 或退回纯 .md(但要在报告里说明)。

### 2.2 .csv 生成 2 篇(结构化补充)

按 design doc §3.2,生成 2 个 csv 模拟"假期日历表 / 审批节点表":

**`knowledge-base-v2/02-假期与加班/data/2026年假期日历.csv`**:
```csv
日期,假期类型,放假天数,补班日期,备注
2026-01-01,元旦,1,无,
2026-02-17,春节,7,2026-02-15,
...(完整一年)
```

**`knowledge-base-v2/06-项目管理流程/data/项目审批节点表.csv`**:
```csv
项目类型,立项审批人,变更审批人,验收审批人,SLA(工作日)
小型项目,部门经理,项目经理,部门经理,3
中型项目,事业部总监,部门经理,事业部总监,5
大型项目,公司副总,事业部总监,公司副总,10
战略项目,CEO,公司副总,CEO,15
```

写好后让 agent 验证 csv loader 能加载:
```python
from langchain_community.document_loaders import CSVLoader
docs = CSVLoader("knowledge-base-v2/02-假期与加班/data/2026年假期日历.csv").load()
print(len(docs))  # 应该 > 0
```

### 2.3 转换后的目录结构验证

```bash
# 文件数应该是:.md ≈ 70 + .docx 18 + .pdf 10 + .csv 2 = 100
find knowledge-base-v2/ -name "*.md" | wc -l    # ≈ 70
find knowledge-base-v2/ -name "*.docx" | wc -l  # = 18
find knowledge-base-v2/ -name "*.pdf" | wc -l   # = 10
find knowledge-base-v2/ -name "*.csv" | wc -l   # = 2
```

## 3. 切到生产位置 + 备份旧库

```bash
# 备份旧库
mv knowledge-base/ knowledge-base-v1-backup/

# 启用新库
mv knowledge-base-v2/ knowledge-base/
```

**重要**:这一步**用户必须确认**才能执行(改动了主目录)。如果在自动化执行,先停下来确认。

## 4. 验证 langchain_rag.py 能加载新库

修改测试或临时脚本(放 `fastapi-service/scripts/verify_kb_load.py`):

```python
"""临时脚本:验证扩库后 langchain_rag 能正常加载所有 100 篇文档"""
import asyncio
from app.services.langchain_rag import _load_documents_from_dir

docs = _load_documents_from_dir("knowledge-base/")
print(f"加载文档总数:{len(docs)}")

# 按 source 分组
from collections import Counter
by_source = Counter(d.metadata.get("source", "?") for d in docs)
print(f"唯一文档源数:{len(by_source)}")
print(f"前 10 个 source:{list(by_source.items())[:10]}")

# 按文件格式分组
from pathlib import Path
by_ext = Counter(Path(s).suffix for s in by_source.keys())
print(f"按格式分布:{dict(by_ext)}")
```

跑:
```bash
cd fastapi-service
python -m scripts.verify_kb_load
```

**预期输出**:
- 加载文档总数 > 100(单文档可能拆多个 chunk)
- 唯一源 100 个
- 按格式 `{'.md':70, '.docx':18, '.pdf':10, '.csv':2}` 接近这个分布

## 5. 重建 Milvus / BM25 索引

如果 Milvus 服务在跑:

```bash
cd fastapi-service
# 重启 ai-service 触发全量重建(drop_old=True 是默认)
# 或者写个脚本调 langchain_rag.initialize() / reload_knowledge_base()
```

**生产环境**(172 服务器):
```bash
ssh caic@172.19.3.136 \
    "cd /home/caic/code/workhour/workhour_agent && \
     docker compose restart ai-service"

# 等 30 秒后看日志
ssh caic@172.19.3.136 "docker logs ai-assistant-service --tail 100" | grep -E "(知识库|加载|向量|BM25)"
```

预期日志:
- "加载文档总数:NNN"
- "Milvus 集合 ... 创建成功"
- "BM25 检索器初始化完成(jieba 分词)"

如果没有部署到 172,本地模式跑就好。

## 6. 抽样人工核查

随机抽 3 篇文档,人工读一下:
- frontmatter 字段全
- 内容真实感(不是空话套话)
- 数字阈值具体
- 章节标题统一(`## N. xxx`)

如果抽样有 2/3 不合格,回退到旧库(`mv knowledge-base/ knowledge-base-v2-bad/ && mv knowledge-base-v1-backup/ knowledge-base/`),报告给用户重生成。

# 验收标准

- [ ] `knowledge-base/` 下 100 篇文档,7 个主题域目录
- [ ] 多格式分布接近 70/18/10/2
- [ ] `verify_kb_load.py` 跑通,加载 100 个 source
- [ ] Milvus / BM25 索引重建完成,服务启动无错
- [ ] 抽样 3 篇人工合格

# 不要做的事

- ❌ 不要直接覆盖 `knowledge-base/`,**必须先备份**
- ❌ 不要修改 `langchain_rag.py` 的加载逻辑(它本来就支持多格式 loader)
- ❌ 不要修改 design doc

# 完成后报告

```markdown
## Phase 1 知识库整合完成报告

### 切分结果
- output-A-工时与考勤.txt → N 篇(预期 30)
- output-B-假期与请假.txt → N 篇(预期 26)
- output-C-薪资福利.txt → N 篇(预期 14)
- output-D-项目流程与通用制度.txt → N 篇(预期 30)
- 切分错误数:N(列具体哪些)

### 多格式分布
.md / .docx / .pdf / .csv = N / N / N / N

### 索引重建
- 加载文档总数:N
- Milvus 集合状态:[OK / 错误]
- BM25 状态:[OK / 错误]

### 抽样核查
随机抽:[列 3 篇文件名 + 合格/不合格 + 理由]

### 偏离设计
[如有]

### 旧库备份位置
knowledge-base-v1-backup/
```

==== PROMPT END ====
