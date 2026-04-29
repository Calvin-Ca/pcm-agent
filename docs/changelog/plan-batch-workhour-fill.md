# 派单方案 ③ 批量工时填报（自然语言 + dry_run 预览）

> 创建：2026-04-28
> 预估：1.5 天
> 简历价值：AI 结构化输出（OpenAI tools schema）+ 时间表达式归一化 + 批量事务 + 多轮确认交互
> 前置条件：派单 ① ECharts、派单 ② 智能填报建议**已合并到 main**（已确认）

---

## 1. 目标

让用户**粘贴一段自由文本**或**复制的表格文本**到聊天框，AI 自动解析成 N 条工时记录，预览确认后批量入库。

**典型输入示例**：

```
（输入1：自由文本）
本周做的事：
周一上午做了工时管理 AI 助手的 ECharts 集成，下午开 B 项目需求会
周二全天测试批量填报功能
周三上午请假半天，下午写文档 4 小时
周四早上修复了智能填报的 e2e 问题，下午继续
周五整理周报

（输入2：表格文本，从 Excel 复制）
日期         项目         工时   内容
4/22        AI助手       8     ECharts集成
4/23        AI助手       8     批量填报开发
4/24        AI助手       4     文档整理
```

**输出**：N 条结构化记录 → 预览 → 用户确认 → 逐条调 `POST /api/workhour` 入库 → 返回成功/失败逐条结果。

---

## 2. 产品决策（已由用户拍板，不许更改）

| 项 | 决策 |
|---|------|
| **Q1 范围** | 仅做"纯文本 + 表格文本"MVP；**不做** Excel/PDF/图片上传（v2 再说） |
| **Q2 时间表达式** | 由 LLM 自己解析；prompt 里告知"今天是 {ISO 日期}"；**不引入** dateparser/arrow 等库 |
| **Q3 重复检测** | dry_run 阶段标记"⚠️ 该日期该项目已有 X 小时"；同时**告知用户后台每日工时上限**（默认 8h，超过黄色警告，超过 24h 物理上限直接红色拒绝） |
| **Q4 部分失败** | 成功的照常入库，失败的**逐条返回错误原因**给用户，让用户单独修；**不做**全量回滚 |

**交互友好性硬要求**：
- dry_run 必须返回**人类可读的预览文本**（带 emoji/对齐/分组），不仅仅是 JSON
- 每日合计超过阈值时，**必须明确告诉用户哪一天超了多少**，不能只说"有问题"
- 部分失败时，必须返回 `{success_count, failed_count, failed_items: [{date, project, error_message, suggested_fix}]}`，每条失败带可执行的修复建议（如 "项目 'XX' 不存在，最接近的项目：'YY'"）

---

## 3. 验收标准（必须全部达成）

### 后端工具

- [ ] 新增 `app/tools/batch_save_workhour.py`，注册到 `tool_registry`
- [ ] 工具 schema 包含两个参数：`text: str`（必填）+ `dry_run: bool`（默认 true）
- [ ] LLM 解析阶段使用 OpenAI tools schema 的强类型输出（**不允许字符串拼装 JSON**）
- [ ] `dry_run=true` 返回结构化预览（解析结果 + 重复检测 + 日上限校验 + 友好文本），**不写库**
- [ ] `dry_run=false` 真正调 `POST /api/workhour`（循环单条），返回逐条结果

### 解析能力

- [ ] **日期归一化**：支持"周一/上周三/4 月 22/昨天/今天/4-22"等表达，输出 ISO `YYYY-MM-DD`
- [ ] **项目模糊匹配**：复用 `param_resolver.resolve_project_id()`；匹配失败时建议最接近的项目名
- [ ] **工时数推断**：用户写"上午做了..."→ 4h，"全天"→ 8h，"半天"→ 4h，明确数字优先
- [ ] **workType 推断**：复用 `work_type_resolver.resolve()`，二维众数 → 一维 → LLM 兜底
- [ ] **workContent**：原文压缩为不超过 200 字的描述

### 校验层

- [ ] **重复检测**：调 `GET /api/workhour/by-date-range` 拉用户该日期范围的现有工时，标记冲突
- [ ] **日上限校验**：默认每日 8h（配置化 `BATCH_DAILY_HOUR_LIMIT`，可改），超过黄色警告（仍允许提交）；> 24h 物理上限红色拒绝（不允许提交）
- [ ] **粒度校验**：单条工时必须是 0.5 的倍数（与 SpringBoot 一致）
- [ ] **批量上限**：单次最多解析 30 条，超过截断并提示"超过单次上限，仅解析前 30 条，剩余请分批提交"

