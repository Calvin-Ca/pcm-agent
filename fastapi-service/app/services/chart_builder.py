"""
Chart Builder - 图表构建服务

根据工具执行结果，调用 LLM 生成 ECharts option JSON。
仅当数据适合可视化时（表格类、≥2行、有数值列）才触发。
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 类别过多时的阈值：超过此值触发图表布局优化
CATEGORY_THRESHOLD = 10

# ─── LLM Tool Schema（强制结构化输出）──────────────────────────────────────────

ECHARTS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "render_chart",
        "description": (
            "把表格数据转成 ECharts 5.x option 配置。"
            "仅当数据适合可视化时返回 should_render=true。"
            "echarts_option 必须是完整的 ECharts option 对象，"
            "包含 title、tooltip、series 等必要字段。"
            "bar/line 必须包含 xAxis 和 yAxis；pie 不需要坐标轴。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "enum": ["bar", "line", "pie", "table"],
                    "description": "图表类型",
                },
                "echarts_option": {
                    "type": "object",
                    "description": (
                        "完整的 ECharts option 对象。例如："
                        '{"title":{"text":"..."},"tooltip":{},'
                        '"xAxis":{"type":"category","data":["A","B"]},'
                        '"yAxis":{"type":"value"},'
                        '"series":[{"data":[10,20],"type":"bar"}]}'
                    ),
                },
                "should_render": {
                    "type": "boolean",
                    "description": "数据不适合可视化时为 false",
                },
            },
            "required": ["chart_type", "should_render"],
        },
    },
}

# 纯文本 prompt 用的 JSON Schema 描述（兼容不支持 tool_choice=func 的 vLLM）
ECHARTS_JSON_SCHEMA_DESC = json.dumps({
    "type": "object",
    "properties": {
        "chart_type": {"enum": ["bar", "line", "pie", "table"]},
        "echarts_option": {"type": "object"},
        "should_render": {"type": "boolean"},
    },
    "required": ["chart_type", "should_render"],
}, ensure_ascii=False)


# ─── 数据提取 ─────────────────────────────────────────────────────────────────

def _extract_rows(tool_result: dict) -> List[Dict[str, Any]]:
    """
    从 task_executor 返回的工具结果中提取数据行。

    支持多种工具返回格式：
    - sql_query: {"data": [...], "columns": [...]}
    - query_timesheet: {"records": [...]}
    - compute_statistics: {"items": [...]}（转为标准行格式）
    """
    if not isinstance(tool_result, dict):
        return []

    # task_executor 包装的内层 result
    inner = tool_result.get("result", tool_result)
    if not isinstance(inner, dict):
        # 如果 inner 直接是列表
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
        return []

    # sql_query: {data: [...], columns: [...]}
    if "data" in inner and isinstance(inner["data"], list):
        return inner["data"]

    # query_timesheet: {records: [...]}
    if "records" in inner and isinstance(inner["records"], list):
        return inner["records"]

    # compute_statistics: {items: [...]}
    if "items" in inner and isinstance(inner["items"], list):
        items = inner["items"]
        rows = []
        for item in items:
            if isinstance(item, dict):
                row = dict(item)
                # 展开 details 中的子字段到顶层（如果有）
                details = row.pop("details", None)
                if isinstance(details, dict):
                    for k, v in details.items():
                        if k not in row:
                            row[k] = v
                rows.append(row)
        return rows

    # query_project: {projects: [...]}
    if "projects" in inner and isinstance(inner["projects"], list):
        return inner["projects"]

    # 通用 fallback：尝试查找任何列表值
    for key, value in inner.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value

    return []


# ─── 可视化可行性判断 ─────────────────────────────────────────────────────────

def _is_chartable(rows: List[Dict[str, Any]]) -> bool:
    """
    判断数据是否适合可视化。

    条件：
    1. 至少 2 行数据
    2. 至少有一列是数值类型（int/float，排除 bool）
    """
    if len(rows) < 2:
        return False

    if not rows or not isinstance(rows[0], dict):
        return False

    numeric_types = (int, float)
    for row in rows:
        for value in row.values():
            if isinstance(value, numeric_types) and not isinstance(value, bool):
                return True

    return False


def _is_numeric_column(rows: List[Dict[str, Any]], column: str) -> bool:
    """判断某一列是否为数值列（至少一半值为数值）"""
    if not rows or column not in rows[0]:
        return False
    numeric_count = 0
    for row in rows:
        v = row.get(column)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            numeric_count += 1
    return numeric_count >= len(rows) / 2


# ─── Prompt 构建 ──────────────────────────────────────────────────────────────

def _build_chart_prompt(user_query: str, rows: List[Dict[str, Any]]) -> tuple[str, str]:
    """构建给 LLM 的 system + user prompt"""

    columns = list(rows[0].keys()) if rows else []
    # 给 LLM 最多 20 行示例数据，防止 prompt 过长
    sample_rows = rows[:20]

    # 检测数值列，用于指导 LLM 选择图表类型
    numeric_cols = [c for c in columns if _is_numeric_column(rows, c)]
    category_cols = [c for c in columns if c not in numeric_cols]

    category_count = len(rows)
    need_optimize = category_count > CATEGORY_THRESHOLD

    system_prompt = (
        "你是数据可视化专家。根据用户问题和数据特征，选择最合适的图表类型，"
        "生成标准的 ECharts 5.x option 配置。\n\n"
        "规则：\n"
        "1. 占比/分布类查询（如'占比''比例''分布'）用 pie（饼图）\n"
        "2. 时间序列/趋势类查询（如'趋势''走势''近X天''每天'）用 line（折线图）\n"
        "3. 对比/排名类查询（如'对比''排名''各部门''各项目'）用 bar（柱状图）\n"
        "4. 其他情况优先 bar\n\n"
        "类别过多优化（数据行数 > 10 时必须执行）：\n"
        "- bar 图：切换为水平条形图，yAxis 放类别名、xAxis 放数值，"
        "并设置 grid.left='25%' 给长标签留空间\n"
        "- pie 图：只保留数值最大的 Top 10，其余合并为'其他'\n"
        "- 当用户明确要求'全部'时仍展示全部数据，但用水平条形图避免重叠\n\n"
        "echarts_option 必须是完整的合法 JSON，包含：\n"
        "- title: {text: '图表标题'}\n"
        "- tooltip: {}\n"
        "- bar/line: 必须包含 xAxis 和 yAxis（水平条形图时互换）\n"
        "- pie: 不需要坐标轴，series.data 用 {name, value} 格式\n"
        "- series: 数组，每个元素包含 type 和 data\n\n"
        "数据值必须是数字，不要带单位字符串。\n\n"
        "你必须以如下 JSON Schema 输出结果（不要加 markdown 代码块，直接输出纯 JSON）：\n"
        f"{ECHARTS_JSON_SCHEMA_DESC}"
    )

    data_json = json.dumps(sample_rows, ensure_ascii=False, default=str)

    user_prompt = (
        f"用户问题：{user_query}\n"
        f"数据列：{columns}\n"
        f"数值列：{numeric_cols}\n"
        f"分类列：{category_cols}\n"
        f"数据行数：{len(rows)}（{'类别过多，必须做水平条形图/Top10 优化' if need_optimize else '可直接展示' }）\n"
        f"数据示例（前{len(sample_rows)}行）：\n{data_json}\n\n"
        f"请生成图表配置，直接输出合法 JSON，不要加 ```json 等标记。"
    )

    return system_prompt, user_prompt


# ─── 主入口 ───────────────────────────────────────────────────────────────────

async def build_chart_option(
    user_query: str,
    tool_result: dict,
    llm_client=None,
) -> Optional[dict]:
    """
    根据工具执行结果构建 ECharts option。

    Args:
        user_query: 用户的原始查询问题
        tool_result: task_executor 返回的完整结果 dict
        llm_client: LLM 客户端实例（可选）

    Returns:
        {"echarts_option": {...}, "chart_type": "..."} 或 None（不适合可视化）
    """
    rows = _extract_rows(tool_result)

    if not _is_chartable(rows):
        return None

    # 行数过多时降级为表格
    if len(rows) > 50:
        return {
            "chart_type": "table",
            "fallback_table": rows[:200],  # 最多返回 200 行给前端
        }

    if not llm_client:
        logger.debug("llm_client 未提供，跳过图表生成")
        return None

    system_prompt, user_prompt = _build_chart_prompt(user_query, rows)

    try:
        content = await llm_client.generate(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1500,
        )

        if not content:
            return None

        # 清理可能的 markdown 代码块标记
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # 提取 JSON
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None

        parsed = json.loads(content[start:end + 1])

        if not parsed.get("should_render", True):
            return None

        chart_type = parsed.get("chart_type", "bar")
        echarts_option = parsed.get("echarts_option")
        if echarts_option and isinstance(echarts_option, dict):
            return {
                "echarts_option": echarts_option,
                "chart_type": chart_type,
            }

        return None

    except Exception as e:
        logger.warning(f"图表生成失败，静默降级: {e}")
        return None
