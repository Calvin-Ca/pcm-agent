# 第二阶段剩余任务：执行计划

> 编写日期：2026-04-07  
> 执行优先级：Task 1 → Task 2 → Task 3（按序执行，每个独立）  
> 当前精度基线：Layer 1 v5 82.6%，Layer 2 v4 86.9%（有效精度 99.7%）

---

## 概览

| # | 任务 | 预估 | 难度 | 涉及文件 |
|---|------|------|------|---------|
| T1 | knowledge_qa few-shot — 修复含人名政策问题误判 | 1h | 极低 | 1 |
| T2 | export_report Tool — 工时报表导出 | 2-3h | 低 | 3 |
| T3 | Layer 3 集成测试 — 全链路验证 | 半天 | 中 | 1（新建） |

> **不做 SQL Agent**：当前无真实用户需求驱动，且需要只读 DB 账号配置（运维依赖）。等生产环境反馈后再评估。

---

## Task 1：knowledge_qa few-shot（含人名政策问题）

### 背景

Layer 1 v5 中 knowledge_qa 类精度 63%，剩余 74 条失败中 70 条的根因是：

> 查询中**含有人名**时（如"周建国，请问工时截止还剩几天"），LLM 认为这是"在向某人提问或转述"，倾向于直接用文字回答（general_chat），而不调用 `knowledge_qa` 工具。

预计修复后 +2~3%，整体精度达 84~85%。

### 改动文件

`fastapi-service/app/prompts/system.yaml`

### 改动内容

在 system.yaml 的 `template` 末尾，`## 多工具调用引导` 段落之前，插入以下内容：

```yaml
  ## knowledge_qa 工具调用说明

  当用户询问工时制度、政策、规则、流程时，必须调用 knowledge_qa 工具，即使问题中包含人名：
  - "周建国，请问工时截止还剩几天" → 调用 knowledge_qa（询问截止日期规则）
  - "李明本周工时截止几号" → 调用 knowledge_qa（询问截止日期）
  - "陈经工时截止日期到了吗" → 调用 knowledge_qa（询问截止规则）
  - "张三问我加班算不算工时" → 调用 knowledge_qa（询问加班政策）
  - "帮我查一下请假期间要填工时吗" → 调用 knowledge_qa（询问请假政策）

  判断标准：问题的**核心是在询问规则/制度/政策**，人名只是上下文，不改变意图。
```

### 插入位置

在现有 system.yaml 的这一行之前插入：
```
  ## 多工具调用引导
```

### 验证方法

```bash
cd fastapi-service

# 冒烟测试（验证 3 条典型失败用例是否修复）
# 需要实际运行服务并手动发送以下请求，确认返回 knowledge_qa 而非 general_chat：
# 1. "周建国，请问工时截止还剩几天"
# 2. "李明本周工时截止几号"  
# 3. "陈经工时截止日期到了吗"

# 如需运行分类精度测试（约 30 分钟）：
../.venv/Scripts/python -m pytest tests/test_classification_accuracy.py \
  -n 4 --dist=load --tb=short \
  --json-report --json-report-file=reports/layer1_v6.json -q
../.venv/Scripts/python tests/utils/accuracy_reporter.py reports/layer1_v6.json
```

---

## Task 2：export_report Tool — 工时报表导出

### 背景

SpringBoot 已有工时导出接口，AI 侧只需封装为工具，让用户可以用自然语言触发报表导出。

### 可用的 SpringBoot API

```
GET /api/workhour/export/project-simple
    Query: startDate=YYYY-MM-DD, endDate=YYYY-MM-DD, title?=报表标题, orgId?=部门ID
    权限：deptAdmin 及以上
    返回：Excel 文件（application/octet-stream）
```

> 注意：这是当前后端唯一的导出接口，仅支持"工时汇总表"这一种报表类型。

### 设计决策

- **文件返回方式**：下载 Excel 后保存到 `/tmp/workhour_exports/` 目录，返回相对路径供前端下载（而非 base64，避免大文件传输问题）
- **权限**：仅 `deptAdmin` 及以上角色可调用（与 SpringBoot 接口一致）
- **工具名**：`export_report`

### 改动文件清单

