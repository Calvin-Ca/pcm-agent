# 派单方案 ② 智能填报建议（简化版）

> 创建：2026-04-28
> 预估：1 天
> 简历价值：长期记忆 + 个性化推荐；user_memory 闭环；与 work_type_resolver 形成系列化能力

---

## 1. 目标

用户填报工时时，根据**历史记录**自动推荐：

| 字段 | 推荐策略 | 已有 | 待做 |
|------|---------|------|------|
| `workType`（工时类别） | (user, project) 二维众数 → user 一维 → LLM 兜底 | ✅ `work_type_resolver.py` | — |
| `projectId`（项目） | user 最近 30 天填报频次 Top3 → 当前用户参与中的项目 | ❌ | **本派单做** |
| `hours`（工时数） | (user, project) 历史中位数 → user 工作日中位数 → 默认 8 | ❌ | **本派单做** |

复用 `work_type_resolver.py` 的套路（TTL 缓存 + 多级降级 + httpx 调 SpringBoot），不引入新依赖。

---

## 2. 验收标准（必须全部达成）

- [ ] 新增 `app/services/project_resolver.py`：`async def resolve_project_suggestion(user_id, auth_token, base_url, top_k=3) -> List[Dict]`
- [ ] 新增 `app/services/hours_resolver.py`：`async def resolve_hours_suggestion(user_id, project_id, auth_token, base_url) -> float`
- [ ] 两个 resolver 都接入 TTL 缓存（cachetools，5 分钟，与 work_type_resolver 一致）
- [ ] **接入点**：`save_workhour.py` 在缺参追问时，把这两个 resolver 的结果通过 `clarify` 节点带给用户作为推荐项（不强制使用，用户可改）
- [ ] **新工具 OR 新接口**（二选一）：
  - **方案 A（推荐）**：新增 `suggest_workhour` 工具，独立返回填报建议给 LLM 决策；不修改 save_workhour 主流程
  - **方案 B**：在 save_workhour 的 prompt 里直接拼建议；改动小但耦合
  - **请 agent 在执行前确认选哪个方案**（默认 A）
- [ ] 单元测试 `tests/unit/test_project_resolver.py` + `tests/unit/test_hours_resolver.py`，覆盖：
  - 历史命中（直接返回众数/中位数）
  - 历史为空（降级到下一级）
  - SpringBoot 不可达（异常 → 返回默认值，不抛）
  - TTL 缓存命中
- [ ] 端到端测试：用 query "我要填工时" 跑通缺参追问流，能看到推荐项目和工时数

---

## 3. 数据契约

### `resolve_project_suggestion()` 返回

```python
[
    {"project_id": "P001", "project_name": "工时管理系统", "frequency": 12, "last_fill_date": "2026-04-27"},
    {"project_id": "P003", "project_name": "数据中台", "frequency": 5, "last_fill_date": "2026-04-20"},
    ...
]
# 长度 0~3，按 frequency desc 排
```

### `resolve_hours_suggestion()` 返回

```python
8.0  # float，默认 8.0
# 取 (user, project) 30 天内已填工时的中位数；该 pair 无数据时取 user 工作日中位数
```

### `suggest_workhour` 工具 schema（如选方案 A）

```python
{
    "name": "suggest_workhour",
    "description": "用户准备填报工时但未提供完整字段时，返回基于历史的项目和工时推荐。仅在用户表达填报意图但缺少 project_id 或 hours 时调用",
    "parameters": {
        "type": "object",
        "properties": {
            "fill_date": {"type": "string", "description": "ISO 8601 日期，默认今天"},
        },
    },
}
```

---

## 4. 实施步骤

### 4.1 `project_resolver.py`

复用 `work_type_resolver._fetch_history()` 拉 30 天数据，统计 `projectId` 频次：

```python
# app/services/project_resolver.py
from collections import Counter
from .work_type_resolver import _fetch_history  # 直接复用

async def resolve_project_suggestion(
    user_id: str, auth_token: str, base_url: str, top_k: int = 3
) -> List[Dict]:
    cache_key = f"proj:{user_id}"
    if cache_key in _project_cache:
        return _project_cache[cache_key]

    records = await _fetch_history(user_id, project_id=None, auth_token=auth_token, base_url=base_url)
    counter = Counter(r["projectId"] for r in records if r.get("projectId"))

    suggestions = [
        {
            "project_id": pid,
            "project_name": _resolve_name(pid, records),  # 从 records 里取 projectName
            "frequency": freq,
            "last_fill_date": _last_date(pid, records),
        }
        for pid, freq in counter.most_common(top_k)
    ]
    _project_cache[cache_key] = suggestions
    return suggestions
```

### 4.2 `hours_resolver.py`

