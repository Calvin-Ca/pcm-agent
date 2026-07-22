# 工时 Agent 微调方案（SFT + DPO）

> 面向本项目（FastAPI + LangGraph 工时智能助手）的模型微调完整方案。
> 目标：微调主对话模型 qwen3-8b，提升「工具选择 + 参数填写」的准确率，
> 而不是训练一个更能聊天的模型。
>
> 整体路线：**SFT 打底（学会基本套路）→ DPO 精修（避开特定坑）**。
> 两者**共用同一套数据采集与工程**，只在「数据格式」和「训练方式」上分叉。

---

## 0. 定位与整体路线

**优化对象**：主对话节点 `app/services/langgraph_agent.py:223` `node_llm_with_tools` 背后的 qwen3-8b 策略，
即「给定用户话 + tools schema，模型输出哪个 tool_call / 什么参数 / 什么自然语言回复」。

**明确不做**：
- 不在本 repo 内训练。本 repo 只负责**采数据 + 构造数据集 + 离线评测**。
- 训练是 172（GPU 服务器）上的**独立离线 LoRA 作业**，产物是 adapter，挂回 vLLM。

**SFT 与 DPO 的关系（先后配合，非二选一）**：

```
基座 qwen3-8b → SFT（教会「工时该调什么工具、参数长啥样」）→ DPO（拿「对 vs 错」对比精修，避开特定坑）
```

- **SFT** = 给新员工的标准流程手册（照抄标准答案）
- **DPO** = 拿他填对 / 填错的单子对比，纠正坏习惯（对比学习）

**为什么不做 PPO**：需先训 reward model + 在线 rollout，显存要 3~4 份模型、超参敏感、基础设施缺；
工时这种可规则校验的确定性场景收益小，不划算。

**对本项目怎么选**：
- 模型基础工具调用还不熟 → **先 SFT**，拿历史成功案例快速上手。
- 模型基本会了、只在特定参数上老犯错（本项目真实情况：项目名当 ID、memberId 回退）→ **直接 DPO 更对症**。
- 稳妥做法：**SFT 打底 + DPO 精修**，两个都做，数据复用（见 §1.3）。

---

# 第一部分：SFT / DPO 共用

以下 §1.1 ~ §1.6 对 SFT 和 DPO **完全通用**，只造一次数据、搭一次工程，两种训练共享。

## 1.1 数据来源（共用）

原始 trajectory 已经在落库：`app/services/conversation_logger.py` 每次对话写入
192 workhour 库的 `conversation_logs` 表（模型 `app/models/conversation.py`）。

**⚠️ 实测核实（2026-07-16，只读账号连 192 workhour 库）**：数据存在但**比预期薄**，直接影响可行性。

| 项 | 实测值 |
|----|--------|
| 总行数 | 484 |
| 带工具调用（`tool_count>0`） | 301 |
| 时间跨度 | 2026-04-25 ~ 2026-07-09 |
| save_workhour / query_timesheet / query_project | 106 / 119 / 14 |
| `context_snapshot` 有非空 history | 130 / 484 |
| `ai_response`（最终自然语言回复） | ✅ 有，内容完整 |
| `tools_called` 里含 **arguments（参数）** | ❌ **0 条** |
| `tools_called` 里含 **result（结果详情）** | ❌ **0 条** |

各字段可用性：

| 字段 | 含义 | 现状 |
|------|------|------|
| `user_message` | 用户输入 → prompt | ✅ 可用 |
| `ai_response` | 最终自然语言回复 | ✅ 可用 |
| `context_snapshot` (JSON) | 最近 2 轮历史 + 记忆，用于还原 prompt | ⚠️ 仅 130/484 有历史，其余空 |
| `tools_called` (JSON) | **实际只存 `{tool_name, success}`**（失败加 `error`/`rejected`） | ⚠️ **无参数、无 result** |
| `intent` / `route_type` / `model_name` / `duration_ms` | 元信息 | ✅ 可用 |

`tools_called` 真实形态（DB 注释写的 `[{name, params, result}]` 是理想、非现实）：
```json
[{"success": true, "tool_name": "save_workhour"}]            // 成功
[{"tool_name": "save_workhour", "success": false, "error": "..."}]  // 失败
```

