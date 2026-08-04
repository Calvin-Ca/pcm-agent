## 工时管理系统 · AI 智能助手（Agent 后端）

**角色**：Agent 应用开发
**技术栈**：FastAPI · LangGraph · Function Calling · LangChain RAG · vLLM · Milvus · Redis · Docker

为企业内部工时系统建设自然语言交互入口，让用户通过对话完成工时查询、填报、统计分析、周报生成和制度知识问答。项目采用独立 FastAPI Agent 服务接入 Spring Boot 主后端，我负责 Agent 编排、工具链治理、RAG/SQL 分析、写操作风控、上下文记忆与生产部署联调，并通过 SSE 流式返回文本和图表事件。

- **Agent 编排与路由**：基于 LangGraph 重构主流程，以 `node_llm_with_tools` 作为 Function Calling 主节点，在一次模型调用中完成工具选择与结构化参数生成，并按 `tool_calls / knowledge_qa / general_chat` 分支路由；保留规则式 `IntentRouter` 作为 LLM 不可用时的降级路径。

- **工具链治理与业务接入**：抽象单例 `ToolRegistry`，将业务工具统一收敛到 `ParamResolver -> PermissionValidator -> TaskExecutor` 链路；接入工时查询、项目查询、统计、周报、单条填报、批量填报、SQL 查询等 7 个工具，修复默认查全员数据、项目名被误当项目 ID 等高风险问题。

- **参数解析与写操作风控**：实现项目名/成员名到后端 ID 的统一解析，支持精确查询、后缀归一和历史项目模糊匹配；写入类工具走 dry-run 预览与确认流程，低置信度实体不猜测，降低工时写错项目的风险。

- **知识问答与复杂分析**：基于 LangChain 搭建 Milvus 向量检索、BM25、MultiQuery 改写和 CrossEncoder 重排链路，用于制度/流程类知识问答；补充 SQL Agent 处理 Function Calling 难以表达的跨表统计，并用只读白名单、危险关键字、表/列限制和 LLM 改写分层降低 SQL 风险。

- **上下文与稳定性工程**：实现 Redis 短期会话记忆（TTL 30min，保留 10 轮）与长期用户记忆，经 PromptBuilder 注入上下文；对 Agent Loop 增加迭代上限、重复工具签名检测、连续异常熔断和 observation 去噪/截断，保证异常路径有确定性出口。

- **工程化与部署落地**：Prompt 模板 YAML 化并支持热加载，SSE 扩展 `event: chart` 返回图表事件；生产侧使用 GPU 服务器 Docker Compose 部署 AI 服务和 vLLM，经反向 SSH 隧道与 nginx 打通公网 Spring Boot 到内网 AI 服务。

补充探索 Claude Code 插件化场景，将“git 提交历史 -> 工时草稿 -> 二段确认入库”封装为团队内网插件，读 git 走 slash command，本地确认后通过 HTTP MCP 网关复用服务端写库和权限治理能力。

项目最终形成覆盖查询、填报、统计、周报、知识问答和 SQL 分析的 7 类工具能力，并完成 AI 服务、Spring Boot 网关、内网数据库和公网访问链路的生产联调。


## 二、问答（逐条预判追问）

### 第 1 条：简要介绍一下这个项目
这是一套工时管理系统的 AI 智能助手服务，让用户可以用自然语言完成工时的查询、填报、统计、周报生成等操作，替代原来在表单里逐格填写的方式。我负责的是其中的 Agent 服务模块，用 FastAPI + LangGraph 构建，独立部署，与 Java（Spring Boot）主后端集成。

架构分层
整体是一个典型的 Function Calling Agent 架构，请求链路是这样的：
Spring Boot 网关 → POST /api/ai/chat/stream → LangGraph Agent → SSE 流式返回
核心是 LangGraph 编排的一个主节点 node_llm_with_tools，用本地 vLLM 部署的 qwen3-8b 做 Function Calling，根据模型判断分三条路走：

