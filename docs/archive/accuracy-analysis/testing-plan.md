# AI 助手测试方案

> 目标：量化验证 Function Calling 架构的分类精度和整体流程精度，建立可回归的测试基线。
> 创建日期：2026-04-01

---

## 一、规模目标

| 层级 | 用例数量 | 说明 |
|------|----------|------|
| Layer 1（意图分类） | **2000 条** | 核心指标，需要统计显著性 |
| Layer 2（参数提取） | **1000 条** | 取 Layer 1 中 tool_execution 子集 |
| Layer 3（端到端）  | **200 条** | 每类场景抽样，成本较高 |

**为什么需要这个量级：**

- 每个 sub_type 50 条起：才能算出该子场景的精度，误差 ±14%（95% 置信区间）
- 每个 sub_type 100 条：误差缩小到 ±10%
- 总体 2000 条：整体精度误差 ±2.2%，足以检测 Prompt 迭代带来的 1-2% 变化
- 少于 100 条总量：精度数字毫无意义，"90%" 可能只是 9/10

---

## 二、测试架构

```
用户输入
  │
  ▼
[Layer 1] 意图分类精度测试          ← 2000 条，约 30 分钟
  │  node_llm_with_tools → 验证 intent + tool_name
  │  不依赖 SpringBoot，纯 LLM 行为测试
  │
  ▼
[Layer 2] 参数提取精度测试          ← 1000 条，约 20 分钟
  │  在 Layer 1 基础上验证 tool_params 是否正确
  │  日期解析（"上个月" → 具体日期区间）是重点
  │
  ▼
[Layer 3] 端到端流程测试            ← 200 条，约 10 分钟（mock SpringBoot）
     完整链路：LLM → 参数解析 → 权限校验 → 工具执行 → 结果格式化
```

每层可独立运行，Layer 1 最轻量（秒级响应），Layer 3 需要 mock SpringBoot API。

---

## 三、测试用例分类与数量

### 总体分布（2000 条 Layer 1）

| category | 总条数 | 占比 | 说明 |
|----------|--------|------|------|
| query_timesheet | 700 | 35% | 最核心场景，重点覆盖 |
| save_workhour | 500 | 25% | 缺参/完整参数各占一半 |
| query_project | 200 | 10% | |
| knowledge_qa | 200 | 10% | 防止把知识问答误判为工具调用 |
| general_chat | 200 | 10% | 防止把闲聊误判为工具调用 |
| edge_cases | 200 | 10% | 模糊表达、混合意图 |

---

### 3.1 工时查询（query_timesheet）— 700 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `query_self_today` | 50 | "今天我填了多少"、"今天工时" |
| `query_self_this_week` | 80 | "这周工时"、"本周填了几小时" |
| `query_self_last_week` | 60 | "上周工时"、"上周填了多少" |
| `query_self_this_month` | 80 | "本月工时"、"这个月的统计" |
| `query_self_last_month` | 80 | "上个月工时"、"上月总计" |
| `query_self_date_range` | 60 | "3月15到31号"、"从上周一到今天" |
| `query_self_by_project` | 50 | "AI平台项目这个月的工时" |
| `query_others_this_week` | 50 | "李明这周工时"、"看看王芳本周" |
| `query_others_this_month` | 50 | "张总本月填了多少"、"查一下何思思本月" |
| `query_others_last_month` | 60 | "何思思上个月的工时"、"李明上月怎么样" |
| `query_others_date_range` | 40 | "查王芳3月份的工时" |

**重点难点**：
- `query_self_*` vs `query_others_*`：必须正确区分"查自己"还是"查别人"
- 模糊指代："帮我查" / "查我的" / "看看我" 均应传 `user_id`，不传 `member_name`
- 他人查询："查何思思" / "看看李明" 应传 `member_name`，不传 `user_id`

---

### 3.2 工时填报（save_workhour）— 500 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `fill_all_params` | 150 | 三参数齐全：项目+日期+时长 |
| `fill_with_description` | 100 | 四参数：项目+日期+时长+描述 |
| `fill_missing_project` | 70 | 只有日期+时长，缺项目 → 应追问 |
| `fill_missing_duration` | 70 | 只有项目+日期，缺时长 → 应追问 |
| `fill_missing_date` | 60 | 只有项目+时长，缺日期 → 应追问 |
| `fill_missing_two` | 30 | 只有一个参数 → 应追问两个 |
| `fill_project_by_name` | 20 | 项目名用全称/简称（测 param_resolver）|

**重点难点**：
- 缺参时必须走 `clarify` 意图，不能猜默认值
- 参数完整时必须走 `tool_execution`，不能追问
- 描述字段（description）是可选的，有没有都应触发工具调用