### 测试

- [ ] 单元测试 `tests/unit/test_batch_save_workhour.py` 覆盖：
  - 自由文本解析（"周一上午..."等）
  - 表格文本解析
  - 日期归一化（相对日期 / 绝对日期 / 跨年边界）
  - 工时数推断（上午/下午/全天/半天/明确数字）
  - 重复检测（已有工时 / 无冲突）
  - 日上限警告（> 8h 黄）/ 拒绝（> 24h 红）
  - 部分失败处理（mock SpringBoot 返回 N 条混合）
  - 批量上限截断（41 条输入 → 30 条 + 提示）
  - 项目名匹配失败 → fallback 到最接近的项目名建议
- [ ] 端到端测试：用真实账号 + 真实文本跑通 dry_run + 实际入库两条

### 文档

- [ ] `docs/api.md` 补 `batch_save_workhour` 工具说明（参数 / 返回 / 交互流程图）
- [ ] `CLAUDE.md` 工具层章节加一行
- [ ] `prompts/system.yaml` 或对应 prompt 加 LLM 调用 `batch_save_workhour` 的引导（用户提到"批量/我把这周记录给你/帮我填一下这段"等触发词）

---

## 4. 数据契约

### 4.1 工具参数

```python
{
    "name": "batch_save_workhour",
    "description": (
        "批量工时填报：解析用户提供的自然语言文本或表格，识别多条工时记录并批量入库。"
        "用户提到'批量填报/把这周/帮我把这段记录填了/这是我本月的工时清单'等场景调用。"
        "首次调用必须 dry_run=true 让用户预览，确认后才能 dry_run=false 实际入库。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "用户提供的工时描述文本，可以是自由文本或表格文本"
            },
            "dry_run": {
                "type": "boolean",
                "description": "true=仅预览解析结果不写库；false=实际入库（必须先 dry_run 让用户确认）",
                "default": True
            }
        },
        "required": ["text"]
    }
}
```

### 4.2 LLM 解析返回结构（强类型 tool 输出）

```python
PARSE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_workhour_records",
        "description": "把用户文本解析为结构化工时记录数组",
        "parameters": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "ISO YYYY-MM-DD"},
                            "project_name": {"type": "string", "description": "项目名称（可模糊）"},
                            "hours": {"type": "number", "description": "工时数（0.5 倍数）"},
                            "work_type": {
                                "type": "string",
                                "enum": ["研发工作", "商务工作", "综合管理工作", "履约工作", "需求工作"],
                            },
                            "content": {"type": "string", "description": "工作内容（≤200字）"},
                            "confidence": {
                                "type": "number",
                                "description": "解析置信度 0~1，<0.7 时前端标黄提示用户确认"
                            }
                        },
                        "required": ["date", "project_name", "hours", "work_type", "content", "confidence"]
                    }
                },
                "unparsed_segments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "无法解析的原文片段，让用户感知到丢失"
                }
            },
            "required": ["records"]
        }
    }
}
```

### 4.3 dry_run 返回结构

```python
{
    "success": true,
    "dry_run": true,
    "preview_text": "...",          # 人类可读的友好预览文本（必填）
    "parsed_records": [
        {
            "date": "2026-04-22",
            "project_id": "P001",      # 已经过 param_resolver 解析
            "project_name": "AI助手",
            "hours": 8.0,
            "work_type": "研发工作",
            "content": "ECharts集成",
            "confidence": 0.95,
            "warnings": []              # 单条警告：["与现有工时重复"/"工时数为推测"等]
        }
    ],
    "duplicates": [...],             # 重复记录详情
    "daily_warnings": [              # 日上限警告
        {"date": "2026-04-23", "total_hours": 10.0, "level": "warning", "message": "该日合计 10h，超过建议上限 8h"}
    ],
    "daily_blockers": [              # 日上限拒绝（>24h）
        {"date": "2026-04-24", "total_hours": 26.0, "level": "blocker", "message": "该日合计 26h 超过物理上限 24h，必须修正后才能提交"}
    ],
    "unparsed_segments": [...],      # LLM 未能解析的原文片段
    "summary": {
        "total_records": 5,
        "estimated_total_hours": 32.0,
        "blocked": false,             # true 时前端禁用"确认提交"按钮
        "next_action": "请确认无误后调用 batch_save_workhour(dry_run=false)"
    }
}
```

### 4.4 实际入库（dry_run=false）返回结构

