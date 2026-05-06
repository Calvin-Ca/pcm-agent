# [Agent G 派单] Phase 4:Progressive RAG 对比评测

> **使用方式**:把下面 `==== PROMPT START ====` 到 `==== PROMPT END ====` 之间的全部内容复制给 IDE coding agent。
> **何时启动**:**等 Agent E(Phase 1 知识库整合)和 Agent F(Phase 2+3 改造)都完成**之后。
> **预估工时**:2-3 小时。

==== PROMPT START ====

# 角色

你是工时管理系统 ai-service 仓库的测试工程师。任务是为 Progressive RAG(渐进式披露)改造跑对比评测,产出可放进简历的硬数字 + 评测报告。

# 项目根目录

`E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service`

# 必读文件

1. `docs/rag-progressive-disclosure-design.md` §6(评测设计)— **必读全节**
2. `fastapi-service/tests/benchmark/bench_rag_recall.py`(已有 RAG 评测脚本,参考结构)
3. `fastapi-service/tests/benchmark/bench_fc_vs_two_calls.py`(已有 FC 评测脚本,参考延迟测量)
4. `docs/benchmarks/report-2026-04-25-final.md`(参考报告格式)

# 前置检查

```bash
# 1. 知识库已扩库(Phase 1 已完成)
find knowledge-base/ -name "*.md" -o -name "*.docx" -o -name "*.pdf" -o -name "*.csv" | wc -l
# 应该 ≈ 100

# 2. Phase 2+3 改造已合入
grep -q "agent_iterations" fastapi-service/app/services/langgraph_agent.py && echo OK
grep -q "kb_outline" fastapi-service/app/tools/__init__.py && echo OK

# 3. 服务能起
cd fastapi-service && python -c "from app.services.langgraph_agent import _build_graph; print('ok')"
```

如有任何前置不满足,**停止并报告**给用户。

# 任务清单

## 1. 创建 18 条评测 query 集

**文件**:`fastapi-service/tests/benchmark/data/progressive_rag_eval_18.jsonl`(新建)

按 design doc §6.2 的 18 条,每行一个 JSON。**严格按照下面这份 jsonl 内容创建**(我已经按 design doc 整理好,直接落盘):

```jsonl
{"id":"S01","category":"simple","query":"加班算不算工时?","expected_path":["knowledge_qa"],"expected_docs":["加班补偿政策","工时类型分类标准"],"expected_points":["加班需审批后计入","单独标记加班工时"]}
{"id":"S02","category":"simple","query":"工时填报截止日期是几号?","expected_path":["knowledge_qa"],"expected_docs":["工时填报管理制度"],"expected_points":["每月 5 号前"]}
{"id":"S03","category":"simple","query":"病假能不能折算成事假?","expected_path":["knowledge_qa"],"expected_docs":["病假管理规定","事假管理规定"],"expected_points":["不可折算","病假需医院证明"]}
{"id":"S04","category":"simple","query":"试用期员工有年假吗?","expected_path":["knowledge_qa"],"expected_docs":["年假管理规定","入职管理制度"],"expected_points":["试用期内无年假","满一年才有"]}
{"id":"S05","category":"simple","query":"出差的工时怎么算?","expected_path":["knowledge_qa"],"expected_docs":["工时类型分类标准"],"expected_points":["按实际工作时长","不含路途"]}
{"id":"M01","category":"multi_hop","query":"周末加班审批超时未处理,工时怎么记?","expected_path":["kb_outline","kb_semantic_search","kb_read_section","kb_semantic_search","kb_read_section"],"expected_docs":["加班补偿政策","工时审核管理办法"],"expected_points":["超时审批的兜底机制","工时仍可正常记录"]}
{"id":"M02","category":"multi_hop","query":"产假期间申请的项目奖金怎么发放?","expected_path":["kb_outline","kb_semantic_search","kb_read_section","kb_keyword_search","kb_read_section"],"expected_docs":["婚丧产假管理规定","年终奖与绩效奖金制度"],"expected_points":["产假期间奖金计算规则","发放时间"]}
{"id":"M03","category":"multi_hop","query":"跨项目工时分摊后,单个项目超 8 小时算不算加班?","expected_path":["kb_keyword_search","kb_read_section","kb_keyword_search","kb_read_section"],"expected_docs":["跨项目工时分摊规则","加班补偿政策"],"expected_points":["分摊后超 8 小时的判定","加班认定条件"]}
{"id":"M04","category":"multi_hop","query":"试用期员工提前离职,五险一金怎么处理?","expected_path":["kb_semantic_search","kb_read_section","kb_semantic_search","kb_read_section"],"expected_docs":["离职管理制度","五险一金缴纳办法"],"expected_points":["试用期离职流程","五险一金停缴/转移"]}
{"id":"M05","category":"multi_hop","query":"项目立项后变更工期,工时填报模板要不要改?","expected_path":["kb_semantic_search","kb_read_section","kb_semantic_search","kb_read_section"],"expected_docs":["项目变更管理办法","工时填报管理制度"],"expected_points":["项目变更对工时的影响","填报模板调整规则"]}
{"id":"M06","category":"multi_hop","query":"调休没用完离职会赔偿吗?涉及哪些制度?","expected_path":["kb_outline","kb_semantic_search","kb_read_section"],"expected_docs":["调休与补休规则","跨年度调休结转规则","离职管理制度"],"expected_points":["未使用调休的处理","离职结算包含调休"]}
{"id":"M07","category":"multi_hop","query":"出差产生加班,可以同时申请加班费和出差补贴吗?","expected_path":["kb_keyword_search","kb_read_section","kb_keyword_search","kb_read_section"],"expected_docs":["加班补偿政策","福利津贴管理办法"],"expected_points":["加班费与出差补贴的关系","是否冲突"]}
{"id":"F01","category":"metadata_filter","query":"项目经理这个角色,有哪些必读制度?","expected_path":["kb_outline"],"expected_docs":["所有 audience=manager 或 pm 的文档"],"expected_points":["列出 manager/pm 受众文档"]}
{"id":"F02","category":"metadata_filter","query":"财务相关的所有政策有几篇?","expected_path":["kb_outline"],"expected_docs":["category=薪资福利"],"expected_points":["列出 14 篇"]}
{"id":"F03","category":"metadata_filter","query":"HR 内部使用的保密文档有哪些?","expected_path":["kb_outline"],"expected_docs":["acl=internal"],"expected_points":["列出 internal 文档"]}
{"id":"L01","category":"compare","query":"病假/事假/年假 三种请假在审批人和时限上有什么区别?","expected_path":["kb_keyword_search","kb_read_section","kb_keyword_search","kb_read_section","kb_keyword_search","kb_read_section"],"expected_docs":["病假管理规定","事假管理规定","年假管理规定"],"expected_points":["三种请假对照表"]}
{"id":"L02","category":"compare","query":"工时类型有几种?","expected_path":["kb_keyword_search","kb_read_section"],"expected_docs":["工时类型分类标准"],"expected_points":["枚举工时类型"]}
{"id":"L03","category":"compare","query":"法定节假日和年假的加班补偿一样吗?","expected_path":["kb_semantic_search","kb_read_section","kb_semantic_search","kb_read_section"],"expected_docs":["法定节假日规定","年假管理规定","加班补偿政策"],"expected_points":["三倍工资 vs 1.5 倍","对比"]}
```