---

### 3.3 项目查询（query_project）— 200 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `query_by_name` | 80 | "查一下AI平台项目"、"AI平台是什么项目" |
| `query_list_fillable` | 60 | "有哪些项目可以填报"、"我能填哪些项目" |
| `query_project_info` | 60 | "智慧园区项目的负责人是谁"、"项目状态" |

---

### 3.4 知识问答（knowledge_qa）— 200 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `policy_deadline` | 60 | "工时截止日期是什么时候"、"几号前要填完" |
| `policy_rules` | 80 | "加班工时怎么算"、"请假期间要填工时吗" |
| `system_usage` | 60 | "怎么填工时"、"怎么修改已填的工时" |

**重点**：这类问题绝对不能触发工具调用，是"负样本"中最重要的一类。

---

### 3.5 闲聊（general_chat）— 200 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `greeting` | 60 | "你好"、"早上好"、"在吗" |
| `acknowledgement` | 60 | "好的"、"谢谢"、"明白了"、"收到" |
| `off_topic` | 80 | "今天天气怎么样"、"帮我写段代码"、"推荐个餐厅" |

---

### 3.6 边缘用例（edge_cases）— 200 条

| sub_type | 条数 | 场景说明 |
|----------|------|----------|
| `ambiguous_query_timesheet` | 50 | "帮我看一下工时"（没有时间范围，缺参但意图明确）|
| `implicit_self` | 40 | 纯隐式自我指代："查一下"、"看看我的"、"统计下" |
| `mixed_intent` | 30 | "查完工时再帮我填一下今天的"（多意图，取第一个）|
| `informal_date` | 40 | "前天"、"大前天"、"上上周"、"这个季度" |
| `name_with_title` | 20 | "张总"、"李经理"、"何工"（带职称的姓名）|
| `short_input` | 20 | "工时"、"填报"、"查一下"（极短输入）|

---

## 四、测试数据 Schema

所有测试用例使用统一 JSON 格式：

```json
{
  "id": "qt_001",
  "category": "query_timesheet",
  "sub_type": "query_others_last_month",
  "description": "查询他人上个月工时",
  "input": "看一下何思思上个月的工时",
  "user_context": {
    "entity_type": "deptAdmin",
    "user_id": "1001",
    "user_name": "张三",
    "department_id": "dept_01"
  },
  "expected": {
    "intent": "tool_execution",
    "tool_name": "query_timesheet",
    "params": {
      "member_name": "何思思",
      "start_date": "2026-03-01",
      "end_date": "2026-03-31"
    },
    "params_fuzzy": [],
    "params_exists": ["member_name"],
    "date_relative": true
  },
  "notes": "上个月需要相对日期解析，执行测试时动态计算预期值"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `category` | 主场景类别，用于分类统计精度 |
| `sub_type` | 细分场景，用于定位问题模式 |
| `input` | 用户的中文输入（口语化，多样化） |
| `user_context.entity_type` | 影响权限校验：`employee` / `deptAdmin` / `companyAdmin` |
| `expected.intent` | 期望意图：`tool_execution` / `knowledge_qa` / `general_chat` / `clarify` |
| `expected.tool_name` | 期望工具（`null` 表示不调工具） |
| `expected.params` | 精确匹配的参数值 |
| `expected.params_fuzzy` | 只检查非空，不校验具体值的参数列表 |
| `expected.params_exists` | 必须存在的参数列表 |
| `expected.date_relative` | `true` 表示日期参数需在运行时动态计算 |

---

## 五、测试数据生成策略

### 5.1 生成方式

测试数据全部由 LLM 批量生成，**不手写**。按以下两步操作：

**Step 1：分批生成**

每次指定一个 `sub_type`，生成 50-100 条，要求 LLM 保证多样性：

```
按照 [sub_type 名称] 场景，生成 100 条测试用例。
要求：
1. 每条 input 表达方式必须不同，覆盖：
   - 长句 vs 短句（"帮我查一下上个月的工时统计" vs "上月工时"）
   - 正式 vs 口语（"请查询" vs "看看"、"瞅一眼"）
   - 不同人名（从以下列表随机选：何思思/李明/王芳/张总/刘工/陈经理/赵丽/孙涛）
   - 不同日期表达（"上个月"/"3月份"/"上月"/"3月1号到31号"）
