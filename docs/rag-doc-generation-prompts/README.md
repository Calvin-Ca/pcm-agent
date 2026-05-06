# 知识库批量生成 — 4-Agent 派单使用说明

> 关联设计文档:[`docs/rag-progressive-disclosure-design.md`](../rag-progressive-disclosure-design.md)
> 用途:把 100 篇企业知识库文档拆给 4 个并行 agent 生成,生成完用 `split_output.py` 切成独立 `.md`。

---

## 100 篇分组

| 组 | Prompt 文件 | 主题域 | 篇数 | 体裁分布 |
|----|------------|-------|------|---------|
| **A** | [`01-prompt-工时与考勤.md`](01-prompt-工时与考勤.md) | 工时管理(18) + 考勤管理(12) | 30 | policy 11 / sop 9 / faq 7 / case 3 |
| **B** | [`02-prompt-假期与请假.md`](02-prompt-假期与请假.md) | 假期与加班(14) + 请假管理(12) | 26 | policy 10 / sop 8 / faq 5 / case 3 |
| **C** | [`03-prompt-薪资福利.md`](03-prompt-薪资福利.md) | 薪资福利(14) | 14 | policy 6 / sop 3 / faq 4 / case 1 |
| **D** | [`04-prompt-项目流程与通用制度.md`](04-prompt-项目流程与通用制度.md) | 项目管理流程(14) + 通用制度(16) | 30 | policy 8 / sop 10 / faq 9 / case 3 |
| 合计 | | 7 主题域 | **100** | policy 35 / sop 30 / faq 25 / case 10 |

## 使用步骤

1. 打开 4 个浏览器 Tab,各自登录一个 LLM(Claude / ChatGPT / Gemini / 通义千问 / DeepSeek 都行)
2. 把对应的 prompt 文件**全文**复制粘贴到对话框,各自启动生成
3. agent 会输出一个**带分隔符的大文本**,把每个 agent 的完整输出保存到本地文件:
   ```
   docs/rag-doc-generation-prompts/raw-output/
   ├── output-A-工时与考勤.txt
   ├── output-B-假期与请假.txt
   ├── output-C-薪资福利.txt
   └── output-D-项目流程与通用制度.txt
   ```
4. 跑切分脚本,把大文本切到 `knowledge-base-v2/`:
   ```bash
   python docs/rag-doc-generation-prompts/split_output.py \
       --input docs/rag-doc-generation-prompts/raw-output/output-A-工时与考勤.txt \
       --output knowledge-base-v2/
   ```
   每个 agent 的输出依次跑一遍。
5. 把 100 篇 `.md` 反馈给我,我做 docx/pdf 转换 + 目录重组 + 重建索引。

## 输出格式约定(所有 agent 必须遵守)

```
---FILE_START: 01-工时管理/policy/工时填报管理制度.md---
---
title: 工时填报管理制度
category: 工时管理
genre: policy
version: 1.0
effective_date: 2026-01-01
audience: all
tags: [填报, 审核, 工时类型]
acl: public
related_docs: [工时审核流程.md, 工时类型分类标准.md]
---

# 工时填报管理制度

## 1. 适用范围
...

## 2. 定义与术语
...

(完整 Markdown,包含 frontmatter)
---FILE_END---

---FILE_START: 01-工时管理/policy/跨项目工时分摊规则.md---
...
---FILE_END---
```

## 切分脚本

[`split_output.py`](split_output.py) 自动:
1. 按 `---FILE_START: <path>---` 和 `---FILE_END---` 分割
2. 按 frontmatter 里的 `category` 验证目录结构
3. 写到目标目录,文件名直接用 `<path>` 部分
4. 输出统计报告(总篇数、按 category 分组、缺失字段警告)

## 质量自检清单(交付给我前用)

- [ ] 4 个 agent 总输出 = 100 篇(文件分组数对得上)
- [ ] 每篇都有 frontmatter,含全部必填字段
- [ ] 没有具体人名/具体项目名/真实部门名(仅用"员工"/"项目"/"部门"泛指)
- [ ] 数字阈值(如"每月 5 号前"、"超过 4 小时")明确,不能是"几天内"这种模糊表述
- [ ] FAQ 类每篇至少 8 个 Q&A
- [ ] policy 类每篇至少 3 条核心规则、2 条例外、1 条处罚条款
- [ ] SOP 类每篇至少 5 个有时限的步骤

## 故障排查

| 问题 | 应对 |
|------|------|
| Agent 中途停了(token 限制) | 让它继续:"请继续从上次中断处生成,无需重复已写完的篇目" |
| Agent 不按格式输出 | 截图给我,我调 prompt;或把 prompt 里的输出格式举例放到最前 |
| 切分脚本报某文件 frontmatter 缺字段 | 手动补一下,或让 agent 重生成那一篇 |
| 多个 agent 写出内容雷同 | 没事,这是 RAG 训练集,语义/术语雷同反而 OK;真重复了让 agent 重写 |