拉数 SQL（能拿到的字段）：
```sql
-- 192 workhour 库
SELECT id, user_id, user_message, context_snapshot, tools_called, intent, ai_response
FROM conversation_logs
WHERE tool_count > 0
ORDER BY user_id, request_time;
```

### 🔴 前置改造（P0 真正第一步，不做则参数级微调无从谈起）

当前日志**没存工具调用的参数与结果**，所以：
- ✅ **现在就能训「工具选择」**（prompt → 调哪个工具 + 成不成功），方法 1 打标成立。
- ❌ **训不了「参数填写」**（本方案旗舰卖点 `projectId=电商平台` vs `1023`）——因为日志里根本没有模型填的参数值。

**必须先改埋点**：`app/services/langgraph_agent.py` 拼 `log_tools_called` 处（约 `:1354` 成功分支、`:1468` 多工具分支、`:1328/:1493` 失败分支），
把每个工具调用的 **`arguments`（模型填的参数）和 `result`（执行返回）一并写入**，`tools_called` 变成真正的 `[{tool_name, arguments, result, success}]`。
改完攒 2~4 周新数据，才能支撑参数级 SFT/DPO。存量 484 条只能用于工具选择级别的训练。

## 1.2 造数据：给答案打标签（共用）

原始 trajectory 只是「问题 + 模型答过什么 + 结果」，要变成训练数据，得判断**每个答案是好是坏**。
下面 4 种方法产出的「好 / 坏答案」，**SFT 用其中的「好答案」，DPO 用「好 vs 坏」的配对**（见 §1.3）。

#### 方法 1：从「报错」里判好坏（今天就能做，量大）

`tools_called[].result` 里带执行结果：
- **好(chosen)** = 执行 `success` 且结果非空
- **坏(rejected)** = 报错 / 空结果 / 权限拒绝 / 参数校验失败

优点：零人工、立刻有。缺点：弱信号，主要能判「别调出错的工具 / 参数」。

#### 方法 2：Join 业务表拿「审批」强信号（本项目王牌）

工时系统最值钱的信号在业务表：填报被**通过 / 驳回**。
拿 `tools_called` 里 `save_workhour` 的 params（user + projectId + date + hours），
去 join workhour 库的**工时填报 / 审批表**：
- **好** = 最终**入库且审批通过**的那版参数
- **坏** = 模型原始给的、但被**用户改字段后才提交**、或被**驳回**的那版

这是「人已经用脚投票」的**免费标注**，质量最高。
> ⚠️ 要区分「模型给错了」（算坏）vs「用户临时改主意」（不算），只在模型给的值**客观错**时才判坏。

#### 方法 3：On-policy 自采样 + dry_run 裁判（冷启动主力，推荐）

不依赖线上审批积累，当天就能造数：
1. 从 `conversation_logs` 取真实 `user_message` 当 prompt 池（保证分布真实）
2. 让当前 qwen3-8b 对每个 prompt **采样 2~4 个回复**（temperature > 0）
3. **裁判排序**：
   - 首选**可执行裁判 dry_run**（见下）：真的试跑 tool_call，能跑通 / 参数对的更好 → 客观 reward
   - 补充 **LLM-as-judge**：更强模型按 rubric 打分（工具选对、参数齐、无越权）

优点：量可控、prompt 真实、裁判客观。线上审批数据要攒几周，自采样当天出上千条。

#### 方法 4：显式反馈 / 小样本人工（补盲区）

- 前端加点赞点踩 → 同 prompt 两次回复配对（改前端，ROI 后置）
- 已知坑做 50~100 条**人工 golden**（项目名当 ID、memberId 漏填回退全员、日期歧义），量小但精准，兼做 eval set。

#### 什么是 dry_run（方法 3 的裁判）

**dry_run = 「只预演，不真做」**：把流程走一遍给你看结果，但最后真正写库的动作不执行——像网购的「确认订单」页。
本项目 `batch_save_workhour` 就带这个开关（`dry_run=True` 只预览不入库）。
价值：**免费验证一个 tool_call 对不对，又不污染真实数据**。工时场景对错**机器能自动判**（项目 ID 存不存在、日期合不合法），造数据几乎不用人工。

