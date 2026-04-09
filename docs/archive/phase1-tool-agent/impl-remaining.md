# 第一阶段剩余任务：实现设计文档

> 编写日期：2026-04-03  
> 适用版本：当前 main 分支（Layer2 Tool Agent）  
> 任务来源：`docs/roadmap.md` 第一阶段剩余两项

---

## 概览

| 任务 | 预估 | 涉及文件数 | 难度 |
|------|------|-----------|------|
| T1：`approve_workhour` 工时审核 Tool | 2-3h | 3 | 低 |
| T2：jieba 接入 BM25 + 补充知识库文档 | 1h | 4 | 极低 |

---

## T1：工时审核 Tool — `approve_workhour`

### 1.1 背景与约束

**SpringBoot 端实际 API**（来自 `docs/springboot-api-reference.md`）：

```
POST /api/workhour/batch-approve
Body: List<String> workhourIds
说明：批量审核工时（仅通过，无拒绝参数）
```

> **注意**：后端只有 `batch-approve`（批量通过），**没有单条审核或拒绝接口**。  
> 因此 Tool 的 `action` 参数只支持 `approve`；`reject` 在文档注释里说明暂不支持，
> 待后端补充 `/api/workhour/reject` 接口后再启用。  
> 这个决定让 Tool 现在就能交付，不依赖后端改动。

---

### 1.2 新建文件：`fastapi-service/app/tools/approve_workhour.py`

文件结构参照 `save_workhour.py`（最简单的 Tool 模板）：

```python
"""
Approve Workhour Tool - 工时审核工具

审核（通过）工时记录，支持批量操作。
权限：仅 deptAdmin 及以上角色可调用。

工具名：approve_workhour
"""

import logging
import os
from typing import Any, Dict, List, Union

import httpx

from app.models.tool import ToolCategory
from app.services.tool_registry import tool_registry

logger = logging.getLogger(__name__)


# ─── JSON Schema ──────────────────────────────────────────────────────────────

APPROVE_WORKHOUR_SCHEMA = {
    "type": "object",
    "properties": {
        "workhour_ids": {
            "oneOf": [
                {"type": "string", "description": "单条工时记录ID"},
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "工时记录ID列表（批量审核）",
                    "minItems": 1,
                },
            ],
            "description": "要审核的工时记录ID，支持单个ID（字符串）或多个ID（数组）",
        },
        "action": {
            "type": "string",
            "enum": ["approve"],
            "description": "审核动作：approve（通过）。拒绝功能待后端支持后开放。",
        },
    },
    "required": ["workhour_ids", "action"],
    "additionalProperties": False,
}


# ─── 工具 Handler ─────────────────────────────────────────────────────────────

async def approve_workhour_handler(**kwargs) -> Dict[str, Any]:
    """
    工时审核工具处理函数。

    流程：
    1. 标准化 workhour_ids 为列表
    2. 校验 action（目前只支持 approve）
    3. 调用 POST /api/workhour/batch-approve
    """
    auth_token = kwargs.pop("auth_token", None)

    raw_ids: Union[str, List[str]] = kwargs.get("workhour_ids", [])
    action: str = kwargs.get("action", "approve")

    # 1. 标准化 ID 列表
    if isinstance(raw_ids, str):
        workhour_ids = [raw_ids]
    else:
        workhour_ids = list(raw_ids)

    if not workhour_ids:
        return {"success": False, "error": "工时记录ID（workhour_ids）不能为空"}

    # 2. 校验 action
    if action != "approve":
        return {
            "success": False,
            "error": f"暂不支持的审核动作：{action}。目前仅支持 approve（通过）。",
        }

    # 3. 调用后端
    base_url = os.getenv("SPRINGBOOT_BASE_URL") or (
        f"http://{os.getenv('SPRINGBOOT_HOST', 'host.docker.internal')}:8080"
    )
    headers: Dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = auth_token

    try:
        url = f"{base_url}/api/workhour/batch-approve"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=workhour_ids, headers=headers)
            response.raise_for_status()

        count = len(workhour_ids)
        return {
            "success": True,
            "message": f"工时审核成功：已通过 {count} 条工时记录",
            "approved_count": count,
            "workhour_ids": workhour_ids,
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"工时审核 API 调用失败: {e}")
        # 403 单独提示权限问题（后端会在这里二次校验）
        if e.response.status_code == 403:
            return {"success": False, "error": "权限不足：您没有审核工时的权限"}
        return {"success": False, "error": f"服务调用失败: HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        logger.error(f"工时审核网络错误: {e}")
        return {"success": False, "error": f"网络请求失败: {e}"}
    except Exception as e:
        logger.error(f"工时审核异常: {e}", exc_info=True)
        return {"success": False, "error": f"审核异常: {e}"}


# ─── 注册 ─────────────────────────────────────────────────────────────────────

def register_approve_workhour_tool():
    """注册工时审核工具"""
    try:
        tool_registry.register_tool(
            name="approve_workhour",
            description=(
                "审核（通过）工时记录（适用：审核工时/批准工时/通过工时申请）。"
                "部门管理员及以上角色（deptAdmin+）可审核；"
                "项目负责人（project_manager）可审核其负责项目的工时。"
                "支持单条或批量审核，传入工时记录ID即可。"
            ),
            json_schema=APPROVE_WORKHOUR_SCHEMA,
            handler=approve_workhour_handler,
            category=ToolCategory.WORKHOUR,
            timeout=30,
            requires_permission=True,
        )
        logger.info("工时审核工具注册成功")
    except Exception as e:
        logger.error(f"工时审核工具注册失败: {e}")
        raise


if __name__ != "__main__":
    register_approve_workhour_tool()
```

