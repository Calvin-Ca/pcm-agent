# 第二阶段：PlannerAgent 激活 + 多步推理设计文档

> 编写日期：2026-04-03  
> 适用版本：当前 main 分支（Layer2 Tool Agent）  
> 目标：升级到 L3 DeepSearch Agent，解锁多工具串联、并行执行、跨工具结果聚合

---

## 一、现状诊断

### 已有但未激活的组件

| 组件 | 位置 | 状态 |
|------|------|------|
| `PlannerAgent` | `app/models/task_plan.py` 第 587 行 | ✅ 已实现，**从未被调用** |
| `TaskExecutor.execute_plan()` | `app/services/task_executor.py` 第 44 行 | ✅ 支持拓扑排序 + 并行执行，**仅单任务路径被用** |
| `TaskPlan` / `TaskNode` 模型 | `app/models/task_plan.py` | ✅ 完整，含循环依赖检测 |

### 当前 LangGraph 的两个缺陷

**缺陷 1：多 tool_calls 被丢弃**  
`node_llm_with_tools`（`langgraph_agent.py:131`）只取 `tool_calls[0]`，当 LLM 返回多个工具调用时，其余全部丢弃。

**缺陷 2：complex_request 未接入规划**  
`_route_by_intent`（`langgraph_agent.py:394`）将 `complex_request` 路由到 `execute_llm`（直接 LLM 回复），PlannerAgent 完全闲置。

---

## 二、目标架构

### 改造后的 LangGraph 节点拓扑

```
START
  └─ llm_with_tools ──(条件路由)──┬─ execute_tool      ─→ END   （单工具，同现在）
                                   ├─ execute_rag       ─→ END   （RAG，同现在）
                                   ├─ execute_llm       ─→ END   （通用对话，同现在）
                                   ├─ clarify_node      ─→ END   （追问，同现在）
                                   └─ plan_and_execute  ─→ summarize ─→ END  ← 新增
```

### 触发 `plan_and_execute` 的两条路径

```
路径 A：LLM 返回多个 tool_calls（≥2）
  node_llm_with_tools 检测到 len(tool_calls) >= 2
  → 直接将 tool_calls 转换为 TaskPlan（无需额外 LLM 调用）
  → 路由到 plan_and_execute

路径 B：complex_request 意图（降级路径）
  node_classify_intent（规则路由 fallback）识别为 complex_request
  → 路由到 plan_and_execute
  → plan_and_execute 内部调用 PlannerAgent 生成 TaskPlan（需 LLM 调用）
```

---

## 三、涉及改动清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `app/services/langgraph_agent.py` | 改 `AgentState`、改 `node_llm_with_tools`、新增 `node_plan_and_execute`、新增 `node_summarize`、改 `_route_by_intent`、改 `_build_graph`、改 `stream_agent_response` | 主要改动 |
| `app/models/task_plan.py` | 修复 `PlannerAgent._get_tools_info()` 的参数访问方式 | 小修 |
| `app/prompts/system.yaml` | 新增 multi-tool 触发引导 | 小改 |

---

## 四、详细实现

### 4.1 修改 `AgentState`（`langgraph_agent.py` 第 56 行）

在现有字段末尾追加 `task_plan`：

```python
class AgentState(TypedDict):
    # ... 现有字段保持不变 ...
    tool_result: Optional[Dict[str, Any]]
    rag_result: Optional[Dict[str, Any]]
    llm_result: Optional[str]
    error: Optional[str]
    # ── 新增：多步规划 ────────────────────────────────
    task_plan: Optional[Dict[str, Any]]          # 序列化的 TaskPlan（plan_and_execute 使用）
    plan_results: Optional[Dict[str, Any]]       # 各任务执行结果 {task_id: result}
```

---

### 4.2 修改 `node_llm_with_tools`（`langgraph_agent.py` 第 126-173 行）

**当前**：`tc = tool_calls[0]`，永远只取第一个。

**改为**：检测到 `≥2` 个 tool_calls 时，构建 TaskPlan 并路由到 `plan_and_execute`。

