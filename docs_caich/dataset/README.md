# 微调数据集（dataset/）

工时 Agent 微调用数据。配套方案见 [`../finetuning.md`](../finetuning.md)。
本目录只放**模板 + 生成脚本产物**；真实语料从 192 workhour 库 `conversation_logs` 表拉取。

## 文件

| 文件 | 用途 |
|------|------|
| `sft.template.jsonl` | SFT 数据模板（messages 格式，含 tool-calling 与纯文本两类） |
| `dpo.template.jsonl` | DPO 数据模板（prompt + chosen + rejected 偏好对） |
| `prompts.jsonl` / `prompts2.jsonl` | 首批用户消息池（100 条，带 dim/trap 元信息） |
| `prompts_v2.jsonl` | 扩量用户消息池（387 条，多样话术，非雷同模板） |
| `build_finetune_data.py` | **从 Langfuse trace 自动构建 sft/dpo（含工具选择 golden 对齐）** |
| `sft.jsonl` / `dpo.jsonl` | 上面脚本的产物（当前：**SFT 229 + DPO 157**） |

### 对齐 eval 指标（工具选择 80%）

eval 指标是"工具选择准确率"，所以数据必须瞄准它，否则训了也不动指标：
- **SFT 正样本改判据**：从"工具执行成功"→"**选对工具**"（拿 prompt 的期望工具 `tool` 当 golden 比对）。
  原因：mock 让**选错的工具也执行成功**，用"成功"当正样本会把错误选择当范本、教模型继续选错。
- **新增工具边界 DPO**：`wrong_tool`（模型选错→chosen=对工具/rejected=错工具，如"部门前5名"→sql_query vs compute_statistics）+ `should_call_tool`（该调没调）。
  这 21 条直接打 80% 的混淆点（query/统计/SQL 边界）。
- 训练用 `prompts_v2`（387），eval 用原始 100 条 probe（给出 80% baseline）——两批**不重叠**，避免泄漏。
- ⚠️ 期望工具是**人工判定的 golden**，query/统计边界本身有主观性，"准确率"含这层噪声。

## 生成流程

```bash
# 1) 本地起 Agent（F5），把 prompt 打进去 → Langfuse 落 trace
python scratchpad/batch_send.py            # 或手动发请求

# 2) 从 Langfuse trace 抽数据集（读仓库根 .env.local 的 LANGFUSE_*）
python docs_caich/dataset/build_finetune_data.py \
    --session-prefix batch100-<ts> --synth-chosen
```

依据 `app/services/langfuse_client.py` 埋的点位构建：
- **SFT** ← 工具**成功**的轨迹（prompt=完整 messages，answer=模型 tool_call）
- **DPO** ← 规则检测的“客观错”：`project_name_as_id`（项目名当 ID）、`duration_over_limit`（>10h）、`duration_not_half_step`、`over_privilege`（employee 查他人）
- **环境错**（连接失败/超时）自动剔除，不进任何数据集

> ⚠️ **本地测的两个限制**：
> 1. 隧道关时工具大量失败 → SFT 正样本很少（知识问答/闲聊为主），DPO 里 `project_name_as_id` 的 chosen 只能用 `--synth-chosen` 合成澄清文案。要**理想 chosen（名称→真实 ID 的 save_workhour）**，需在数据采集时让 `resolve_project_id` 能真解析（起本地 SpringBoot + 本地库，路 B）。
> 2. 越权/参数越界这些 DPO 对是**真实模型行为**触发的，质量高；但样本量取决于 prompt 覆盖。

> 模板里的真实语料摘自 `conversation_logs`（如 id=480「今天做了测试工作两个小时」、
> id=482 带历史的「查询我参与的项目信息」）；参数由 `ai_response` + 工具 schema 重建。

---

## SFT 格式（监督微调）

**一条 = 一个完整对话，assistant 段是要模型照抄的标准答案。** 每行一个 JSON（JSONL）。