---

### 1.3 修改文件：`fastapi-service/app/tools/__init__.py`

在现有 import 列表末尾追加：

```python
# 现有内容（不改）
from . import query_timesheet
from . import query_project
from . import compute_statistics
from . import generate_weekly_report
from . import save_workhour
from . import knowledge_qa

# 新增
from . import approve_workhour   # ← 加这一行

__all__ = [
    "query_timesheet",
    "query_project",
    "compute_statistics",
    "generate_weekly_report",
    "save_workhour",
    "knowledge_qa",
    "approve_workhour",          # ← 加这一行
]
```

---

### 1.4 修改文件：`fastapi-service/app/services/task_executor.py`

在 `_execute_tool_call` 方法的权限验证 `if/elif` 链中，在 `generate_weekly_report / save_workhour` 的 `elif` **之后**，追加 `approve_workhour` 的权限块：

**定位**：`task_executor.py` 第 286-292 行（`elif task.tool_name in ['generate_weekly_report', 'save_workhour']:` 块）

在该块之后追加：

```python
            elif task.tool_name == 'approve_workhour':
                # 工时审核权限：以下两类用户可调用：
                #   A. 部门管理员及以上角色（deptAdmin / regionAdmin / companyAdmin / superAdmin）
                #   B. 项目负责人（entity_type 为 employee，但 managed_projects 非空）
                #      ——具体到某条工时是否属于其负责项目，由后端 SpringBoot 二次校验（会返回 403）
                ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
                is_admin = permission_context.entity_type in ADMIN_ROLES
                is_project_manager = bool(permission_context.managed_projects)
                if not is_admin and not is_project_manager:
                    raise PermissionError(
                        "工时审核权限不足：需要部门管理员角色，或担任至少一个项目的项目负责人"
                    )
```

**完整上下文**（替换后的样子，便于 agent 精确定位）：

```python
            elif task.tool_name in ['generate_weekly_report', 'save_workhour']:
                # Phase 8 工具：只允许访问自己的数据，管理员除外
                target_user_id = processed_params.get('user_id')
                if target_user_id:
                    result = self.permission_validator.can_access_user_data(permission_context, target_user_id)
                    if not result.allowed:
                        raise PermissionError(f"无权限操作用户 {target_user_id} 的工时数据：{result.reason}")

            elif task.tool_name == 'approve_workhour':
                # 工时审核权限：以下两类用户可调用：
                #   A. 部门管理员及以上角色（deptAdmin / regionAdmin / companyAdmin / superAdmin）
                #   B. 项目负责人（entity_type 为 employee，但 managed_projects 非空）
                #      ——具体到某条工时是否属于其负责项目，由后端 SpringBoot 二次校验（会返回 403）
                ADMIN_ROLES = {"deptAdmin", "regionAdmin", "companyAdmin", "superAdmin"}
                is_admin = permission_context.entity_type in ADMIN_ROLES
                is_project_manager = bool(permission_context.managed_projects)
                if not is_admin and not is_project_manager:
                    raise PermissionError(
                        "工时审核权限不足：需要部门管理员角色，或担任至少一个项目的项目负责人"
                    )
```

---

### 1.5 验证步骤