> **注意**:`expected_docs` 写的是文档标题(不带 .md 后缀),evaluator 用 substring 匹配。如果 Phase 1 的扩库文档标题和这里写的略有不同(如 `加班补偿政策` vs `加班费计算规则`),允许部分模糊匹配。

## 2. 实现评测脚本

**文件**:`fastapi-service/tests/benchmark/bench_progressive_rag.py`(新建)

参考 `bench_fc_vs_two_calls.py` 的结构。需要支持:

```python
# 命令行
python bench_progressive_rag.py --mode oneshot     # 强制走 knowledge_qa
python bench_progressive_rag.py --mode progressive # 启用 kb_* 工具
python bench_progressive_rag.py --mode both        # 两个模式都跑,对比输出
python bench_progressive_rag.py --smoke            # 只跑前 2 条快速验证
```

### 2.1 评测流程

```python
async def run_one(query_record, mode):
    """跑单条 query"""
    # mode="oneshot":临时把 kb_* 工具从 ToolRegistry 摘掉,LLM 只能看见 knowledge_qa
    # mode="progressive":全量工具
    
    start_ts = time.time()
    state = {"user_message": query_record["query"], "user_context": {...}}
    result = await graph.ainvoke(state)
    elapsed_ms = (time.time() - start_ts) * 1000
    
    return {
        "id": query_record["id"],
        "category": query_record["category"],
        "mode": mode,
        "answer": extract_answer(result),
        "tokens_total": extract_tokens(result),  # 从 llm_client 的累积日志取
        "latency_ms": elapsed_ms,
        "tool_calls": len(result.get("agent_history", [])),
        "tools_used": [h["tool"] for h in result.get("agent_history", [])],
        "docs_retrieved": extract_doc_names(result),  # 从 agent_history 的 observation 里提
    }
```

### 2.2 评测指标(按 design doc §6.3)

| 指标 | 说明 | 计算 |
|------|------|------|
| **答案完整度** | 1-10 分人工打分 | 评测脚本不能自动算,**留空字段供事后人工填** |
| **跨文档覆盖率** | 涉及的所有期望文档都被检索到? | `len(set(actual) & set(expected)) / len(expected)` |
| **Tokens 总消耗** | input + output | 从 llm_client 取 |
| **延迟** | E2E ms | 直接计时 |
| **Tool calls 平均轮数** | `len(agent_history)` | 直接计 |

### 2.3 输出 CSV

**文件**:`fastapi-service/tests/benchmark/results/progressive_rag_<YYYY-MM-DD>.csv`