1. **新建** `fastapi-service/app/tools/export_report.py`
2. **修改** `fastapi-service/app/tools/__init__.py` — 注册新工具
3. **修改** `fastapi-service/app/services/permission_validator.py` — 确认 export_report 需要 deptAdmin+

---

### 文件 1：新建 `fastapi-service/app/tools/export_report.py`

参照 `approve_workhour.py` 的结构，完整实现如下：

```python
"""
Export Report Tool - 工时报表导出工具

导出工时汇总报表为 Excel 文件。
权限：仅 deptAdmin 及以上角色可调用。

工具名：export_report
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)

EXPORT_DIR = Path("/tmp/workhour_exports")


# ─── JSON Schema ──────────────────────────────────────────────────────────────

EXPORT_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "start_date": {
            "type": "string",
            "description": "报表开始日期，格式 YYYY-MM-DD",
        },
        "end_date": {
            "type": "string",
            "description": "报表结束日期，格式 YYYY-MM-DD",
        },
        "title": {
            "type": "string",
            "description": "报表标题（可选，默认为"工时汇总表"）",
        },
        "org_id": {
            "type": "string",
            "description": "部门/组织ID（可选，不传则导出当前用户所在组织）",
        },
        "auth_token": {
            "type": "string",
            "description": "用户认证 token（系统自动注入，无需用户填写）",
        },
    },
    "required": ["start_date", "end_date"],
    "additionalProperties": False,
}


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def export_report_handler(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    导出工时报表为 Excel 文件。

    返回格式：
    {
        "success": True,
        "file_name": "workhour_export_xxx.xlsx",
        "file_path": "/tmp/workhour_exports/workhour_export_xxx.xlsx",
        "message": "报表已生成，文件名：workhour_export_xxx.xlsx"
    }
    """
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    title = params.get("title", "工时汇总表")
    org_id = params.get("org_id")
    auth_token = params.get("auth_token", "")

    if not start_date or not end_date:
        return {"success": False, "error": "缺少必填参数：start_date 和 end_date"}

    springboot_base = os.getenv("SPRINGBOOT_BASE_URL", "http://localhost:8080")
    url = f"{springboot_base}/api/workhour/export/project-simple"

    query_params: Dict[str, str] = {
        "startDate": start_date,
        "endDate": end_date,
        "title": title,
    }
    if org_id:
        query_params["orgId"] = org_id

    headers = {}
    if auth_token:
        token = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        headers["Authorization"] = token

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, params=query_params, headers=headers)

        if response.status_code == 403:
            return {"success": False, "error": "权限不足，仅部门管理员及以上可导出报表"}

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"导出失败，服务返回状态码 {response.status_code}",
            }

        # 保存文件
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        file_name = f"workhour_export_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
        file_path = EXPORT_DIR / file_name

        file_path.write_bytes(response.content)

        logger.info(f"报表已导出：{file_path}，大小：{len(response.content)} bytes")

        return {
            "success": True,
            "file_name": file_name,
            "file_path": str(file_path),
            "size_bytes": len(response.content),
            "message": f"报表已生成：{file_name}（{start_date} 至 {end_date}）",
        }

    except httpx.TimeoutException:
        return {"success": False, "error": "导出超时，请稍后重试（大范围报表生成可能较慢）"}
    except Exception as e:
        logger.error(f"导出报表异常: {e}", exc_info=True)
        return {"success": False, "error": f"导出失败：{str(e)}"}


# ─── 注册工具 ─────────────────────────────────────────────────────────────────

tool_registry.register(
    name="export_report",
    description=(
        "导出工时汇总报表为 Excel 文件（适用：导出报表/下载工时表/生成Excel/工时汇总导出）。"
        "需指定时间范围（开始日期和结束日期）。仅部门管理员及以上角色可使用。"
    ),
    handler=export_report_handler,
    json_schema=EXPORT_REPORT_SCHEMA,
    category=ToolCategory.QUERY,
    requires_permission=True,
    min_role="deptAdmin",
)
```

---

### 文件 2：修改 `fastapi-service/app/tools/__init__.py`

在现有 import 列表末尾追加：

```python
from . import export_report
```

并在 `__all__` 列表末尾追加：

```python
"export_report",
```

---

### 文件 3：确认 permission_validator.py

检查 `fastapi-service/app/services/permission_validator.py`，确认 `min_role="deptAdmin"` 的处理逻辑是否已覆盖 `export_report`。