## 1.3 数据复用关系：SFT 数据 = DPO 数据的「好答案」那一半（关键）

只要**造数据时统一按 DPO 格式采集（prompt + 好答案 + 坏答案）**，SFT 数据就是白送的：

```python
# DPO 数据 → SFT 数据，一行搞定：扔掉 rejected 即可
sft_data = [{"prompt": d["prompt"], "answer": d["chosen"]} for d in dpo_data]
```

- **DPO → SFT**：✅ 可以，抽 `prompt + chosen`、丢 `rejected`
- **SFT → DPO**：❌ 不行，SFT 只有好答案、缺 rejected，配不成对

所以**统一按 DPO 标准造数据（带 rejected），SFT 自动就有**，别反过来。

两点注意：
1. **SFT 对 chosen 质量要求更高**：DPO 里 chosen 只要「比 rejected 好」即可；SFT 是让模型**照抄**，chosen 必须是**真标准答案**。来自方法 1/2（成功 / 审批通过）的够格；来自方法 3（自采样挑相对好的）可能只是「没那么错」，当 SFT 范本要再筛。
2. **数据量不同**：SFT 通常要更多（几千~几万）把套路教扎实；DPO 不需大（1k~5k 对）。实践中 SFT 用更大的一批纯正样本，DPO 用其中带 rejected 的子集，两者有重叠但不相等。

## 1.4 数据管道要补的工程（共用）

现有 `conversation_logs` 已覆盖 trajectory，还需补：

1. **数据集构造脚本** `scripts/build_finetune_data.py`（离线批）：
   - 读 `conversation_logs` → 按 §1.2 规则打好 / 坏标签 → **同时输出两份**：
     - `sft.jsonl`（prompt + answer）
     - `dpo.jsonl`（prompt + chosen + rejected）
   - 去噪：过滤太短 / 工具无关闲聊、dedup、好==坏丢弃
2. **（可选）审批回调 / 显式反馈端点**：`app/api/internal_tools.py` 增一个端点，
   接收 SpringBoot 审批动作或前端点赞点踩，回填标签（支撑方法 2 / 4）。
3. **dry_run 裁判封装**：把 `batch_save_workhour(dry_run=True)` 包成可批量调用的校验函数，供方法 3 用。

## 1.5 训练环境（共用）

- **框架**：Hugging Face TRL（SFT 用 `SFTTrainer`，DPO 用 `DPOTrainer`）或 LLaMA-Factory（配置更省事）
- **基座**：qwen3-8b，**LoRA / QLoRA**（r=16~32，α=32，target 全 attention + mlp），172 单卡够
- **chat template**：必须与 vLLM 推理时**完全一致**的 qwen3 模板，否则训推不一致白训（建议做 CI 校验）——SFT/DPO 都适用

## 1.6 部署回环（共用）

无论 SFT 还是 DPO，产物都是 LoRA adapter，挂法相同：
1. **vLLM 动态加载 adapter**（`--enable-lora`，推荐）：可多 adapter 并存、秒级回滚
2. merge 进基座重发一个 vLLM 端点

**灰度**：新 adapter 挂成第二端点，`langgraph_agent` 按流量比例路由，对比线上指标，坏了直接摘。
（SFT 打底 + DPO 精修时，最终上线的是「SFT 权重之上再叠 DPO」后的 adapter。）

---

# 第二部分：SFT 专属

## 2.1 SFT 在做什么

**SFT（监督微调）= 给模型一堆「问题 → 标准答案」，让它照着背。** 用交叉熵 loss 让模型输出尽量贴近标准答案。
只有正样本，教「遇到这种话就这么答」，教不了「别这样错」。