1. 工具调用（tool_calls）——查工时、填工时、统计、周报等业务动作
2. 知识问答（knowledge_qa）——走 LangChain RAG（Milvus 向量 + BM25 + CrossEncoder 重排）
3. 闲聊（general_chat）——LLM 直接回复
另外有一条降级链路：当 LLM 不可用时，退回到基于规则匹配的 IntentRouter，保证服务不完全挂掉。

我认为设计上几个关键点
- 工具执行前有三道处理：ParamResolver（把"项目名/成员名"这类自然语言参数解析成后端要的 ID，带缓存）→ PermissionValidator（基于角色的细粒度权限校验）→ TaskExecutor（依赖注入 + 实际调用）。这样把"参数解析""权限""执行"三个关注点拆开了。
   
  PermissionValidator：先根据工具类型、数据可见性
  
  工具调用由 TaskExecutor 统一编排：先完成依赖参数注入和 PermissionValidator 权限校验，再注入认证上下文并调用工具 handler；handler 在访问 Spring Boot 前通过 ParamResolver 将项目名、成员名等自然语言参数解析为业务 ID。这样把参数标准化、权限治理和执行控制三个关注点解耦。


- 工具层有 7 个业务工具，6 个通过 HTTP 调 Spring Boot 现有接口，1 个是 SQL Agent（自然语言转 SQL，直接查库，用于复杂分析场景）。
- 记忆分两层：短期会话记忆和长期用户记忆，都放 Redis。
- 权限体系是六级角色（employee 到 superAdmin），由网关注入请求头，Agent 侧每次工具调用前校验。

部署现状
生产是三台机器：GPU 服务器跑 AI 服务和 vLLM，公网机跑 Spring Boot + nginx，还有内网数据库。中间用反向 SSH 隧道打通内外网。

### 第 1 条：Function Calling 单次调用

**Q1：为什么单次 FC 调用能比两段式快？具体省了什么？**
> 传统方案是两次串行 LLM 调用：第一次输出意图分类（tool_execution / knowledge_qa / chat），第二次根据意图再抽取参数。两次调用 = 两倍网络往返 + 两倍 prefill。我用 FC 的 `tool_choice="auto"`，让模型在一次推理里直接吐出 `tool_calls`（含工具名和结构化参数），意图就隐含在"选了哪个工具/没选工具"里——没选工具且返回文本就是闲聊，选了 `knowledge_qa` 就是走 RAG。省掉了整整一次 LLM 调用。

**Q2：那意图分类的准确性会不会下降？毕竟少了一个专门的分类步骤。**
> 实测没有明显下降，因为 8B 模型对"该不该调工具、调哪个"本身判断力够用，工具的 description 就是分类依据。但我保留了**两条保险**：一是把规则路由 `IntentRouter` 作为降级路径，FC 解析失败或模型不可用时回退；二是留了 `BENCHMARK_FORCE_FALLBACK` 开关强制走两段式，方便我随时 A/B 对比两种方案的准确率和延迟，用数据决策而不是拍脑袋。

**Q3：降级到规则路由，规则是怎么写的？会不会很脆？**
> 规则路由是关键词匹配 + 槽位抽取的兜底，确实比 LLM 脆，但它的定位只是"LLM 全挂时别让用户白屏"，不是主路径。生产 99% 的流量走 FC，规则路由是 last resort。这是个可用性兜底的工程取舍，不追求降级路径的智能程度。

**Q4（陷阱）：8B 模型 Function Calling 稳定吗？吐出的 JSON 格式错了怎么办？**
> 这是本地小模型的真实痛点。我的处理：`generate_with_tools` 里对 `tool_calls` 的 arguments 做了 JSON 解析容错，解析失败就当作没有有效 tool_call、降级走 classify_intent；另外通过 `temperature=0.1` 降低格式抖动。如果再严格可以加 JSON Schema 约束解码（outlines/xgrammar），但当前 vLLM 版本下 0.1 温度的稳定性已满足生产。

---

### 第 2 条：Agent Loop 三重熔断 + 上下文工程