```python
    if result.get("finish_reason") == "tool_calls":
        tool_calls = result.get("tool_calls", [])
        if not tool_calls:
            return await node_classify_intent(state)

        user_ctx = state.get("user_context") or {}

        # ── 多工具调用：构建并行 TaskPlan ──────────────────────────────────
        if len(tool_calls) >= 2:
            import uuid
            tasks = []
            for i, tc in enumerate(tool_calls):
                t_name = tc["name"]
                t_params = dict(tc.get("arguments", {}))
                # knowledge_qa 在多步中不作为单独任务，跳过
                if t_name == "knowledge_qa":
                    continue
                # 注入身份
                if user_ctx.get("user_id") and "user_id" not in t_params and "member_name" not in t_params:
                    t_params["user_id"] = user_ctx["user_id"]
                if user_ctx.get("auth_token"):
                    t_params["auth_token"] = user_ctx["auth_token"]
                tasks.append({
                    "task_id": f"t{i+1}",
                    "task_type": "tool_call",
                    "tool_name": t_name,
                    "parameters": t_params,
                    "dependencies": [],  # 并行执行，无依赖
                })
            if tasks:
                return {
                    "intent": "complex_request",
                    "tool_name": None,
                    "tool_params": {},
                    "query": state["user_message"],
                    "task_plan": {
                        "plan_name": "多工具并行执行",
                        "tasks": tasks,
                        "source": "multi_tool_calls",  # 标记来源，plan_and_execute 跳过 PlannerAgent
                    },
                }

        # ── 单工具调用：原有逻辑不变 ───────────────────────────────────────
        tc = tool_calls[0]
        tool_name = tc["name"]
        tool_params = dict(tc.get("arguments", {}))

        if user_ctx.get("user_id") and "user_id" not in tool_params and "member_name" not in tool_params:
            tool_params["user_id"] = user_ctx["user_id"]
        if user_ctx.get("auth_token"):
            tool_params["auth_token"] = user_ctx["auth_token"]

        if tool_name == "knowledge_qa":
            return {
                "intent": "knowledge_qa",
                "tool_name": None,
                "tool_params": {},
                "query": tool_params.get("query", state["user_message"]),
            }

        if tool_name == "save_workhour":
            # ... 原有 clarify 逻辑不变 ...

        return {
            "intent": "tool_execution",
            "tool_name": tool_name,
            "tool_params": tool_params,
            "query": state["user_message"],
        }
```

---

### 4.3 新增 `node_plan_and_execute`（新增到 `langgraph_agent.py`）

这是核心新节点，在 `node_clarify` 函数之后插入：

```python
async def node_plan_and_execute(state: AgentState) -> dict:
    """
    节点：多步任务规划 + 并行执行

    两条入口：
    A. state["task_plan"]["source"] == "multi_tool_calls"
       → LLM 已返回多个 tool_calls，直接执行，跳过 PlannerAgent
    B. intent == "complex_request"（来自规则路由降级）
       → 调用 PlannerAgent 生成 TaskPlan，再执行
    """
    from app.models.task_plan import TaskPlan, TaskNode, TaskType, PlannerAgent
    from app.services.permission_validator import PermissionContext
    import uuid

    user_ctx = state.get("user_context") or {}
    permission_ctx = user_ctx.get("permission_context")

    raw_plan = state.get("task_plan")

    # ── 路径 A：multi_tool_calls，直接构建 TaskPlan ─────────────────────────
    if raw_plan and raw_plan.get("source") == "multi_tool_calls":
        task_plan = TaskPlan(
            name=raw_plan.get("plan_name", "多工具执行"),
            description="LLM 多工具调用自动规划",
            user_request=state["user_message"],
        )
        for t in raw_plan.get("tasks", []):
            node = TaskNode(
                task_id=t["task_id"],
                task_type=TaskType.TOOL_CALL,
                tool_name=t["tool_name"],
                parameters=t["parameters"],
                dependencies=t.get("dependencies", []),
            )
            task_plan.add_task(node)

    # ── 路径 B：complex_request，调用 PlannerAgent ──────────────────────────
    else:
        if not _llm_client or not _tool_registry:
            return {"llm_result": "抱歉，多步规划功能暂时不可用。", "error": "规划组件未初始化"}

        planner = PlannerAgent(
            tool_registry=_tool_registry,
            llm_client=_llm_client,
        )
        try:
            task_plan = await planner.plan_tasks(
                user_request=state["user_message"],
                user_context=user_ctx,
            )
        except Exception as e:
            logger.error(f"PlannerAgent 规划失败: {e}")
            return {"llm_result": "抱歉，任务规划失败，请尝试更简单的问题描述。", "error": str(e)}

    # ── 执行 TaskPlan ────────────────────────────────────────────────────────
    if not _task_executor:
        return {"llm_result": "任务执行器未初始化。", "error": "TaskExecutor 未初始化"}

    try:
        summary = await _task_executor.execute_plan(
            task_plan=task_plan,
            permission_context=permission_ctx,
            timeout=120,
        )
        plan_results = summary.get("task_results", {})
        return {
            "plan_results": plan_results,
            "task_plan": {"plan_name": task_plan.name, "status": str(task_plan.status)},
        }
    except Exception as e:
        logger.error(f"TaskPlan 执行失败: {e}", exc_info=True)
        return {"llm_result": f"任务执行失败: {e}", "error": str(e)}
```