## 2.2 数据格式（只有一个好答案）

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "帮我把今天在电商平台项目上的8小时填一下"},
    {"role": "assistant", "content": null,
     "tool_calls": [{"name": "save_workhour",
                     "arguments": {"projectId": 1023, "date": "2026-07-14", "hours": 8}}]}
  ]
}
```

覆盖各类场景，多喂几百上千条：

| 类型 | 用户 | 标准答案 |
|------|------|---------|
| 填报 | 帮我填今天电商平台8小时 | `save_workhour(projectId=1023, ...)` |
| 查询 | 我这周填了多少工时？ | `query_timesheet(memberId=当前用户, ...)` |
| 知识 | 加班费怎么算？ | 走 RAG 检索，回复加班政策 |
| 闲聊 | 你能帮我做什么？ | 我可以帮你填报 / 查询工时、生成周报…… |

数据来源：§1.2 方法产出的**「好答案」**（`prompt + chosen`），优先取方法 1/2（成功 / 审批通过）的高质量样本。

## 2.3 训练配置（SFT）

- **框架**：TRL `SFTTrainer`
- **LoRA**：同 §1.5
- **关键超参**：
  - `lr=1e-4 ~ 2e-4`（LoRA SFT 常用，比 DPO 大）
  - `epoch=2~3`（要把套路记扎实，可比 DPO 多）
  - 只对 assistant 段算 loss（mask 掉 prompt）
- **数据量**：几千~几万条，越全越好

## 2.4 SFT 的目标

让模型**稳定学会工具调用的基本套路**：认识 7 个工具、知道大致该填哪些参数、格式合法。
SFT 后模型「大致会了但常犯特定错」，交给 DPO 精修。

---

# 第三部分：DPO 专属

## 3.1 DPO 原理（含 ref model）

DPO 的目标**不是**无脑「最大化 chosen、最小化 rejected」——那样模型会为了拉大差距把分布带跑偏（塌缩 / reward hacking）。
所以 loss 里锚了一个**冻结的参考模型 π_ref**，优化**相对 ref 的对数概率比**：

```
loss = -log σ( β · [ (log π_θ(chosen)   − log π_ref(chosen))
                   − (log π_θ(rejected) − log π_ref(rejected)) ] )
```

直觉：**「让 chosen 比 rejected 更可能，但别偏离原始模型太远」**。π_ref 就是「别跑太远」的桩子（等价于加 KL 约束）。

- **π_ref = 训练前的模型快照**（若先做了 SFT，则是 **SFT 后的模型**），全程冻结，只算「原来这句话的概率」。
- π_θ = 正在训练、参数在动的模型。每个 step 同一批数据过两次：π_θ（反向传播）、π_ref（只前向 no_grad）。

**为什么 LoRA 下「共享基座省显存」**：LoRA 训练时基座 W 冻结，只训低秩增量 `ΔW=B·A`（<1% 参数）。于是：

```
π_θ  = 基座 W(冻结) + LoRA(可训练)   ← 打开 adapter
π_ref = 基座 W(冻结)                 ← 关闭 adapter，就是「没加 LoRA 的自己」
```

两者共用同一份基座，算 π_ref 时关掉 adapter 跑一遍即可。显存只需一份 8b（还可 4-bit = QLoRA），单卡可跑。
TRL 的 `DPOTrainer` 检测到 PEFT/LoRA 模型时**无需再传 `ref_model`**，内部自动切 adapter 开关。

## 3.2 数据格式（好 + 坏 一对）

```json
{"prompt": [{"role":"system","content":"..."},{"role":"user","content":"帮我填今天电商平台8小时"}],
 "chosen":   {"role":"assistant","tool_calls":[{"name":"save_workhour","arguments":{"projectId":1023,"date":"2026-07-14","hours":8}}]},
 "rejected": {"role":"assistant","tool_calls":[{"name":"save_workhour","arguments":{"projectId":"电商平台","date":"2026-07-14","hours":8}}]}}