**Q1：展开讲讲三道闸分别防什么？**
> - **闸一（迭代上限）**：`agent_iterations >= max_iterations`（默认 5），到顶强制走 summarize 节点用已有结果兜底回答，而不是硬中断报错。
> - **闸二（重复签名）**：把最近 3 轮的 `(tool_name, 排序后的 args JSON)` 算签名，同一签名出现 ≥2 次就判定模型在原地打转，提前结束。这防的是 LLM 反复用相同参数调同一个工具。
> - **闸三（连续异常）**：最近 3 轮 observation 全是 `success:false / error`，说明工具链路坏了，继续调只是烧 token，直接熔断。

**Q2：为什么是"3 轮内重复 2 次"而不是别的阈值？**
> 经验值。设太松（比如连续 3 次完全相同才算）会浪费迭代次数；设太紧（出现 1 次重复就停）会误杀正常的"重试一次换个角度"。3 选 2 是在"给模型纠错空间"和"及时止损"之间的平衡。这个阈值我做成可调的，真上规模会用线上数据回归最优值。

**Q3：达到上限走 summarize 兜底，如果一条有用结果都没有呢？**
> summarize 节点会消费 `agent_history` 里所有轮次的 observation，即便都不完美也会让 LLM 综合给个"我查到了部分信息 + 建议"的回答，而不是直接抛异常。最差情况（history 为空）返回固定兜底文案。核心原则是**任何分支都有确定性出口，不让用户看到 500**。

**Q4：Observation 去噪，你怎么确定删的字段对 LLM 真没用？会不会删错？**
> 我维护了一个白名单外的"噪声字段集" `_LLM_NOISE_KEYS`（记录 UUID、project_id、created_at 这类）。判断标准是：**这个字段 LLM 生成回答时会不会引用它**。比如 `project_id` 是 UUID，但结果里已经有 `project_name`，LLM 只会说项目名不会念 UUID，所以删。**但 `user_id` 我特意保留**——跨用户查询时 LLM 需要它区分"这是张三还是李四的记录"。这是个 case by case 划线的过程，删错的代价就是 LLM 少个字段，可控。

**Q5：32K context 装 100 条记录，超了怎么办？**
> 两层防护：去噪后还超 8000 字符，`_truncate_observation` 会截断并打 `_truncated` 标记 + 保留 preview；历史消息层面还有条数截断（保留 system+最近若干条）和 token 估算截断（字符/3 + tools schema 开销）。等于"单条结果"和"整体历史"两个维度都设了闸。

---

### 第 3 条：多工具 DAG 并行 + 中文日期展开

**Q1：多工具并行，任务间有依赖怎么办？会不会乱序？**
> `TaskPlan` 用 `TaskNode` 带 `dependencies` 字段建 DAG。当前 LLM 单轮多 tool_calls 的场景生成的都是**无依赖的并行任务**（dependencies 为空），由 TaskExecutor 并发执行后统一汇总。框架本身支持依赖编排（`plan_and_execute` 路径 B 走 PlannerAgent 生成带依赖的 plan），按拓扑序执行。所以是"框架支持依赖，当前主用并行"。

**Q2："周一到周五"这种你用正则硬解的，为什么不直接让 LLM 输出多个日期？**
> 两个考虑：一是**确定性**——日期计算是确定逻辑，正则/datetime 算"本周一是几号"100% 准，让 8B 模型算相对日期反而容易错（尤其跨月、跨年）；二是**省 token 和省调用**——不用为了展开日期再请求一次 LLM。所以分工是：LLM 负责理解"用户想批量填"，日期展开这种确定性计算交给代码。这也是 agent 设计的一个原则：**能用确定性代码做的别交给模型**。

**Q3：展开成 5 个并行任务，其中第 3 天失败了，整体怎么表现？**
> TaskExecutor 单任务失败不阻断其他任务，summarize 会拿到 5 个结果（4 成功 1 失败），汇总时如实告诉用户"周一二四五填报成功，周三因 XX 失败"。不是全成功才算成功的事务语义，是 best-effort + 明确反馈。如果业务要求原子性（要么全成要么全不），那得加事务包裹，当前需求不需要。

**Q4：并行执行用的什么？asyncio？并发数有上限吗？**
> 基于 `asyncio` 协程并发（工具底层是 httpx 异步调 Spring Boot API），execute_plan 带 120s 超时。当前单次并行任务数受 LLM 一次能吐的 tool_calls 数限制（通常个位数），没专门设信号量限流；真要防止打爆下游 API，会加 `asyncio.Semaphore` 控并发。