---

### 4.4 新增 `node_summarize`（新增到 `langgraph_agent.py`）

```python
async def node_summarize(state: AgentState) -> dict:
    """
    节点：多步执行结果汇总

    将 plan_results 中的各工具执行结果交给 LLM 综合分析，
    生成面向用户的自然语言回答。
    """
    plan_results = state.get("plan_results") or {}
    user_message = state.get("user_message", "")

    if not plan_results:
        return {"llm_result": "所有任务均已完成，但未产生可汇总的结果。"}

    if not _llm_client:
        # 降级：直接拼接各工具结果
        parts = []
        for task_id, result in plan_results.items():
            r = result.get("result", result)
            if isinstance(r, dict) and r.get("success"):
                parts.append(str(r))
        return {"llm_result": "\n\n".join(parts) if parts else "任务已完成。"}

    # 构建汇总 prompt
    results_text = ""
    for task_id, result in plan_results.items():
        tool_name = result.get("tool_name", task_id)
        r = result.get("result", result)
        results_text += f"\n【{tool_name}】执行结果：\n{json.dumps(r, ensure_ascii=False, indent=2)}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "你是工时管理系统的智能助手。"
                "用户提出了一个需要多步操作的请求，系统已自动执行了多个工具并收集到结果。"
                "请将这些结果综合分析，用简洁、友好的语言回答用户的原始问题。"
                "如果有数据对比或排名，请用表格或列表呈现。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原始问题：{user_message}\n\n"
                f"各工具执行结果如下：{results_text}\n\n"
                "请根据以上结果，综合回答用户问题。"
            ),
        },
    ]

    try:
        answer = await _llm_client.generate(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
        )
        return {"llm_result": answer}
    except Exception as e:
        logger.error(f"汇总节点 LLM 调用失败: {e}")
        return {"llm_result": f"结果已收集，但汇总生成失败：{e}"}
```

---

### 4.5 修改 `_route_by_intent`（`langgraph_agent.py` 第 388 行）

```python
def _route_by_intent(state: AgentState) -> str:
    intent = state.get("intent", "general_chat")
    return {
        "knowledge_qa":     "execute_rag",
        "tool_execution":   "execute_tool",
        "complex_request":  "plan_and_execute",   # ← 从 execute_llm 改为 plan_and_execute
        "general_chat":     "execute_llm",
        "clarify":          "clarify_node",
    }.get(intent, "execute_llm")
```

---

### 4.6 修改 `_build_graph`（`langgraph_agent.py` 第 402 行）