如果 permission_validator 是通过读取工具注册信息中的 `min_role` 字段来做校验，则无需改动（tool_registry.register 时已传入 `min_role="deptAdmin"`）。

如果有硬编码的工具名白名单，需要将 `export_report` 加入。

---

### 验证方法

```bash
# 1. 启动服务
cd fastapi-service && python main.py

# 2. 手动测试（需要 deptAdmin 身份的 token）
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -H "X-User-ID: <user_id>" \
  -H "X-Entity-Type: deptAdmin" \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "导出本月工时汇总报表"}'

# 期望：
# 1. LLM 调用 export_report 工具，传入本月 start_date/end_date
# 2. 返回文件名和成功消息
# 3. /tmp/workhour_exports/ 下生成 xlsx 文件

# 3. 权限拒绝测试
# 用 entity_type=employee 发同样请求，期望返回"权限不足"
```

---

## Task 3：Layer 3 集成测试（全链路验证）

### 背景

Layer 1（意图识别）和 Layer 2（参数提取）均已验证，但测试都是在**模拟环境**中进行的（mock SpringBoot 调用）。Layer 3 需要验证：

> 真实请求 → FastAPI → LLM → 工具调用 → SpringBoot API → 结果 → 格式化回复

这是在接近生产条件下发现隐藏问题的最后机会。

### 测试范围

| 测试场景 | 工具 | 验证重点 |
|---------|------|---------|
| 查自己本周工时 | query_timesheet | user_id 注入、日期解析、结果格式化 |
| 查张三本月工时 | query_timesheet | member_name → memberId 解析 |
| 填今天工时8小时（带项目名） | save_workhour | 项目名 → projectId 解析 |
| 查可填报项目列表 | query_project | 列表展示 |
| 查本月工时统计 | compute_statistics | 数据汇总格式 |
| 查张三和李四本周工时（多工具） | query_timesheet x2 | PlannerAgent 并行执行 |
| 加班算工时吗 | knowledge_qa | RAG 路由 |
| 你好 | — | general_chat |

### 新建文件：`fastapi-service/tests/test_layer3_integration.py`