---

### 第 4 条：ParamResolver 三级实体解析

**Q1：三级降级具体怎么触发？能举个例子吗？**
> 用户说"帮我给预管理平台填工时"：
> - **第一级**：`projectName.contains=预管理平台` 精确搜，没命中（后端叫"预管理子系统"）；
> - **第二级**：剥常见后缀，"预管理平台"→"预管理"再搜，命中。
> - **第三级**（前两级都空）：拉该用户近 3 个月填过的项目列表，用**最长公共子串/输入长度**评分，要求连续匹配 ≥2 字且覆盖率 ≥30% 才算命中——这一级专门兜口语化简称，比如用户习惯叫"那个 AI 的项目"。

**Q2：为什么用最长公共子串而不是编辑距离或向量相似度？**
> 场景决定的。项目名匹配的特点是**用户输入通常是全名的一个连续片段**（"管理平台"→"XX 预管理平台"），LCS 正好抓"最长连续公共片段"，直觉上贴合。编辑距离对这种"子串包含"不敏感（长度差异会拉高距离）；向量相似度要嵌入模型 + 额外延迟，对这种短名匹配是杀鸡用牛刀。LCS 是 O(m·n) 纯本地计算，我还用滚动数组优化到 O(n) 空间，够快够准。

**Q3：第三级要拉用户历史，多一次 API 调用，延迟不担心吗？**
> 第三级只在前两级都失败时才触发，是低频路径。而且整层有**进程级缓存** `_resolve_cache`：同一个名称（含"查过但没找到"的 None 结果）缓存，同会话内重复提到同一项目直接命中，不重复打 API。所以高频路径（纯数字 ID 直接返回、或缓存命中）基本零额外延迟。

**Q4：缓存用进程级 dict，多实例部署会不会不一致？有内存泄漏风险吗？**
> 好问题，这是当前实现的局限。进程级 dict 在多副本下各管各的，不共享——但因为缓存的是"名称→ID"这种**幂等只读映射**，不一致顶多是某副本多打一次 API，无正确性问题。内存方面 dict 无上限确实有隐患，生产规模下我会换成带 TTL 和 LRU 上限的缓存（如 Redis），Redis 还能顺带解决多实例共享。当前数据量（项目数有限）还没到瓶颈，属于"先跑通再优化"的有意取舍。

**Q5（陷阱）：模糊匹配匹错了项目，把工时填错地方，这风险怎么控？**
> 这是填报类写操作最该警惕的。我的控制点：一是第三级有**双门槛**（连续 ≥2 字 + 覆盖率 ≥30%），低置信度直接判未命中、返回"没找到请确认"而不是猜一个；二是写操作前有 `clarify` 引导确认机制，缺关键参数会反问用户。如果要更稳，可以对模糊命中的结果做一次"您是指 XX 项目吗？"的二次确认——这是下一步会加的。

---

## 五、MCP × Slash Command × 插件市场（把工时能力接进 Claude Code / Codex）

> 这一节讲的是**另一条产品线**：不改前端网页，而是把工时能力做成 Claude Code 插件，让研发在 IDE/CLI 里用自然语言按 git 历史填工时。面试若问"你对 MCP / Agent 生态的理解"，这节是主战场。

### 0. 先用一句话把三个概念摆正（面试最容易混）

- **MCP server** = 给"模型"用的**能力/动作**（能读 git、能写库）。是给 Claude 挂的工具。
- **Slash command**（`/xxx`）= 给"用户"用的**流程快捷入口**，本质是一段预写好的提示词模板。
- **插件市场** = 把上面两样**打包分发**给全团队的机制，本质是"一个带清单文件的 git 仓库"。
- 一句话分工：**command 管"怎么做（流程）"，MCP 管"能做什么（动作）"，市场管"怎么发给所有人"。**

---

### 5.1 MCP 是什么 / 为什么 Claude 能识别并调用