```bash
cd fastapi-service

# 1. 确认工具注册成功（启动时日志）
python -c "from app.tools import approve_workhour; print('注册成功')"

# 2. 手动测试（需要服务已启动）
# 用 deptAdmin token 发请求：应该成功
# 用 employee token 发请求：应返回权限错误

# 3. 确认不影响现有工具
pytest tests/test_core_functionality.py -v
```

---

## T2：jieba 接入 BM25 + 补充知识库文档

### 2.1 背景

当前 `langchain_rag.py` 的 BM25 使用默认分词（按空格拆分），对中文无效：
- "工时审核" 会被当成一个 token，无法拆成 "工时" + "审核"
- 导致 BM25 对中文专业词汇的召回率极低

`user_memory.py` 的 `_tokenize` 方法同样是逐字切分，也需要换 jieba。

---

### 2.2 修改文件：`fastapi-service/requirements.txt`

在文件末尾追加：

```
jieba==0.42.1
```

---

### 2.3 修改文件：`fastapi-service/app/services/langchain_rag.py`

**定位**：第 244-250 行（BM25 检索器初始化块）

**当前代码**：

```python
        # 4. 初始化 BM25 检索器（纯内存，无需外部依赖）
        from langchain_community.retrievers import BM25Retriever

        self.bm25_retriever = await asyncio.to_thread(
            BM25Retriever.from_documents, documents, k=5
        )
        logger.info("BM25 检索器初始化完成")
```

**替换为**：

```python
        # 4. 初始化 BM25 检索器（纯内存，无需外部依赖）
        from langchain_community.retrievers import BM25Retriever

        def _jieba_tokenizer(text: str):
            """使用 jieba 分词，提升中文专业词汇的 BM25 召回率。"""
            import jieba
            return list(jieba.cut(text))

        self.bm25_retriever = await asyncio.to_thread(
            BM25Retriever.from_documents, documents, k=5,
            preprocess_func=_jieba_tokenizer,
        )
        logger.info("BM25 检索器初始化完成（jieba 分词）")
```

> **说明**：`BM25Retriever.from_documents` 的 `preprocess_func` 参数接受一个 `Callable[[str], List[str]]`，
> 会在建立倒排索引和查询时同时应用，因此不需要其他改动。

---

### 2.4 修改文件：`fastapi-service/app/services/user_memory.py`

**定位**：第 229-242 行（`_tokenize` 方法）

**当前代码**：

```python
    def _tokenize(self, text: str) -> List[str]:
        """
        简单中文分词（按字符/标点分割）。
        生产环境可替换为 jieba 等分词器。
        """
        import re
        # 去除标点，按空格/标点切分，保留中文字符（每个字为一个 token）
        tokens = []
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                tokens.append(char)
            elif char.isalnum():
                tokens.append(char.lower())
        return tokens
```

**替换为**：

```python
    def _tokenize(self, text: str) -> List[str]:
        """使用 jieba 分词（中文词语级别切分，提升专业术语匹配率）。"""
        try:
            import jieba
            return [t for t in jieba.cut(text) if t.strip()]
        except ImportError:
            # jieba 未安装时降级为逐字切分
            tokens = []
            for char in text:
                if '\u4e00' <= char <= '\u9fff':
                    tokens.append(char)
                elif char.isalnum():
                    tokens.append(char.lower())
            return tokens
```

---

### 2.5 新建知识库文档

在 `knowledge-base/` 目录下新建以下两个文件：

---

#### 文件 1：`knowledge-base/工时审核流程.md`

```markdown
# 工时审核流程

## 审核角色与权限

| 角色 | 审核权限 |
|------|---------|
| 部门管理员（deptAdmin） | 审核本部门成员的工时 |
| 大区管理员（regionAdmin） | 审核大区内所有部门工时 |
| 公司管理员（companyAdmin） | 审核全公司工时 |
| 超级管理员（superAdmin） | 无限制 |
| 项目负责人（project_manager） | 仅可审核其负责项目的工时（即使角色为普通员工） |
| 普通员工（employee） | 无审核权限（除非同时担任项目负责人） |

## 审核流程

1. 员工填写工时后，状态为"待审核"
2. 部门管理员登录系统，查看待审核工时列表
3. 管理员审核通过后，工时状态变为"已审核"
4. 工时审核后方可纳入统计和报表

## AI 助手使用说明

可通过自然语言让 AI 批量审核工时，例如：
- "帮我审核工时 ID 为 123 的记录"
- "通过张三提交的所有工时，ID 是 101、102、103"
- "批量审核这些工时：[101, 102, 103]"

## 常见问题

**Q：工时审核后还能修改吗？**  
A：已审核工时原则上不可修改。如需更改，需联系管理员撤销审核后重新提交。

**Q：审核拒绝怎么操作？**  
A：AI 工具目前仅支持审核通过（approve）。如需拒绝，请在工时管理系统 Web 界面操作。

**Q：忘记审核会怎样？**  
A：未审核工时不会计入月度统计，请管理员定期检查待审核列表。
```