```python
"""
Layer 3 集成测试

前提条件：
1. FastAPI 服务已启动（http://localhost:8000）
2. SpringBoot 服务已启动（http://localhost:8080）
3. .env 文件已配置 DASHSCOPE_API_KEY

运行方式：
    pytest tests/test_layer3_integration.py -v -s \
        --base-url=http://localhost:8000 \
        --user-id=<测试用户ID> \
        --token=<测试用户JWT>
"""

import pytest
import httpx
import json
import os

BASE_URL = os.getenv("L3_BASE_URL", "http://localhost:8000")
TEST_USER_ID = os.getenv("L3_USER_ID", "test_user_1")
TEST_TOKEN = os.getenv("L3_TOKEN", "")
TEST_ENTITY_TYPE = os.getenv("L3_ENTITY_TYPE", "employee")


def make_headers(entity_type: str = None) -> dict:
    return {
        "Content-Type": "application/json",
        "X-User-ID": TEST_USER_ID,
        "X-Entity-Type": entity_type or TEST_ENTITY_TYPE,
        "X-Department-ID": "1",
        "Authorization": f"Bearer {TEST_TOKEN}",
    }


def chat(message: str, entity_type: str = None) -> dict:
    """发送聊天请求（非流式）并返回响应"""
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{BASE_URL}/api/ai/chat",
            headers=make_headers(entity_type),
            json={"message": message},
        )
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text}"
    return response.json()


# ─── 基础路由测试 ──────────────────────────────────────────────────────────────

class TestBasicRouting:
    def test_general_chat(self):
        result = chat("你好")
        assert result.get("intent") in ("general_chat", None)
        assert result.get("response"), "应有回复内容"

    def test_knowledge_qa_routing(self):
        result = chat("加班算工时吗")
        # 路由到 knowledge_qa 或 general_chat 均可，关键是有实质性回答
        assert result.get("response"), "应有回复内容"
        assert len(result["response"]) > 10, "回答不应为空或过短"


# ─── 工时查询测试 ──────────────────────────────────────────────────────────────

class TestQueryTimesheet:
    def test_query_self_this_week(self):
        result = chat("查一下我本周工时")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_timesheet"
        assert result.get("response"), "应有工时查询结果"

    def test_query_self_this_month(self):
        result = chat("查本月工时")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_timesheet"


# ─── 工时填报测试 ──────────────────────────────────────────────────────────────

class TestSaveWorkhour:
    def test_save_with_project_name(self):
        """填报工时时传项目名，验证 param_resolver 将其转为 projectId"""
        result = chat("帮我填今天工时8小时，项目是AI助手")
        # 因为项目名可能不存在，接受 tool_execution 或追问
        assert result.get("intent") in ("tool_execution", "clarify")

    def test_save_missing_project(self):
        """缺少项目时，行为取决于 LLM 判断（追问或尝试执行）"""
        result = chat("填今天8小时")
        assert result.get("intent") in ("tool_execution", "clarify")
        assert result.get("response"), "应有回复"


# ─── 项目查询测试 ──────────────────────────────────────────────────────────────

class TestQueryProject:
    def test_query_fillable_projects(self):
        result = chat("我可以填报哪些项目")
        assert result.get("intent") == "tool_execution"
        assert result.get("tool_name") == "query_project"
        assert result.get("response"), "应返回项目列表"


# ─── 多工具并行测试 ────────────────────────────────────────────────────────────

class TestMultiTool:
    def test_query_two_members(self):
        """查询两人工时，验证 PlannerAgent 并行执行"""
        result = chat("查张三和李四本月工时")
        # 期望走 plan_and_execute 路径
        assert result.get("response"), "应有汇总回复"
        # 注：intent 可能是 complex_request 或 tool_execution（取决于 LLM 是否返回多个 tool_calls）


# ─── 权限测试 ──────────────────────────────────────────────────────────────────

class TestPermission:
    def test_export_report_employee_forbidden(self):
        """普通员工不能导出报表"""
        result = chat("导出本月工时报表", entity_type="employee")
        assert "权限" in result.get("response", "") or "管理员" in result.get("response", "")

    def test_approve_workhour_employee_forbidden(self):
        """普通员工不能审核工时"""
        result = chat("审核工时记录 12345", entity_type="employee")
        assert "权限" in result.get("response", "") or "管理员" in result.get("response", "")
```

### 运行方式

```bash
# 配置环境变量后运行
export L3_USER_ID="your_test_user_id"
export L3_TOKEN="your_jwt_token"
export L3_ENTITY_TYPE="deptAdmin"

cd fastapi-service
../.venv/Scripts/python -m pytest tests/test_layer3_integration.py -v -s
```

### 注意事项

1. **测试需要真实 SpringBoot 在线**，否则工具调用会 500
2. **测试用户需要有实际工时/项目数据**，否则"查工时"结果为空，无法判断路由是否正确
3. `test_query_two_members` 可能需要 `张三`/`李四` 在系统中真实存在
4. 这个测试文件**不应纳入 CI**（依赖外部服务），仅供手动运行

---

## 执行完成后的检查清单

```
Task 1 完成标志：
  □ system.yaml 已添加 knowledge_qa few-shot 段落
  □ 手动测试 3 条含人名政策问题，均返回 knowledge_qa 工具调用
  □ （可选）运行 layer1_v6 全量测试，对比精度变化

Task 2 完成标志：
  □ export_report.py 已创建并通过 import 检查
  □ __init__.py 已注册
  □ deptAdmin 角色可触发导出，/tmp/workhour_exports/ 下生成 xlsx 文件
  □ employee 角色被拒绝，返回权限提示

Task 3 完成标志：
  □ test_layer3_integration.py 已创建
  □ 基础路由测试（general_chat / knowledge_qa）通过
  □ query_timesheet / query_project 测试通过
  □ 发现任何新 bug 均已记录到 docs/changelog/2026-04-07.md
```

---

## 不做的事（本次范围外）

| 项目 | 原因 |
|------|------|
| SQL Agent | 无用户需求驱动；需运维配置只读 DB 账号；等生产反馈后再决定 |
| PlannerAgent clarify 优化 | 需改 LangGraph 图结构，等 Layer 3 验证完成后再做 |
| Prometheus 监控 | 下一阶段（4.14~4.18）统一做 |
| vLLM 本地部署 | 同上 |