```python
{
    "success": true,
    "dry_run": false,
    "success_count": 4,
    "failed_count": 1,
    "succeeded_items": [...],        # 成功记录（含 SpringBoot 返回的 workhour_id）
    "failed_items": [
        {
            "date": "2026-04-23",
            "project_name": "AI助手",
            "hours": 8.0,
            "error_message": "SpringBoot 400: 该日期已存在工时记录",
            "suggested_fix": "建议先删除该日期已有记录，或修改日期"
        }
    ],
    "summary_text": "✅ 成功填报 4 条共 32 小时；❌ 1 条失败，详情见 failed_items"
}
```

### 4.5 友好预览文本格式（preview_text 字段）

```
📋 解析到 5 条工时记录，预计总计 32 小时

✅ 2026-04-22 (周一)  AI助手        8.0h  研发工作  "ECharts集成"
✅ 2026-04-23 (周二)  AI助手        8.0h  研发工作  "批量填报开发"
⚠️ 2026-04-24 (周三)  AI助手        4.0h  研发工作  "文档整理"  [⚠️ 该日已有 6h 记录，提交后合计 10h 超过 8h 建议上限]
✅ 2026-04-25 (周四)  AI助手        8.0h  研发工作  "修复 e2e"
🟡 2026-04-26 (周五)  ?            4.0h  研发工作  "周报"     [🟡 项目名未识别，最接近："工时管理AI"]

⚠️ 警告：
   - 2026-04-24：合计 10.0h（建议 ≤ 8h）

✏️ 未解析片段：
   - "周三上午请假半天" — 请假记录请走请假流程，未纳入工时填报

请检查后回复"确认提交"或"取消"，或具体说明需要修改的条目。
```

---

## 5. 实施步骤

### 5.1 LLM 解析阶段

```python
# app/tools/batch_save_workhour.py

async def _parse_text_to_records(text: str, today: str, llm_client) -> dict:
    """LLM 解析自由文本为结构化记录"""
    if len(text) > 5000:
        text = text[:5000]  # 防止超长

    parse_prompt = f"""
你是工时填报助手。把用户提供的工时描述文本解析为结构化记录数组。

**今天的日期是 {today}**（用于解析"昨天/上周三/周一"等相对表达）

规则：
1. 日期统一输出 ISO 格式 YYYY-MM-DD
2. "上午"=4h，"下午"=4h，"全天"=8h，"半天"=4h；优先采纳明确写出的小时数
3. 工时数必须是 0.5 的倍数
4. work_type 必须是这 5 个之一：研发工作 / 商务工作 / 综合管理工作 / 履约工作 / 需求工作；
   不确定时默认"研发工作"
5. 解析不到日期/项目/工时数任一字段时，仍输出该记录但 confidence<0.5
6. 无法解析的原文片段放入 unparsed_segments，不要丢弃

用户文本：
{text}
"""
    result = await llm_client.call_with_tool_choice(
        prompt=parse_prompt,
        tool_schema=PARSE_TOOL_SCHEMA,
        tool_choice="parse_workhour_records",
    )
    return result  # {"records": [...], "unparsed_segments": [...]}
```

### 5.2 校验阶段（dry_run）

```python
async def _validate_records(records, user_id, auth_token, base_url) -> dict:
    """规范化 + 重复检测 + 日上限校验"""
    # 1) 项目名 → ID（复用 param_resolver）
    for r in records:
        r["project_id"] = await resolve_project_id(r["project_name"], ...)
        if not r["project_id"]:
            r["warnings"].append(f"项目名'{r['project_name']}'未识别")

    # 2) 拉现有工时（复用 work_type_resolver._fetch_history 套路）
    dates = sorted({r["date"] for r in records})
    existing = await _fetch_workhour_by_range(user_id, dates[0], dates[-1], auth_token, base_url)

    # 3) 重复检测 + 日上限
    daily_total = defaultdict(float)
    duplicates = []
    for r in records:
        # 累加现有工时
        existing_today = sum(e["workhour"] for e in existing if e["date"] == r["date"])
        daily_total[r["date"]] = existing_today + r["hours"]
        # 重复检测（同日同项目）
        for e in existing:
            if e["date"] == r["date"] and e["projectId"] == r["project_id"]:
                duplicates.append({...})

    # 4) 阈值判定
    daily_warnings = []
    daily_blockers = []
    LIMIT_WARN = float(os.getenv("BATCH_DAILY_HOUR_LIMIT", "8"))
    LIMIT_BLOCK = 24.0
    for date, total in daily_total.items():
        if total > LIMIT_BLOCK:
            daily_blockers.append({"date": date, "total_hours": total, ...})
        elif total > LIMIT_WARN:
            daily_warnings.append({"date": date, "total_hours": total, ...})

    return {"records": records, "duplicates": duplicates,
            "daily_warnings": daily_warnings, "daily_blockers": daily_blockers}
```