**Q1：一句话说清 MCP 是什么？**
> MCP（Model Context Protocol）是"大模型 ↔ 外部工具"之间的标准协议，类比 USB。Claude 本身只会生成文字，不会读 git、不会写库；MCP server 就是提供这些动作的小程序。Claude 想干活时不自己动手，而是发一条"请调用 `collect_git_activity(since='上周')`"的消息，server 真去跑、把结果回给它。

**Q2：Claude 到底怎么"知道"有这么个工具、还能正确调用？（核心考点）**
> 靠**代码侧自动生成 schema + 配置侧声明 + 协议握手**三步：
> 1. **代码侧**：用官方 FastMCP，一个函数加 `@mcp.tool()` 装饰器就成了工具。装饰器会**反射函数签名和 docstring 自动生成 JSON Schema**——类型注解 `since: str` 变成 schema 的参数类型，有默认值的变成非必填，docstring 的 `Args:` 段变成每个参数的说明。**全程没手写一行 schema**。这也是为什么我把 docstring 写得很详细（甚至写了"拿到结果后请这样编排：1…2…3…"）——**这段话会直接进模型上下文，是在给 Claude 下编排指令**。
> 2. **配置侧**：在 `.mcp.json` 里登记"用哪个命令/URL 启动这个 server"。
> 3. **握手**：Claude Code（host/客户端）启动时按配置拉起 server，发 `tools/list` 拿到那份自动生成的 schema，转成"可用工具"注入对话。之后模型看到工具名、参数、描述，就能在合适时机产出 tool_call（工具名 + JSON 参数），host 路由去执行、把结果回填。
>
> **关键澄清**：不是模型自己"发现"脚本，发现和执行都由 Claude Code 这个 host 完成，模型只做"看到 schema → 决定调用"这一层。

**Q3：那 `estimated_hours` 估时基线为什么放代码里，不让模型估？**
> 这是 agent 设计的一条原则——**能用确定性代码做的别交给模型**。日期跨度、commit 数是确定数据，`clamp(max(span+0.5, 0.5×commit数), 0.5, 8)` 是确定公式，代码算 100% 稳。所以我把"读 git（确定性）"和"归纳工作内容/微调工时（判断）"分开：前者进工具，后者交给 Claude。而且工具每条草稿都带 `estimate_basis` 说明推导过程，模型可在基线上结合改动量调整，人也能核对。

---

### 5.2 stdio MCP vs HTTP MCP（两种"通信管道"）

**Q1：stdio 和 HTTP 两种 MCP 有什么区别？各自适合干嘛？**

| | stdio MCP | HTTP MCP |
|---|---|---|
| 跑在哪 | **用户本地**，Claude Code 拉起的子进程 | **远程服务器**，常驻服务 |
| 通信管道 | 进程的 stdin/stdout | 网络 HTTP 请求 |
| 适合 | 读本地文件/git（必须在你机器上） | 集中式服务、多人共用、写数据库 |
| 用户依赖 | 得装 Python 环境 | 零依赖，填个 URL 即可 |

> - **stdio** = standard input/output，命令行程序默认那两个流。Claude Code 把 server 当子进程拉起，通过管道：请求写进子进程 stdin，响应从 stdout 读回。对应代码就是入口的 `mcp.run()`（不带参默认 stdio）。
> - **读 git 最初用 stdio**：因为 git 仓库在用户本机，只有本地子进程才读得到。**写库改用 HTTP 网关**：集中在服务端、用户零配置。

**Q2（陷阱）：stdio server 为什么日志只写文件、不能直接命令行 `python xxx.py` 跑？**
> 两个坑都源于"stdout 是协议专用管道"：
> 1. **日志不写 stdout/stderr**，只写文件——往 stdout print 日志会污染 MCP 协议消息，管道缓冲区写满还会把子进程卡死。
> 2. **不能手动裸跑**——它会卡在"等 stdin 喂 MCP 消息"，而你没喂，看起来像死掉了。它天生是被 host 拉起、用管道喂消息的。

---

### 5.3 Slash Command 是什么