---

#### 文件 2：`knowledge-base/假期与加班政策.md`

```markdown
# 假期与加班政策

## 年假政策

| 工龄 | 年假天数 |
|------|---------|
| 1年以下 | 按比例折算 |
| 1-3年 | 5天 |
| 3-10年 | 10天 |
| 10年以上 | 15天 |

年假需提前在 OA 系统申请，审批通过后方可休假。年假当年有效，原则上不可跨年结转。

## 调休政策

- 加班产生的调休时数在 OA 系统中自动累计
- 调休可按小时为单位使用，最小单位 0.5 小时
- 调休时数有效期：产生之日起 6 个月内使用

## 加班政策

- 工作日加班：提前申请，填写加班工时记录
- 节假日加班：享受 3 倍工资，或协商换调休
- 周末加班：享受 2 倍工资，或协商换调休
- 加班工时必须在工时管理系统中填报，作为薪酬核算依据

## 工时填报要求

1. 加班工时与正常工时分开填报，选择对应项目和工时类型
2. 每天最多填报 24 小时工时（含正常 + 加班）
3. 加班工时需在次日提交，超过 3 个工作日未提交视为放弃
4. 当月工时须在次月 5 日前全部提交完毕

## 常见问题

**Q：年假怎么查剩余天数？**  
A：请在 OA 系统个人中心查看，或咨询 HR 部门。

**Q：加班工时填报后多久能审核？**  
A：提交后 3 个工作日内，部门管理员会完成审核。

**Q：工时填错了怎么办？**  
A：工时在审核前可以修改。审核后需联系部门管理员撤销后重新填报。
```

---

### 2.6 验证步骤

```bash
cd fastapi-service

# 1. 安装 jieba
pip install jieba==0.42.1

# 2. 快速验证分词效果
python -c "
import jieba
text = '工时审核流程和部门管理员权限'
print(list(jieba.cut(text)))
# 期望输出：['工时', '审核', '流程', '和', '部门', '管理员', '权限']
"

# 3. 确认 BM25Retriever 接受 preprocess_func
python -c "
from langchain_community.retrievers import BM25Retriever
import inspect
sig = inspect.signature(BM25Retriever.from_documents)
print('preprocess_func' in sig.parameters)  # 期望 True
"

# 4. 运行 RAG 测试（确认现有功能没有破坏）
pytest tests/test_langchain_rag_retrieval.py -v
```

---

## 执行顺序建议

建议 agent 按以下顺序执行，每步都可以独立验证：

```
Step 1  创建 approve_workhour.py（复制 save_workhour.py 为模板，按 1.2 节改写）
Step 2  修改 __init__.py（加一行 import + 一行 __all__）
Step 3  修改 task_executor.py（加 elif 权限块）
Step 4  修改 requirements.txt（加 jieba）
Step 5  修改 langchain_rag.py（加 preprocess_func）
Step 6  修改 user_memory.py（换 _tokenize 实现）
Step 7  新建两个知识库 .md 文件
Step 8  运行验证命令
```

---

## 注意事项

1. **approve_workhour 的权限判断**：`task_executor.py` 中的 `permission_context.entity_type` 是字符串，和 `EntityType` 枚举的 `.value` 对应，直接用字符串集合比较即可（不要 import EntityType 枚举，保持简单）。

2. **jieba 首次加载**：第一次 `import jieba` 时会加载词典（约 0.3s），之后缓存在内存中。不影响正常运行。

3. **BM25 preprocess_func 版本兼容**：`langchain_community >= 0.0.20` 才支持 `preprocess_func`。如果版本较旧，改为在 `from_documents` 前手动对 documents 的 `page_content` 做 jieba 分词替换（空格拼接），同样有效。

4. **知识库文档加载**：新增 `.md` 文件后，重启服务即可自动加载。无需其他操作（`_load_documents_from_dir` 会遍历整个目录）。