### 5.3 入库阶段（dry_run=false）

```python
async def _save_records(records, user_id, auth_token, base_url) -> dict:
    """循环调 POST /api/workhour，逐条入库，部分失败逐条返回原因"""
    succeeded, failed = [], []
    for r in records:
        try:
            resp = await httpx_post_workhour(...)
            succeeded.append({**r, "workhour_id": resp["id"]})
        except httpx.HTTPStatusError as e:
            failed.append({
                **r,
                "error_message": _extract_problem_detail(e),  # 复用 save_workhour 的 ProblemDetail 解析
                "suggested_fix": _suggest_fix(e),
            })
    return {"success_count": len(succeeded), "failed_count": len(failed),
            "succeeded_items": succeeded, "failed_items": failed}
```

### 5.4 友好文本生成

```python
def _format_preview_text(parsed) -> str:
    """生成 emoji + 对齐的预览文本"""
    lines = [f"📋 解析到 {len(parsed['records'])} 条工时记录，预计总计 {sum(r['hours'] for r in parsed['records'])} 小时", ""]
    for r in parsed["records"]:
        icon = "✅" if not r["warnings"] else ("🟡" if r["confidence"] >= 0.7 else "⚠️")
        weekday = _date_to_weekday_zh(r["date"])
        lines.append(f"{icon} {r['date']} ({weekday})  {r['project_name']:<10} {r['hours']:>4}h  {r['work_type']}  \"{r['content']}\"")
        for w in r["warnings"]:
            lines.append(f"     ⚠️ {w}")
    if parsed["daily_warnings"]:
        lines.extend(["", "⚠️ 警告："] + [f"   - {dw['date']}：合计 {dw['total_hours']}h（建议 ≤ {LIMIT_WARN}h）" for dw in parsed["daily_warnings"]])
    if parsed["unparsed_segments"]:
        lines.extend(["", "✏️ 未解析片段："] + [f"   - {s}" for s in parsed["unparsed_segments"]])
    lines.extend(["", '请检查后回复"确认提交"或"取消"，或具体说明需要修改的条目。'])
    return "\n".join(lines)
```

### 5.5 task_executor context 注入

参考派单 ② 的踩坑（dd2f1a7），**预先在 `task_executor.py:342` 把 `batch_save_workhour` 加入需要 context 注入的工具列表**：

```python
if task.tool_name in ("sql_query", "suggest_workhour", "batch_save_workhour"):
    exec_params["context"] = {...}
```

这是已知的依赖，**可以直接改不算被迫修复**——已经在派单 ② 的修复里建立了模式，本派单复用即可。

### 5.6 重启 + 端到端验证

```bash
# 重启容器（feedback_agent_commit_discipline 第 2 条）
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"

# 真实账号 + 真实文本测试
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我批量填一下这周工时：周一上午...","user_context":{...}}'

# 期望：
# 1. LLM 调 batch_save_workhour(dry_run=true)
# 2. 返回友好预览文本
# 3. 用户回复"确认提交"
# 4. LLM 再调 batch_save_workhour(dry_run=false)
# 5. 返回 success_count/failed_count
```

---

## 6. 工作域边界（**不许动**）

- ❌ 不许动 `docs/benchmarks/` 任何文件（基准测试归档区）
- ❌ 不许动 `docs/interview/` 任何文件（简历定稿区）
- ❌ 不许动 `fastapi-service/tests/benchmark/`（基准代码归档）
- ❌ 不许动派单 ① ② 的产出（`chart_builder.py` / `project_resolver.py` / `hours_resolver.py` / `suggest_workhour.py` / `chart_builder` 测试）
- ❌ 不许做 git mv
- ❌ 不许动 `app/services/work_type_resolver.py` 的实现（仅复用 `_fetch_history` 和 `resolve()` 接口）
- ❌ 不许引入 `dateparser` / `arrow` / `python-docx` / `openpyxl` / `pandas` / OCR 库等新依赖（Q1=A 决定）
- ❌ 不许实现 Excel/PDF/图片上传（v2 再说）
- ❌ 不许动 `save_workhour.py` 单条工时的逻辑（本工具是另一条独立链路）