2. user_context.entity_type 分布：70% employee，20% deptAdmin，10% companyAdmin
3. 严格按 JSON schema 输出，每条用 --- 分隔
```

**Step 2：质量过滤**

生成后运行自动检查脚本，过滤以下问题：
- `input` 重复或相似度 > 80%（用简单 Jaccard 相似度检测）
- `expected.intent` 为空或不在合法值列表
- `params` 中 `start_date` > `end_date`
- `date_relative=true` 但 `params` 中没有日期字段

### 5.2 生成 Prompt 模板

将以下 prompt 交给另一个模型，替换 `[SUB_TYPE]` 和 `[COUNT]` 后执行：

````
你是测试数据生成器。为一个企业工时管理 AI 助手生成测试用例。

## 助手能力
- query_timesheet：查询工时记录，参数：member_name（指定他人时用）、user_id（查自己时用）、start_date（YYYY-MM-DD）、end_date（YYYY-MM-DD）、project_id（可选）
- save_workhour：填报工时，必填参数：project_id、date（YYYY-MM-DD）、duration（0.5步长的小时数）；可选：description
- query_project：查询项目信息，参数：project_name 或 project_id
- knowledge_qa：制度/规则/政策类问题，不调工具
- general_chat：闲聊/问候，不调工具
- clarify：填报工时缺少必填参数时追问

## 当前日期（生成 params 时使用）
今天：2026-04-01
本周：2026-03-30 至 2026-04-05
上周：2026-03-23 至 2026-03-29
本月：2026-04-01 至 2026-04-30
上个月：2026-03-01 至 2026-03-31
本季度：2026-01-01 至 2026-03-31

## 可用人名
何思思、李明、王芳、张总、刘工、陈经理、赵丽、孙涛、周建国、吴晓燕

## 可用项目名
AI平台、智慧园区、数字化转型、ERP升级、移动端改版、云迁移项目

## 任务
按照 [SUB_TYPE] 场景，生成 [COUNT] 条测试用例。

表达方式覆盖要求（每批次必须包含）：
- 长句（10字以上）和短句（5字以内）各占 20%
- 口语化表达（"瞅一眼"、"看看"、"查下"）占 40%
- 含人名的用例中，人名分布均匀，不要集中用同一个人
- 日期表达多样：口语（上个月/本周/昨天）和具体日期（3月1号/2026-03-01）各占一半

输出格式：每条用例严格按以下 JSON 输出，用 --- 分隔，不要输出其他内容：

{
  "id": "[SUB_TYPE缩写]_[序号，三位数]",
  "category": "[对应category]",
  "sub_type": "[SUB_TYPE]",
  "description": "[一句话说明]",
  "input": "[用户中文输入]",
  "user_context": {
    "entity_type": "[employee|deptAdmin|companyAdmin]",
    "user_id": "1001",
    "user_name": "张三",
    "department_id": "dept_01"
  },
  "expected": {
    "intent": "[tool_execution|knowledge_qa|general_chat|clarify]",
    "tool_name": "[query_timesheet|save_workhour|query_project|null]",
    "params": {},
    "params_exists": [],
    "date_relative": false
  },
  "notes": ""
}
````

### 5.3 推荐生成顺序

优先生成核心场景，再补边缘用例：

```
批次 1：query_timesheet（7个sub_type × 100条）= 700条
批次 2：save_workhour（7个sub_type × 70条）= 500条
批次 3：knowledge_qa + general_chat（各200条）= 400条
批次 4：query_project + edge_cases（各200条）= 400条
```

---

## 六、测试框架实现

### 6.1 目录结构

```
fastapi-service/tests/
├── data/
│   ├── query_timesheet/
│   │   ├── query_self_today.json         # 50条
│   │   ├── query_self_this_week.json     # 80条
│   │   ├── query_others_last_month.json  # 60条
│   │   └── ...（按 sub_type 拆文件）
│   ├── save_workhour/
│   ├── knowledge_qa/
│   ├── general_chat/
│   └── edge_cases/
├── test_classification_accuracy.py       # Layer 1：意图分类
├── test_param_extraction.py              # Layer 2：参数提取
├── test_e2e_pipeline.py                  # Layer 3：端到端
└── utils/
    ├── test_data_loader.py               # 递归加载 data/ 下所有 JSON
    ├── date_resolver.py                  # 相对日期动态计算
    ├── similarity_check.py               # 生成数据去重检查
    └── accuracy_reporter.py             # 精度统计报告
```

### 6.2 Layer 1：意图分类测试

```python
# tests/test_classification_accuracy.py
import pytest
import json
from pathlib import Path
from app.services.langgraph_agent import node_llm_with_tools, AgentState