**Q1：slash command 和 MCP 是一回事吗？**
> 不是，两个维度：
> - **MCP 工具**给**模型**新增动作能力。
> - **slash command**（你在输入框打 `/fill-workhour 上周`）给**用户**一个流程快捷入口，本质是一个 `.md` 文件里的**提示词模板**。你打这个命令，Claude Code 就把这个 md 的内容当成你的提问喂给 Claude，`$ARGUMENTS` 变量接你命令后面那个"上周"。
> 效果 ≈ 你手动把一整套"第0步自检…第5步二段确认写库"的流程说明贴进对话。价值是把**复杂、易错、每次都要重复交代的流程**（尤其"必须二段确认、绝不代填他人"这些铁律）固化成一键触发，团队谁用都不漏步骤。

**Q2：command 和 MCP 在这个插件里怎么配合？**
> 分工清晰：`/fill-workhour` 命令负责**编排流程 + 读 git**（让 Claude 用内置 Bash 跑 `git log`，不走 MCP）；流程走到"写库"那步，再让 Claude 调 **MCP 工具** `save_workhour`。一个管"怎么做"，一个管"能做什么动作"。

---

### 5.4 插件市场机制（怎么发给全团队）

**Q1：Claude Code 的"插件市场"到底是什么？和 App Store 什么关系？**
> 先分清三个词：**plugin**（打包好的功能包，可含 command + MCP + agent）、**marketplace**（插件清单目录，货架）、**install**（用户拉到本地启用）。
>
> | App Store | Claude Code | 本项目实物 |
> |---|---|---|
> | 平台 | marketplace | 内网 git 仓库 `172.19.2.176/root/workhour_mcp.git` |
> | 货架清单 | `marketplace.json` | 列了 1 个插件 `workhour` |
> | 一个 App | plugin | `plugins/workhour/` 目录 |
> | 安装 | `claude plugin install` | 装到 `~/.claude/plugins/cache/` |
>
> **关键**：Claude Code 的市场**不是中心化网站，而是"任何一个 git 仓库，根目录放了 `marketplace.json` 就是一个市场"**。没有审核、没有上架流程，`git push` 到内网 GitLab 就算"发布"。

**Q2：一个插件由哪几个文件组成？**
> 最小骨架三样（我这个插件就这三样）：
> - `.claude-plugin/plugin.json`：身份清单（name=workhour, version=0.2.0, keywords）。
> - `.mcp.json`：声明要挂哪些 MCP server（这里是 HTTP 网关 `workhour-gateway`）。
> - `commands/fill-workhour.md`：一个 slash command。

**Q3：别人怎么装？装完本地发生了什么？**
> 两条命令：
> ```bash
> claude plugin marketplace add http://172.19.2.176:8929/root/workhour_mcp.git  # 加市场
> claude plugin install workhour@workhour-mcp                                    # 装插件
> ```
> 背后：Claude Code 把市场仓库 clone 到 `~/.claude/plugins/marketplaces/`，读 `marketplace.json` 知道有哪些插件，把插件缓存到 `cache/…/0.2.0/`，在 `installed_plugins.json` 记录锁定的 `gitCommitSha`（保证可复现）。之后 `/fill-workhour` 命令和 `save_workhour` 工具就自动进 Claude 上下文。

**Q4：是"谁都能装"吗？**
> 不是，**这是团队内网私有市场**。git 地址（`172.19.2.176`）是内网的，外人访问不到；插件真正干活还要连内网网关（`172.19.3.136:8765`）。只有能进公司内网的同事能加、能装。每人还需各自配 `WORKHOUR_ENTITY_ID` 环境变量代表自己身份（`.mcp.json` 里 `${WORKHOUR_ENTITY_ID}` 变量由本地环境注入）。

---

### 5.5 方案演进（这是加分项，体现工程判断）