```jsonc
{"messages": [
  {"role": "system",    "content": "<系统提示 + 工具 schema>"},
  {"role": "user",      "content": "今天做了测试工作两个小时"},
  {"role": "assistant", "content": "",
   "tool_calls": [{"id":"call_0","type":"function",
     "function": {"name":"save_workhour",
                  "arguments":"{\"date\":\"2026-05-21\",\"duration\":2.0,\"description\":\"测试工作\"}"}}]}
]}
```

要点：
- `arguments` 是**字符串化的 JSON**（qwen3 / OpenAI tool-calling 惯例），不是对象。
- 字段名以 **工具 schema 为准**：`save_workhour` 用 `project_id / date / duration / description`（下划线，见 `app/tools/save_workhour.py:28`），**不是** DB 里的 `projectId`。
- 纯问答 / 闲聊类（knowledge_qa、general_chat）assistant 直接给 `content` 文本，无 `tool_calls`。
- 训练只对 assistant 段算 loss（mask 掉 prompt）。
- 数据来源：`conversation_logs` 里**执行成功**的行；参数用 `ai_response` 文本 + schema 重建。

---

## DPO 格式（偏好微调）

**一条 = prompt + chosen(好答案) + rejected(坏答案)。** 教模型「要像 chosen，别像 rejected」。

```jsonc
{"prompt": [
   {"role":"system","content":"<系统提示>"},
   {"role":"user","content":"今天在电商平台项目上做了8小时"}],
 "chosen":   {"role":"assistant","content":"","tool_calls":[{"id":"call_0","type":"function",
    "function":{"name":"save_workhour","arguments":"{\"project_id\":\"1023\",\"date\":\"2026-05-21\",\"duration\":8.0}"}}]},
 "rejected": {"role":"assistant","content":"","tool_calls":[{"id":"call_0","type":"function",
    "function":{"name":"save_workhour","arguments":"{\"project_id\":\"电商平台\",\"date\":\"2026-05-21\",\"duration\":8.0}"}}]}}
```

要点：
- `chosen` / `rejected` 结构与 SFT 的 assistant 段完全一致，只是给两个版本。
- **只在模型给的值『客观错』时才当 rejected**（项目名当 ID、越权调用、超 24 小时、漏必填参数），
  不能把「后端 401 / 日期锁定 / 用户改主意」这类环境或业务因素当 rejected（详见 `../finetuning.md` 铁律）。
- chosen 可以是「拒绝 / 澄清」而非工具调用（如模板里越权、超时长两条：chosen 是拒答文本，rejected 是硬调工具）。
- **prompt 用 `context_snapshot` 还原成和线上一致**，否则训推不一致。

### 模板里的 4 个真实坑（来自 conversation_logs 失败样本）

| # | 用户输入 | rejected（错） | chosen（对） |
|---|---------|---------------|-------------|
| 1 | 电商平台项目 8 小时 | `project_id="电商平台"`（项目名当 ID） | `project_id="1023"`（解析成 ID） |
| 2 | 本周填了多少工时 | `query_timesheet({})`（漏时间范围） | 带 `start_date/end_date` |
| 3 | 查李四的工时 | 直接调 `query_timesheet(member_name="李四")`（越权） | 拒答：employee 无权查他人 |
| 4 | 昨天填 30 小时 | `duration=30.0`（超 24h 上限） | 拒答：超上限，请分多天 |

### DPO 造数据来源

对应 `../finetuning.md` §1.2：
- **方法 1**：`conversation_logs` 里「项目解析失败 / 越权 / 超时长」的失败行 → rejected；同类成功行 → chosen。
- **方法 3**：同一 prompt 自采样多个回复，用 dry_run + schema 校验挑「跑通」vs「报错」配对。

---

## 三种格式对照

| | SFT | DPO | PPO |
|---|---|---|---|
| 一条数据 | prompt + **1 个好答案** | prompt + **好 + 坏一对** | **只有 prompt** |
| 好坏从哪来 | 历史成功 / 审批通过 | 好 vs 坏配对 | 现场采样 + reward 打分 |
| 需要 reward 函数 | 否 | 否（隐式偏好） | 是（在线 rollout） |
| 训练成本 | 低 | 中 | 高（本项目不推荐，见 `../finetuning.md`） |