def load_all_cases():
    """递归加载 data/ 目录下所有 JSON 文件中的测试用例"""
    data_dir = Path(__file__).parent / "data"
    cases = []
    for f in sorted(data_dir.rglob("*.json")):
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
            cases.extend(data if isinstance(data, list) else [data])
    return cases

def build_state(test_case: dict) -> AgentState:
    ctx = test_case["user_context"]
    return AgentState(
        user_message=test_case["input"],
        user_context={
            "user_id": ctx.get("user_id", "1001"),
            "user_name": ctx.get("user_name", "测试用户"),
            "entity_type": ctx.get("entity_type", "employee"),
            "department_id": ctx.get("department_id", "dept_01"),
            "auth_token": "Bearer test-token",
        },
        session_id="test-session",
        conversation_history=[
            {"role": "user", "content": test_case["input"]}
        ],
        intent=None, tool_name=None, tool_params={}, query="",
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("case", load_all_cases(), ids=lambda c: c["id"])
async def test_intent_classification(case):
    state = build_state(case)
    result = await node_llm_with_tools(state)

    expected = case["expected"]
    assert result["intent"] == expected["intent"], (
        f"[{case['id']}] 意图分类错误\n"
        f"  输入: {case['input']}\n"
        f"  期望: {expected['intent']} / 实际: {result['intent']}"
    )
    if expected.get("tool_name"):
        assert result.get("tool_name") == expected["tool_name"], (
            f"[{case['id']}] 工具选择错误\n"
            f"  期望: {expected['tool_name']} / 实际: {result.get('tool_name')}"
        )
```

### 6.3 Layer 2：参数提取测试

```python
# tests/test_param_extraction.py
from tests.utils.date_resolver import resolve_relative_dates

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [c for c in load_all_cases() if c["expected"]["intent"] == "tool_execution"],
    ids=lambda c: c["id"]
)
async def test_param_extraction(case):
    state = build_state(case)
    result = await node_llm_with_tools(state)

    # 意图分类错误时跳过，避免掩盖 Layer 1 问题
    if result["intent"] != "tool_execution":
        pytest.skip(f"意图分类错误（由 Layer 1 负责报告），跳过参数验证")

    params = result.get("tool_params", {})
    expected = case["expected"]

    # 动态解析相对日期
    expected_params = expected.get("params", {}).copy()
    if expected.get("date_relative"):
        expected_params = resolve_relative_dates(expected_params)

    # 精确匹配（不在 params_fuzzy 中的参数）
    fuzzy_keys = set(expected.get("params_fuzzy", []))
    exists_keys = set(expected.get("params_exists", []))

    for key, val in expected_params.items():
        if key in fuzzy_keys:
            continue
        assert params.get(key) == val, (
            f"[{case['id']}] 参数 {key!r} 值错误\n"
            f"  输入: {case['input']}\n"
            f"  期望: {val!r} / 实际: {params.get(key)!r}"
        )

    # 存在性检查
    for key in exists_keys:
        assert params.get(key), (
            f"[{case['id']}] 参数 {key!r} 缺失或为空\n"
            f"  输入: {case['input']}"
        )
```

### 6.4 Layer 3：端到端测试

```python
# tests/test_e2e_pipeline.py
# 使用 httpx mock，拦截 SpringBoot API 调用，无需真实服务
from unittest.mock import AsyncMock, patch

MOCK_RESPONSES = {
    "workhour": [
        {"id": "1", "memberId": "1001", "projectId": "p001",
         "projectName": "AI平台", "workhourDate": "2026-03-01T00:00:00Z",
         "workhour": 8.0, "description": "需求分析"}
    ],
    "project": [
        {"id": "p001", "projectName": "AI平台", "managerId": "1001"}
    ],
}

@pytest.mark.asyncio
@pytest.mark.parametrize("case", e2e_sample_cases(), ids=lambda c: c["id"])
async def test_e2e_pipeline(case):
    with patch("httpx.AsyncClient.get") as mock_get, \
         patch("httpx.AsyncClient.post") as mock_post:

        mock_get.return_value = AsyncMock(
            status_code=200,
            json=lambda: MOCK_RESPONSES["workhour"],
            raise_for_status=lambda: None,
        )
        mock_post.return_value = AsyncMock(
            status_code=201,
            json=lambda: {"id": "new_001", "projectId": "p001"},
            raise_for_status=lambda: None,
        )

        response_chunks = []
        async for chunk in stream_agent_response(
            user_message=case["input"],
            user_context=build_user_context(case),
            session_id="test-e2e-session",
        ):
            response_chunks.append(chunk)

    final_response = "".join(response_chunks)

    # 通用断言：不输出原始 JSON
    import json as json_lib
    try:
        json_lib.loads(final_response)
        pytest.fail(f"[{case['id']}] 响应是原始 JSON，应转换为自然语言")
    except ValueError:
        pass  # 正常：不是合法 JSON

    # 按用例断言关键词
    for keyword in case.get("expected_response_contains", []):
        assert keyword in final_response, (
            f"[{case['id']}] 响应缺少关键词 {keyword!r}\n响应内容: {final_response[:200]}"
        )