**Q1（陷阱）：仓库里明明有个 stdio 的 `git_workhour_mcp_server.py`，为什么发布的插件里没有它？**
> 这是**方案演进**，值得主动讲：
> - **早期方案**：读 git 用 stdio MCP（`collect_git_activity` 工具）+ 写库复用 stdio 的 `workhour-save`（每人本地配 token）。问题：用户得装 Python + mcp SDK，还要各自配 token，接入成本高。
> - **上市场的 0.2.0**：读 git 改成 **slash command 直接跑 `git log`**（零 Python 依赖）；写库改成 **HTTP 网关 `workhour-gateway`**，身份集中在服务端用 `X-Entity-ID` + 网关 token 换 JWT（Service Account 模式），用户零配置。
>
> | | 早期 stdio 方案 | 上市场的 0.2.0 |
> |---|---|---|
> | 读 git | `collect_git_activity`（Python 工具） | `/fill-workhour` 直接跑 `git log` |
> | 写库 | stdio `workhour-save`（本地配 token） | HTTP `workhour-gateway`（服务端换 JWT）|
> | 用户依赖 | 装 Python + mcp SDK | 零依赖，`claude plugin install` 即用 |
> | 分发 | 手动 `.mcp.json` / `claude mcp add` | 内网 git 市场，两条命令安装 |
>
> **一句话总结取舍**：从"能力可用"到"团队可规模化接入"，核心是把用户侧依赖压到最小——**能用确定性代码（Bash 跑 git）做的下沉到 command，需要集中管理身份的（写库）上移到服务端网关**。

**Q2：一次 `/fill-workhour 上周` 的完整链路？**
> 1. 你打 `/fill-workhour 上周` → Claude Code 把命令 md（`$ARGUMENTS`=上周）喂给 Claude。
> 2. Claude 按 md 步骤用**内置 Bash** 跑 `git log --numstat`（这步不走 MCP）。
> 3. Claude 按估时公式算工时、出表格让你核对、问你记哪个项目。
> 4. 你确认后，Claude 调 **MCP 工具** `save_workhour`（来自 HTTP 网关）：先 `confirm=False` 预览 → 你点头 → `confirm=True` 真写。全程二段确认、只填本人。

---

## 六、模型微调（SFT / DPO）

> 配套方案见 `finetuning.md`，数据集模板见 `dataset/`。面试若问"你会怎么用微调优化这个 Agent"，主战场是这节。

### 6.1 为什么要微调这些？怎么验证问题真存在？（高频陷阱题）

**Q1：你说要用 SFT/DPO 优化"项目名当 ID""越权"这些——但优化的前提是这些真有问题。你怎么验证问题存在，而不是拍脑袋？**
> 我的原则是：**动微调前必须先回答三个问题，缺一个都不该上**——① 问题真存在吗、有多频繁？② 是不是模型的错？③ 微调是不是最优解（prompt/规则/后端改能不能更省地解决）？验证分四层，我在这个项目里实际做了前两层：
>
> **① 生产日志量化（第一手证据）**：查 `conversation_logs`（484 条对话、301 条带工具调用），按 `error` 归类统计——项目名解析失败 ~30 条、权限越权 ~14 条、单次 >24h 5 条，而 401 鉴权 / 空 error 有 ~490 条。**这一步直接决定优化谁**：高频且是模型错的（项目解析）排前面，低频的（超时长）往后放，环境问题（401）根本不进微调范围。
>
> **② 建 eval set 测 baseline（定量证明差距）**：构造 golden 用例（项目名、越权、日期歧义各 20~50 条），跑当前线上模型测准确率。如果项目名→ID 只有 60% 就坐实有优化空间；若已经 98% 就别浪费成本。**这套 eval set 同时是"问题证据"和"验收标准"**，微调后用同一套证明 60%→90%，形成闭环。
>
> **③ 错误归因（区分模型 vs 环境）**：同是 `success:false` 也要拆——"项目解析失败/越权"是模型决策错、微调能解决；"401/日期锁定"是后端/权限配置、微调无效。这也是为什么 DPO 造数据时**只有"客观错"才当 rejected**，归因错了会把噪声喂进训练。
>
> **④ 消融：微调是不是最优解？**：先试更便宜的手段。项目名当 ID 先试改 prompt（要求先调解析）或加规则（param_resolver 强制解析）；只有"prompt 调了、规则加了还是错"时，微调才是对的杠杆。