```
> 上面的 rejected 是本项目真实踩过的坑：把项目名当成了 projectId。

数据来源：§1.2 方法产出的**「好 vs 坏」配对**。

> ⚠️ 两条铁律：
> 1. **prompt 用 `context_snapshot` 还原成和线上推理完全一致**（含 system + 注入的历史 / 记忆），否则训推不一致白训。
> 2. 方法 2 里严格区分「模型错」vs「用户改主意」，只在模型值**客观错**时才算 rejected。

## 3.3 训练配置（DPO）

- **框架**：TRL `DPOTrainer`
- **ref model**：LoRA 下**不用单独传**，切 adapter 开关即可（见 §3.1）
- **关键超参**：
  - `beta=0.1`（工时确定性强，可调高到 0.2~0.3 收紧、防偏离）
  - `lr=5e-6 ~ 1e-5`（比 SFT 小一到两个量级）
  - `epoch=1~2`（DPO 极易过拟合，**宁少勿多**）
- **数据量级**：不需大，**1k~5k 高质量对**即可见效；先跑方法 1/3 的几百对验证链路

## 3.4 DPO 的目标

在 SFT 打好的底子上，用「对 vs 错」对比把模型从**特定坑**里拽出来：项目名当 ID、memberId 漏填回退全员、日期歧义等。

---

# 第四部分：评测、落地、风险（共用）

## 4. 评测与防作弊（方案成败在这）

**离线 eval set**（从 §1.2 数据留出，**按时间切分、不能随机**，防泄漏）：
- 指标：工具选择准确率、参数字段准确率（projectId / memberId / date）、越权率、空结果率
- golden 用例：手工 20~50 条覆盖已知坑（项目名→ID、memberId 回退、日期歧义）

**守护栏**：
- DPO 尤其要监控 **KL / 隐式奖励 margin**，防 reward hacking（学「话术讨好」而非真做对）→ eval 必须看**任务成功率**而非回复流畅度
- 每轮训练后**必跑现有回归**：`fastapi-service/tests/test_core_functionality.py`、`test_intent_router.py`，防退化
- 永久保留「训练前模型」基线，SFT 后、DPO 后各存一版，逐级对比

## 5. 分期落地

| 阶段 | 内容 | 数据来源 | 面试可展示点 |
|------|------|---------|-------------|
| **P0** | 拉数 + `build_finetune_data.py` 骨架（同出 sft/dpo 两份） | 现有 `conversation_logs` + 方法 1 | 数据飞轮闭环 |
| **P1** | **SFT 打底**：172 上 LoRA SFT 跑通，adapter 挂 vLLM | 方法 1/2 的好答案（几千条） | 工具调用基本盘 |
| **P2** | **DPO 精修**：在 SFT 之上做 LoRA DPO，灰度上线 | 方法 3 自采样 + dry_run + 方法 4 golden（1~3k 对） | 免标注造数 + 对比学习 + 灰度回滚 |
| **P3（持续）** | 审批信号持续供数，滚动重训 + eval | 方法 2 审批 join（主）+ 方法 1 补 | 真实偏好 + 防作弊 / 防退化 |

## 6. 主要风险

1. **配对质量 > 数量**：方法 2 靠 `user_edited_fields`，只在模型值**客观错**时才算 rejected，别把「用户改主意」当噪声喂进去。
2. **训推 template 不一致**：SFT/DPO 通吃的头号白干原因，单列一条 CI 校验。
3. **SFT chosen 质量**：SFT 是照抄，chosen 必须是真标准答案，方法 3 的自采样样本要筛。
4. **DPO 过拟合 / 风格塌缩**：1 epoch 就可能让回复变生硬，靠 §4 通用 eval 兜底。
5. **冷启动数据量**：P2 用自采样先跑通；方法 2 的审批数据攒 2~4 周再作为主力，别急。

---

## 附：一句话总结

- **SFT 打底、DPO 精修**，两者**共用一套数据采集与工程**，只在数据格式（SFT 一个好答案 / DPO 好坏一对）和训练方式（照抄 / 对比）上分叉。
- **数据白送**：统一按 DPO 格式造数据（带 rejected），`prompt + chosen` 就是现成的 SFT 数据；反过来不行。
- **好 / 坏答案不用编**：好 = 历史成功 / 审批通过 / dry_run 跑通；坏 = 历史报错 / 用户改掉 / 采样跑不通。
- **工时系统的运气**：对错能自动判（能不能入库 / dry_run 通不通），造数据几乎零人工——这是通用 chatbot 没有的优势。
- 本 repo 是**数据飞轮 + 评测台**，训练交给 172 上的独立 LoRA 作业。