```python
# app/services/hours_resolver.py
import statistics

async def resolve_hours_suggestion(
    user_id: str, project_id: Optional[str], auth_token: str, base_url: str
) -> float:
    cache_key = f"hrs:{user_id}:{project_id or 'global'}"
    if cache_key in _hours_cache:
        return _hours_cache[cache_key]

    records = await _fetch_history(user_id, project_id, auth_token, base_url)
    hours_list = [float(r["hours"]) for r in records if r.get("hours")]

    if hours_list:
        result = statistics.median(hours_list)
    else:
        # 降级：user 工作日中位数（再拉一次不带 project 的）
        all_records = await _fetch_history(user_id, None, auth_token, base_url)
        all_hours = [float(r["hours"]) for r in all_records if r.get("hours")]
        result = statistics.median(all_hours) if all_hours else 8.0

    _hours_cache[cache_key] = result
    return result
```

### 4.3 接入 `suggest_workhour` 工具（方案 A）

新增 `app/tools/suggest_workhour.py`：

```python
async def execute(params: dict, context: dict) -> dict:
    user_id = context["user_id"]
    auth_token = context.get("auth_token")
    base_url = settings.SPRINGBOOT_BASE_URL

    projects = await resolve_project_suggestion(user_id, auth_token, base_url)
    default_hours = await resolve_hours_suggestion(
        user_id, projects[0]["project_id"] if projects else None, auth_token, base_url
    )
    return {
        "suggested_projects": projects,
        "suggested_hours": default_hours,
        "tip": "以上是基于您最近 30 天填报历史的推荐，可直接使用或自行修改",
    }
```

注册到 `tool_registry`，无需改 `save_workhour.py` 主流程。

### 4.4 单元测试

mock httpx 返回不同形态的 records，覆盖：

- 30 天 12 条记录 → projects[0] 是众数项目
- 0 条记录 → 返回空 list
- httpx 异常 → 返回空 list（不抛）
- TTL 缓存命中（连续两次调用，第二次不发 httpx）

### 4.5 端到端验证

```bash
# 改完后必须重启 + e2e 验证（feedback_agent_commit_discipline 第 2 条）
ssh caic@172.19.3.136 "cd /home/caic/code/workhour/workhour_agent && docker compose up -d --force-recreate ai-service"

# 用真实有历史填报的账号测试
curl -X POST http://localhost:8000/api/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"我今天要填工时","user_context":{"user_id":"<有 30 天历史的真实用户>",...}}'

# 期望：LLM 触发 suggest_workhour 工具，返回 Top3 项目 + 默认工时
```

---

## 5. 工作域边界（**不许动**）

- ❌ 不许动 `docs/benchmarks/` 任何文件（基准测试归档区）
- ❌ 不许动 `docs/interview/` 任何文件（简历定稿区）
- ❌ 不许动 `fastapi-service/tests/benchmark/`（基准代码归档）
- ❌ 不许做 git mv
- ❌ 不许动 `app/services/work_type_resolver.py`（仅复用 `_fetch_history`，不修改）
- ❌ 不许动 ECharts 派单方案文件 `docs/changelog/plan-echarts-visualization.md`
- ❌ **不许同时跑这两个派单**（① 和 ② 串行执行，不要并行）

---

## 6. Commit 纪律（必读）

使用 conventional commits 格式，**名实相符**：

| 实际改动 | commit 前缀 |
|---------|------------|
| 新增 resolver / 新工具 | `feat(suggest): ...` |
| 修 resolver bug | `fix(suggest): ...` |
| 加单元测试 | `test(suggest): ...` |
| 改文档 | `docs: ...` |

**反例**：
- ❌ `fix(suggest): 加单元测试` → 应该是 `test(suggest):`
- ❌ `feat: 智能填报建议方案起草` → 起草不是 feat，是 `chore` 或 `docs`

期望 commit 数量：**3~5 个**。

---

## 7. 被迫修复条款

如果 e2e 验证发现 SpringBoot 接口字段名与 `springboot-api-reference.md` 不符（例如 `projectId` 实际是 `project_id`），允许：

- 改 `_fetch_history()` 的字段映射（这是修 work_type_resolver 的允许例外，因为 work_type_resolver 也用同样的字段）
- 重启 + e2e 验证
- commit 用 `fix(work_type_resolver): 字段名对齐 SpringBoot 实际返回`
- 汇报中标注 "为打通 suggest 链路被迫修了 work_type_resolver 字段名"

绝对禁止：隐瞒越界。

---

## 8. 完成标志

agent 提交最终汇报时必须包含：

1. 所有 commit 列表（`git log --oneline <分支起点>..HEAD`）
2. 每个 commit 的 stat 一行
3. 选择的方案（A 或 B）及理由
4. e2e 验证输出（必须能看到推荐项目和工时数）
5. 单元测试通过截图
6. 是否触发"被迫修复"，触发了改了什么

用户会用 `git show --stat <hash>` 验证每个 commit 名实相符。