```csv
id,category,mode,query,answer_completeness,docs_coverage,tokens,latency_ms,tool_calls,tools_used,docs_retrieved
S01,simple,oneshot,加班算不算工时?,,1.0,1234,5678,1,knowledge_qa,加班补偿政策
S01,simple,progressive,加班算不算工时?,,1.0,1456,7890,1,knowledge_qa,加班补偿政策
M01,multi_hop,oneshot,周末加班审批超时...,,0.5,2345,8000,1,knowledge_qa,加班补偿政策
M01,multi_hop,progressive,周末加班审批超时...,,1.0,3456,15000,4,"kb_outline,kb_semantic_search,kb_read_section,kb_read_section",加班补偿政策|工时审核管理办法
...
```

### 2.4 Smoke 模式

`--smoke` 只跑 S01 + M01 各 2 模式 = 4 次,确保脚本能正常跑完不报错。

## 3. 实际跑评测

```bash
cd fastapi-service

# 1. 先 smoke
python tests/benchmark/bench_progressive_rag.py --smoke

# 2. 全量跑(36 次,1-2 小时)
python tests/benchmark/bench_progressive_rag.py --mode both
```

**注意**:
- vLLM qwen3-8b 慢,18 × 2 = 36 次估算 ≥ 1 小时
- 如果中途失败,记录失败 ID,跳过继续跑剩下的
- 网络/服务异常不算失败,要重跑那一条

## 4. 人工打分(本步骤需要用户参与)

跑完后,csv 里 `answer_completeness` 字段是空的(自动跑不出来,必须人工)。

打开 csv,对每条 query 的 answer 字段:
- 0 = 完全错或没答
- 5 = 答了一半或方向对但缺关键点
- 10 = 完整、准确、覆盖所有 expected_points

**这一步可以让用户自己打**(因为 agent 自我评估有偏向)。脚本里加一个 `--mark-mode` 子命令辅助:

```bash
python tests/benchmark/bench_progressive_rag.py \
    --mark-mode \
    --csv tests/benchmark/results/progressive_rag_2026-05-XX.csv
# 逐条显示 query + answer + expected_points,提示用户输入 0-10 分
```

## 5. 输出报告

**文件**:`docs/benchmarks/progressive_rag_report_<YYYY-MM-DD>.md`

格式参照 `docs/benchmarks/report-2026-04-25-final.md`,内容必须包括:

```markdown
# Progressive RAG 对比评测报告

> 评测日期:YYYY-MM-DD
> 知识库规模:100 文档 / N chunks
> 模型:qwen3-8b(本地 vLLM)/ bge-large-zh-v1.5

## 总体对比

| 指标 | One-shot | Progressive | 提升 |
|------|---------|-------------|------|
| 答案完整度均值 | X.X/10 | Y.Y/10 | +Z.Z |
| 跨文档覆盖率均值 | XX% | YY% | +ZZ% |
| Tokens 均值 | NNNN | MMMM | -L% |
| 延迟均值 | NNNN ms | MMMM ms | +L%(预期变慢) |
| Tool calls 均值 | 1.0 | N.N | — |

## 按类别拆分

### 简单单文档(5 条)
[预期 progressive 不显著优于 one-shot,因为本身就该走 knowledge_qa]

### 跨文档多跳(7 条)
[**核心场景**,progressive 应该显著优于 one-shot]

### Metadata 过滤(3 条)
### 对比/列举(3 条)

## 失败用例分析
[列出完整度 < 5 的用例,分析根因]

## 结论与简历写法

[2-3 段:progressive 在多跳场景的价值、token/延迟权衡、未来工作]

```

## 6. 更新简历 bullet(草稿)

按 design doc §9 选合适的草稿,把 X% 替换成实测数字,写到:

`docs/interview/resume-bullets.md`(在已有的 5 条 bullet 后追加 1 条作为可选第 6 条,或者替换某一条不太硬的)

# 验收标准

- [ ] `progressive_rag_eval_18.jsonl` 18 条 query
- [ ] `bench_progressive_rag.py` 能 smoke / both / mark-mode
- [ ] 实际跑出 36 次结果 csv(允许少量失败,> 30 即可)
- [ ] 评测报告 .md 完整
- [ ] 简历 bullet 草稿(用户最终定稿)

# 不要做的事

- ❌ 不要修改 design doc
- ❌ 不要修改主 graph 代码(评测只读不写业务代码)
- ❌ 不要在评测脚本里调"提高 progressive 数字"的小手段(如刻意走 oneshot 减少检索)
- ❌ 不要把人工打分自动化(诚实数字 > 漂亮数字)
- ❌ 不要把评测结果直接 push 到任何远端

# 完成后报告

```markdown
## Phase 4 评测完成报告

### 评测覆盖
- 18 条 query × 2 模式 = 36 次,实际跑通 N 次,失败 M 次

### 关键数字
- 多跳场景答案完整度:one-shot X.X / progressive Y.Y(+Z.Z)
- 多跳场景文档覆盖率:one-shot XX% / progressive YY%(+ZZ%)
- Tokens:one-shot N / progressive M(-L%)
- 延迟:one-shot N ms / progressive M ms

### 报告路径
docs/benchmarks/progressive_rag_report_<date>.md

### 简历草稿(待用户定稿)
[贴 1-2 个候选 bullet]
```

==== PROMPT END ====