```python
def _build_graph():
    builder = StateGraph(AgentState)

    # 注册节点（新增两个）
    builder.add_node("llm_with_tools",    node_llm_with_tools)
    builder.add_node("execute_tool",      node_execute_tool)
    builder.add_node("execute_rag",       node_execute_rag)
    builder.add_node("execute_llm",       node_execute_llm)
    builder.add_node("clarify_node",      node_clarify)
    builder.add_node("plan_and_execute",  node_plan_and_execute)   # ← 新增
    builder.add_node("summarize",         node_summarize)           # ← 新增

    builder.add_edge(START, "llm_with_tools")

    builder.add_conditional_edges(
        "llm_with_tools",
        _route_by_intent,
        {
            "execute_tool":     "execute_tool",
            "execute_rag":      "execute_rag",
            "execute_llm":      "execute_llm",
            "clarify_node":     "clarify_node",
            "plan_and_execute": "plan_and_execute",  # ← 新增
        },
    )

    builder.add_edge("execute_tool",     END)
    builder.add_edge("execute_rag",      END)
    builder.add_edge("execute_llm",      END)
    builder.add_edge("clarify_node",     END)
    builder.add_edge("plan_and_execute", "summarize")   # ← 新增：执行后汇总
    builder.add_edge("summarize",        END)            # ← 新增

    return builder.compile()
```

---

### 4.7 修改 `stream_agent_response` 中的结果提取逻辑

当前结果提取代码（约第 560 行附近）只处理 `tool_result / rag_result / llm_result`。
需要在提取 `llm_result` 之前，确保 `summarize` 节点的结果也能被正确读取。

由于 `node_summarize` 最终写入的是 `llm_result`，**不需要改结果提取逻辑**，只需确认 SSE 的 `tool_call` 事件中补充进度信息（见 4.8）。

---

### 4.8 SSE 进度事件（在 `node_plan_and_execute` 内输出）

> **注意**：LangGraph 节点是同步执行的，无法直接 yield SSE。进度事件需要通过 LangGraph 的 `astream_events` 机制或在 TaskExecutor 中注入回调。

**简化方案（推荐）**：`plan_and_execute` 节点执行完成后，在 `stream_agent_response` 中检测到 `plan_results` 时，在 `response` 事件之前额外发送一个 `tool_call` 事件汇报已执行的工具：

在 `stream_agent_response` 中，读取最终 state 后追加：

```python
# 多步执行：发送工具调用摘要事件
plan_results = final_state.get("plan_results")
if plan_results:
    for task_id, res in plan_results.items():
        tool_name = res.get("tool_name", task_id)
        success = res.get("success", True)
        yield _format_sse("tool_call", {
            "tool": tool_name,
            "status": "success" if success else "error",
            "task_id": task_id,
        })
```

---

### 4.9 修复 `PlannerAgent._get_tools_info()`（`task_plan.py` 第 665 行）

当前代码 `tool_def.parameters` 和 `tool_def.required_params` 可能与 `ToolRegistry` 实际字段不匹配。

检查 `tool_registry.py` 中的 `ToolDefinition` 字段名，改为正确访问方式：

```python
# 将
"parameters": tool_def.parameters,
"required": tool_def.required_params

# 改为（假设 ToolDefinition 用的是 json_schema）
"parameters": tool_def.json_schema.get("properties", {}),
"required": tool_def.json_schema.get("required", []),
```

> 执行前先确认 `tool_registry.py` 中 `ToolDefinition` 的实际字段名。

---

## 五、System Prompt 更新（`app/prompts/system.yaml`）

在现有 system prompt 中追加以下引导，让 LLM 在复杂请求时主动返回多个 tool_calls：

```yaml
# 在 system prompt 末尾追加：
multi_tool_guidance: |
  当用户的请求需要查询多项数据或执行多个独立操作时（如"查张三和李四的工时"、
  "查本月各项目工时"），请在一次响应中同时调用多个工具（返回多个 tool_calls），
  系统会自动并行执行并汇总结果。无需逐一询问，直接同时调用所有需要的工具。
```

---

## 六、执行顺序

```
Step 1  修改 AgentState，加 task_plan / plan_results 字段
Step 2  修改 node_llm_with_tools，加多 tool_calls 检测逻辑
Step 3  新增 node_plan_and_execute 函数
Step 4  新增 node_summarize 函数
Step 5  修改 _route_by_intent（complex_request → plan_and_execute）
Step 6  修改 _build_graph（注册新节点和边）
Step 7  修改 stream_agent_response，追加 plan_results 的 tool_call SSE 事件
Step 8  修复 PlannerAgent._get_tools_info() 字段访问
Step 9  更新 system.yaml，追加 multi_tool_guidance
Step 10 验证（见第七节）
```

---

## 七、验证方案

### 7.1 单元验证（无需运行服务）