> 本目录只提供 **SFT + DPO** 模板（项目推荐路线：SFT 打底 → DPO 精修）。PPO 太重，仅列于对照表。

---

## 数据飞轮闭环（本仓库实测跑通）

```
prompts.jsonl(100条用户消息)
  → 本地 Agent(F5) ── HTTP ──> mock_springboot(127.0.0.1:9900，隔离假后端)
  → Langfuse(work-hour 项目) 落 100 条完整 trace
     trace"chat"[role/user_message] → generate_with_tools[带参数tool_call]
       → resolve_project_id[名称→ID] → tool:*[参数+结果+成功/ERROR]
  → build_finetune_data.py ── 按点位抽 ──> sft.jsonl / dpo.jsonl
```

**mock 的定位**：让工具"真成功"以产出成功轨迹，但**绝不碰生产库**（全内存假数据）。
`mock_springboot.py` 在 scratchpad，忠实模拟 `projectName.contains`（`q in name`）。

## 当前产出与演进

| 阶段 | SFT | DPO | 说明 |
|------|:---:|:---:|------|
| 路A（隧道关） | 7 | 43 | 工具全连接失败 |
| 路B 松散 mock | 64 | 43 | LCS 乱配假 ID（脏） |
| 路B 严格 mock + 规范化 | 60 | 21 | SFT 塞了 mock 假 ID（毒） |
| **v2 清毒 + 扩量（当前）** | **250** | **136** | ✅ 无假 ID、多样 |

**清毒的关键改动**：SFT 的 save 样本**保留模型输出的项目名**（如 `project_id="电商平台项目"`），
**不写 mock 编的假数字 ID**——因为模型本就该输出名字、由 ParamResolver 在推理时解析。
DPO 陷阱分布（136）：项目名解析失败 69 / 超10h 28 / 非0.5倍 21 / 越权 18。

**残留噪声（诚实记录）**：偶发把目录里存在的项目（如"移动端App项目"）判成"解析失败"→ 澄清，
占个位数%，根因是 `param_resolver` 第三级模糊匹配（用户历史 LCS）的顺序相关抖动，非本流程引入。

## ⚠️ 数据可信度（面试必须讲清）

一条样本 = `prompt → 模型 tool_call`。**模型的决策在后端回应之前就产生**，所以：

| 信号 | 谁判定 | mock 影响 | 可信度 |
|------|--------|:---:|:---:|
| 工具选择、参数格式 | 模型 / 工具自校验 | 无 | ✅ 直接可用 |
| 越权、工时>10h、非0.5倍 | 权限逻辑 / 工具校验（后端无关） | 无 | ✅ 直接可用 |
| **项目名→真实ID** | 依赖真实项目目录 | **大** | ⚠️ ID 是 mock 种子，**真用需换生产只读拉的真实目录** |

**结论**：后端无关的信号（工具选择 / 越权 / 越界）mock 造的数据可信；
依赖真实业务数据的（项目名→ID）流程成立但 ID 是假的，落地前须替换真实目录。

## 关键设计决策（build_finetune_data.py）

- **环境错**（连接失败/超时）→ 剔除，不是模型信号
- **项目名解析成功** → SFT 正样本，`project_id` 规范化成真实 ID（教规范形式）
- **项目名解析失败** → DPO（chosen=澄清）；路B 实锤：能解析 ≠ 模型错，多数由 ParamResolver 兜底
- **越权 / 越界** → DPO（chosen=拒绝），规则检测 + 工具报错兜底

## 已知残留

`param_resolver` 第三级模糊匹配（用户历史项目 LCS）会把"开发工作"配成"测试工作"，
带来顺序相关噪声——这是**线上 ParamResolver 既有行为**，非本流程引入。