**Q2：那你验证下来，这个项目到底哪些值得微调？**
> 用 484 条真实日志跑完前两层，结论是**真正值得微调的只有"项目名解析"和"权限越权"两类**（高频 + 模型错 + 规则难以穷尽）；"超 24h"低频、可用校验规则兜；而占失败大头的 401/空 error 是环境噪声，微调一行都改不了。**主动说清"大部分问题微调解决不了"，恰恰是判断力的体现**——不盲目上微调。

**Q3：SFT 和 DPO 在这里各优化什么？**
> - **SFT 打底**：优化"选对工具 + 意图分流 + 参数格式合法（`project_id` 下划线，不是 DB 的 `projectId`）"这些基本盘，数据来自历史成功行。
> - **DPO 精修**：优化"避开特定坑"——项目名当 ID、越权该拒不拒、参数越界，用"对 vs 错"配对教，最对症。
> 两者共用一套数据采集，`prompt + chosen` 就是现成 SFT 数据（DPO 数据扔掉 rejected 即可），单向复用。

**Q4（陷阱）：你的数据从哪来？`conversation_logs` 真能支撑参数级微调吗？**
> 实测发现一个关键限制：**当前 `tools_called` 只存了 `{tool_name, success}`，没存工具参数和结果**（`langgraph_agent.py` 拼日志那几行只挑了工具名和成功标志）。所以：存量数据够训"工具选择"，但**训不了"参数填写"**——项目名→ID 这类参数级 DPO 现在造不出规模数据。**真正的第一步不是训练，是补 `tools_called` 埋点**（把 `arguments` + `result` 写进去），再攒几周新数据，或用 on-policy 自采样 + dry_run 校验临时造。这个卡点我是查了生产库才发现的，不是文档假设的。

### 项目的用户/角色有哪些？spring 后端是如何和AI Service 交互的？
普通员工、部门管理员、大区管理员、超级管理员
前端通过 nginx 访问 spring 后端，spring 后端透传JWT，从 JWT 中解析用户身份信息，AI service 根据信息
注入上下文、记忆，基于 Langgraph 分析用户请求

### 整个agent有哪些工具？graph中的节点和边是什么？

### Rag是怎么做的？为什么用这种方案？

### 长期记忆
- 写入
  每次 run 结束后，用户触发了 tool_execution ，且有实际回复 fastapi-service/app/services/langgraph_agent.py:1549
  基于正则规则识别后写入redis：
    patterns = [
      身份信息
      (r"我的?(user_?id|工号|员工号|账号)[是为：:]\s*(\S+)", 0.9),
      组织信息
      (r"我[在是](.{2,10}部门)", 0.7),
      (r"我负责(.{2,20}项目)", 0.7),
      用户偏好
      (r"我(一般|习惯|通常|喜欢|倾向)(.{4,30})", 0.6),
  ]
- 储存：redis：每个用户最多保存 50 条长期记忆。
- 注入：基于用户 query 检索
  BM25关键词相关度 × 时间衰减 × 记忆重要度。fastapi-service/app/services/user_memory.py:119
  注入 system prompt：使用 user_id 
  
### 短期记忆
- 写入
  每次 run 结束后：  
- 存储：redis：TTL 默认 30 分钟，默认最多保存 10 条消息，10 条消息约等于 5 轮对话，超出上限后删除最早的消息
- 注入：以真实历史对话轮次注入 message

### systemprompt
  system.yaml
    ↓ 填入用户身份、部门、当前日期
  base_system
    ↓
  PromptBuilder.build_messages_with_history()
    ├─ 检索长期记忆 → 拼进 system 消息
    ├─ 读取短期记忆 → 添加历史 user/assistant 消息
    └─ 添加当前 user 消息
    ↓
  conversation_history
    ↓
  写入 AgentState
    ↓
  传给 Function Calling / 普通 LLM
  最终构造出的消息类似：
  [
      {
          "role": "system",
          "content": """
          你是工时管理助手……
          当前用户：张三
          部门：研发部

          关于该用户的已知信息：
          - 用户习惯查看本周工时
          - 用户负责 A 项目
          """
      },
      {"role": "user", "content": "查询一下本周工时"},
      {"role": "assistant", "content": "你本周已填报 32 小时"},
      {"role": "user", "content": "那上周呢"}  # 当前消息
  ]