```

### 6.5 精度统计报告

```python
# tests/utils/accuracy_reporter.py
# 运行：pytest tests/test_classification_accuracy.py --json-report \
#        --json-report-file=report.json && python tests/utils/accuracy_reporter.py

def generate_report(report_path: str):
    """
    示例输出：
    ┌──────────────────────────────────────┬──────┬──────┬─────────┐
    │ 类别/子类别                           │ 总数 │ 通过 │  精度   │
    ├──────────────────────────────────────┼──────┼──────┼─────────┤
    │ query_timesheet                      │  700 │  651 │  93.0%  │
    │   ├─ query_self_this_week            │   80 │   78 │  97.5%  │
    │   ├─ query_others_last_month         │   60 │   47 │  78.3%  │ ⚠
    │   └─ query_fuzzy_date                │   40 │   28 │  70.0%  │ ⚠
    │ save_workhour                        │  500 │  480 │  96.0%  │
    │   ├─ fill_all_params                 │  150 │  148 │  98.7%  │
    │   └─ fill_missing_project            │   70 │   63 │  90.0%  │
    │ knowledge_qa                         │  200 │  198 │  99.0%  │
    │ general_chat                         │  200 │  197 │  98.5%  │
    │ edge_cases                           │  200 │  162 │  81.0%  │ ⚠
    ├──────────────────────────────────────┼──────┼──────┼─────────┤
    │ 总体                                 │ 2000 │ 1870 │  93.5%  │
    └──────────────────────────────────────┴──────┴──────┴─────────┘
    ⚠ = 低于目标精度（见精度目标表）

    失败模式分析（Top 5）：
    1. query_others_last_month → general_chat  (13次) ← 最需关注
    2. query_fuzzy_date → tool_execution (参数缺失) (12次)
    3. ambiguous_query_timesheet → general_chat (9次)
    """
```

---

## 七、运行方式

```bash
cd fastapi-service

# 数据质量检查（生成数据后先跑）
python tests/utils/similarity_check.py tests/data/

# Layer 1：意图分类（约 30 分钟，2000条 × LLM调用）
pytest tests/test_classification_accuracy.py -v --tb=short \
  --json-report --json-report-file=reports/layer1_report.json

# Layer 2：参数提取（仅跑 tool_execution 用例）
pytest tests/test_param_extraction.py -v --tb=short \
  --json-report --json-report-file=reports/layer2_report.json

# 生成精度报告
python tests/utils/accuracy_reporter.py reports/layer1_report.json

# Layer 3：端到端（抽样 200 条）
pytest tests/test_e2e_pipeline.py -v --tb=long

# 快速冒烟（每个 sub_type 各取 5 条，约 2 分钟）
pytest tests/test_classification_accuracy.py -k "smoke" --tb=short
```

---

## 八、精度目标

| 场景 | 目标 | 当前基线（待建立） |
|------|------|-------------------|
| 整体意图分类精度 | ≥ 92% | - |
| 核心工时场景（query + save） | ≥ 95% | - |
| 知识问答/闲聊（不误触发工具） | ≥ 98% | - |
| 缺参识别（clarify 意图） | ≥ 95% | - |
| 参数提取（日期精确值） | ≥ 80% | - |
| 参数提取（人名/项目名存在性） | ≥ 90% | - |
| 端到端响应（自然语言、含关键信息）| ≥ 88% | - |

---

## 九、迭代流程

```
Step 1：生成测试数据（另一个 LLM，按 5.3 顺序分批）
    ↓
Step 2：运行 similarity_check.py 过滤重复数据
    ↓
Step 3：运行 Layer 1，建立精度基线
    ↓
Step 4：分析失败模式（accuracy_reporter.py 的 Top 失败列表）
    ↓
Step 5：修改 system.yaml Prompt 或工具 JSON Schema
    ↓
Step 6：重新运行 Layer 1，对比精度变化
    ↓  精度达标（见目标表）
Step 7：运行 Layer 2，检查参数提取
    ↓
Step 8：运行 Layer 3，端到端验证
    ↓
Step 9：纳入 CI，每次 Prompt 修改自动触发 Layer 1 回归
```