---

## 7. Commit 纪律

使用 conventional commits 格式，**名实相符**：

| 实际改动 | commit 前缀 |
|---------|------------|
| 新增 `batch_save_workhour.py` + 工具注册 | `feat(batch): ...` |
| 修 batch 工具 bug | `fix(batch): ...` |
| 加单元测试 | `test(batch): ...` |
| 改 prompt yaml 引导 LLM 触发 | `feat(prompts): ...` 或 `fix(prompts):`（视情况） |
| 改 docs/api.md / CLAUDE.md | `docs: ...` |
| task_executor.py 添加 batch_save_workhour 到 context 列表 | **`feat(batch):`** 或 **`fix(batch):`**（不是 fix(task_executor)，因为是为新功能服务） |

**反例**：
- ❌ `fix(prompts): 起草批量填报方案` → 起草不是 fix，应该是 `chore`
- ❌ `feat: 批量填报完成` → scope 必须有

期望 commit 数量：**4~6 个**。

---

## 8. 被迫修复条款

如果 e2e 验证发现以下问题，允许修：

1. `param_resolver.resolve_project_id()` 在批量场景下性能不达标（30 条 × 串行调用 SpringBoot 太慢）→ 允许加 `asyncio.gather()` 并发；commit 用 `fix(param_resolver):` 或 `perf(param_resolver):`
2. SpringBoot 接口字段名意外不符（虽然派单 ② 已确认一致）→ 改 `_fetch_history` 字段映射
3. `prompt` 触发词不准确（用户说"批量填一下"但 LLM 没调 batch_save_workhour）→ 改 `prompts/system.yaml` 多轮迭代

**所有被迫修复必须在最终汇报中显式标注**："为打通批量填报链路被迫修了 X，理由是 Y"。

绝对禁止：
- 隐瞒越界
- 用 docs/chore 前缀掩盖代码修改
- 假装 e2e 跑通了但实际未真实下单（dry_run=false 必须真的写一条到 SpringBoot）

---

## 9. 完成标志

agent 提交最终汇报时必须包含：

1. 所有 commit 列表 + stat（`git log --oneline <分支起点>..HEAD` + 每个 `git show --stat <hash> | head -3`）
2. 单元测试通过截图（`pytest tests/unit/test_batch_save_workhour.py -v`）
3. **e2e 验证证据**：
   - dry_run=true 的友好预览文本（粘贴实际输出）
   - dry_run=false 的真实入库结果（必须从 SpringBoot DB 查询确认有新行；提供 SQL 查询命令和返回 row）
   - 至少跑一次"部分失败"场景（故意让一条项目名不匹配，验证 failed_items 输出友好且带 suggested_fix）
4. 是否触发"被迫修复"，触发了改了什么、为什么
5. 是否需要更新 `feedback_agent_commit_discipline`（如发现新的纪律盲点）

用户会用 `git show --stat <hash>` 验证每个 commit 名实相符（feedback 第 5 条）。

---

## 10. 与 ① ② 的协同

- **与 ① ECharts 协同**：批量预览阶段，如果用户填报数据 ≥ 3 条，可以在 dry_run 返回中**附带 ECharts 数据**（按日期/项目分组的柱状图），让前端预览图表。**本派单不强制做**，可以作为 nice-to-have 在最后 30 分钟里加；如果时间不够直接跳过。
- **与 ② 智能填报建议协同**：用户输入"帮我批量填这周"但完全没具体内容时，LLM 应该先调 `suggest_workhour` 给出 Top3 项目和工时建议，再让用户在此基础上输入具体描述。**本派单的 prompt 引导里要说明这个分工**：用户给了具体文本 → batch_save_workhour；用户只说"帮我填工时"没给文本 → suggest_workhour。

---

## 11. 简历价值预期

完成后简历可写：

```
批量工时填报：用户粘贴一段自然语言描述（"周一上午做了 A 项目，下午开 B 会"），
LLM 通过 OpenAI tools schema 输出强类型工时记录数组（含日期归一化 / 项目模糊匹配 /
工时数推断 / workType 自动识别），dry_run 模式预览（含每日上限校验、重复检测、
未解析片段提示），用户确认后批量循环入库（部分失败逐条返回错误原因 + 修复建议）。
单次最高解析 30 条，解析准确率 _% （上线后跑测试集补数据）。
```

★★★★ 简历分量。重点是"AI 结构化输出"+"批量事务"+"多轮确认交互"三个面试加分点。