```python
# 验证 TaskPlan 构建和执行
import asyncio
from app.models.task_plan import TaskPlan, TaskNode, TaskType

plan = TaskPlan(name="test", user_request="test")
plan.add_task(TaskNode(task_id="t1", task_type=TaskType.TOOL_CALL, tool_name="query_timesheet", parameters={}))
plan.add_task(TaskNode(task_id="t2", task_type=TaskType.TOOL_CALL, tool_name="query_project", parameters={}))
levels = plan.topological_sort()
print(f"执行层级: {len(levels)}, 第一层任务数: {len(levels[0])}")
# 期望：1 层，2 个并行任务（无依赖）
```

### 7.2 集成验证（服务运行中）

```bash
# 测试用例 1：多工具并行（张三和李四工时查询）
# 期望：LLM 返回 2 个 tool_calls → plan_and_execute → summarize → 对比回答

# 测试用例 2：单工具不受影响
# 期望：仍走 execute_tool → END，不触发 plan_and_execute

# 测试用例 3：complex_request 降级（如意图路由走了规则匹配）
# 期望：plan_and_execute → PlannerAgent 生成计划 → 执行 → summarize
```

---

## 八、边界情况与注意事项

### 8.1 TaskExecutor 权限传递

`node_plan_and_execute` 调用 `_task_executor.execute_plan()` 时必须传入 `permission_context`：

```python
permission_ctx = user_ctx.get("permission_context")
summary = await _task_executor.execute_plan(
    task_plan=task_plan,
    permission_context=permission_ctx,  # ← 不能漏
    timeout=120,
)
```

### 8.2 auth_token 注入

多工具调用时，每个 task 的 `parameters` 里都需要注入 `auth_token`（工具调用 SpringBoot API 时需要）。已在 4.2 节的 multi-tool 构建逻辑中处理。

### 8.3 PlannerAgent 生成的计划工具名校验

路径 B（PlannerAgent 生成计划）可能产生不存在的工具名。TaskExecutor 在 `_execute_tool_call` 中已有校验：

```python
tool_def = self.tool_registry.get_tool(task.tool_name)
if not tool_def:
    raise ValueError(f"工具不存在: {task.tool_name}")
```

会抛出异常并标记该任务 FAILED，不影响其他任务。

### 8.4 并行任务的 auth_token

TaskExecutor 的并行执行用 `asyncio.gather`，每个任务独立调用 handler，auth_token 在各自 parameters 中，不存在竞争问题。

### 8.5 总超时

`execute_plan` 的 `timeout=120` 是**单任务**超时，整体计划无独立超时。若并行任务数多，总时间可控。建议保持 120s。

---

## 九、典型场景示例

### 场景 1："查张三和李四本月工时并对比"

```
用户输入 → node_llm_with_tools
  LLM 返回 tool_calls: [
    query_timesheet(member_name="张三", start_date="...", end_date="..."),
    query_timesheet(member_name="李四", start_date="...", end_date="..."),
  ]
  → 检测到 len=2 → 构建 TaskPlan（2个并行任务）
  → plan_and_execute: asyncio.gather(task_t1, task_t2)
  → summarize: LLM 对比两人工时 → 回答用户
```

### 场景 2："查本月各项目工时，找出工时最多的前3个"

```
用户输入 → node_llm_with_tools
  LLM 可能返回：
    tool_calls: [query_timesheet(...), compute_statistics(...)]
  或 finish_reason=stop（LLM 觉得这是 complex_request）
  → 若 multi tool_calls → plan_and_execute（并行查询+统计）
  → 若 stop → general_chat（LLM 自己组织语言）
  
  * 这个场景 System Prompt 的 multi_tool_guidance 很关键，
    引导 LLM 同时调用 query_timesheet + compute_statistics
```

### 场景 3："帮我生成周报，并查一下上周工时和本周差异"

```
用户输入 → node_llm_with_tools
  LLM 返回 tool_calls: [
    query_timesheet(start_date=上周一, end_date=上周日),
    query_timesheet(start_date=本周一, end_date=今天),
    generate_weekly_report(...),
  ]
  → 3个并行任务 → plan_and_execute
  → summarize: 生成周报 + 对比差异
```
