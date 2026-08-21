"""Generate and validate the pre-production business-completion dataset.

The script uses the configured OpenAI-compatible CHAT_LLM endpoint only to
create natural-language test cases. It never sends production data or writes
to SpringBoot/MySQL. Expected tool results are deterministic mocks added
locally so the dataset can exercise the complete Agent-side contract.

Run from repository root::

    .venv/Scripts/python.exe \
      docs_caich/test_by_caic/pre_production_mandatory_tests/\
      01-business-task-completion/generate_business_completion_dataset.py

Use ``--validate-only`` to validate existing output without an API call.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(__file__).resolve().parent / "data"
RAW_DIR = OUTPUT_DIR / "generation_raw"
REFERENCE_DATE = "2026-08-07"
PROMPT_VERSION = "business-completion-v1"

SINGLE_QUOTAS = {
    "query_timesheet": 30,
    "compute_statistics": 30,
    "query_project": 25,
    "save_workhour": 35,
    "batch_save_workhour": 30,
    "generate_weekly_report": 25,
    "knowledge_qa": 25,
    "general_chat": 20,
    "complex_task": 20,
    "robustness": 20,
    "approve_workhour": 12,
    "export_report": 12,
    "suggest_workhour": 12,
    "kb_navigation": 24,
}

MULTI_QUOTAS = {
    "clarification_completion": 10,
    "context_inheritance": 10,
    "parameter_correction": 10,
    "task_switch": 10,
    "identity_switch": 10,
    "interruption_resume": 10,
    "tool_failure_recovery": 10,
    "write_confirmation": 10,
    "ambiguous_reference": 10,
    "partial_tool_failure": 10,
}

BUSINESS_TOOLS = {
    "query_timesheet",
    "compute_statistics",
    "query_project",
    "save_workhour",
    "batch_save_workhour",
    "generate_weekly_report",
    "approve_workhour",
    "export_report",
    "suggest_workhour",
    "knowledge_qa",
    "kb_outline",
    "kb_keyword_search",
    "kb_semantic_search",
    "kb_read_section",
}
WRITE_TOOLS = {"save_workhour", "batch_save_workhour", "approve_workhour"}
DRY_RUN_TOOLS = {"save_workhour", "batch_save_workhour"}
VALID_ACTIONS = {"tool", "clarify", "rag", "direct", "multi_tool"}
VALID_ROLES = {
    "employee",
    "deptSubAdmin",
    "deptAdmin",
    "regionAdmin",
    "companyAdmin",
    "superAdmin",
}

CATEGORY_RULES = {
    "query_timesheet": "工时明细查询；工具只能是 query_timesheet。覆盖本人/他人/项目/日期范围/空结果/越权。",
    "compute_statistics": "工时统计、汇总、排名、占比、平均值；工具只能是 compute_statistics，不要写成明细查询。",
    "query_project": "项目查询和模糊项目名解析；工具只能是 query_project。",
    "save_workhour": "单条工时填报；完整参数调用 save_workhour，缺关键参数必须 clarify；所有调用都 dry_run=true。",
    "batch_save_workhour": "一次输入包含至少两条工时记录或表格；工具只能是 batch_save_workhour，参数 text 必须保留原输入，dry_run=true。",
    "generate_weekly_report": "生成本周、上周或指定 ISO 周的周报；工具只能是 generate_weekly_report。",
    "knowledge_qa": "工时制度、填报规则等知识库问答；expected_action=rag，expected_tools=[]。不要编造个人业务数据。",
    "general_chat": "普通寒暄或能力说明；expected_action=direct，expected_tools=[]，不得调用业务工具。",
    "complex_task": "一次请求包含两个及以上相互关联的只读步骤；expected_action=multi_tool，至少两个工具。不得包含真实写入。",
    "robustness": "空白之外的异常输入、错别字、口语、繁体、英中混合、emoji、提示注入或动作不明确；在 direct/clarify/tool/rag 中给出安全合理期望。",
    "approve_workhour": "审核通过单条或多条工时；工具只能是 approve_workhour。仅管理员角色允许，员工应澄清或拒绝。该工具没有 dry_run，数据集必须标记 Mock-only。",
    "export_report": "导出指定日期范围的 Excel 工时报表；工具只能是 export_report。覆盖管理员成功、员工越权、缺日期和超时。",
    "suggest_workhour": "用户有填报意图但缺项目或时长时给出历史建议；工具只能是 suggest_workhour，不得写入。",
    "kb_navigation": "多步知识库导航。覆盖 knowledge_qa、kb_outline、kb_keyword_search、kb_semantic_search、kb_read_section 五个工具，至少一半用例需要两个及以上知识库工具。",
}

SCENARIO_RULES = {
    "clarification_completion": "首轮缺少写入关键参数，Agent 追问；后续用户补齐，最终只做 dry-run 预览。",
    "context_inheritance": "先完成查询，后续用‘上周呢/那个项目呢’等省略表达，正确继承非冲突参数。",
    "parameter_correction": "用户纠正日期、人员、项目或时长；新值覆盖旧值，不得同时保留冲突参数。",
    "task_switch": "用户从查询切换到填报或从填报切换到查询；切换时清理不适用的旧参数。",
    "identity_switch": "查询主体从本人切换到他人；包含员工越权应拒绝、管理员允许的不同情况。",
    "interruption_resume": "未完成任务被闲聊打断后继续；恢复必要上下文但不得臆造缺失参数。",
    "tool_failure_recovery": "工具首次返回失败/超时/空结果，Agent如实说明；用户修改参数后再试，不能把失败说成成功。",
    "write_confirmation": "批量或单条写先 dry-run 预览，再由用户确认；测试只使用 Mock，不接真实数据库。",
    "ambiguous_reference": "‘他/那个项目/还是之前的’存在多个候选时必须澄清，唯一候选时才继承。",
    "partial_tool_failure": "多工具任务中一个成功一个失败；最终回答必须逐项说明，不能声称全部完成。",
}


def load_env_file(path: Path, *, override: bool) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def load_environment() -> None:
    load_env_file(REPO_ROOT / ".env", override=False)
    load_env_file(REPO_ROOT / ".env.local", override=True)


def extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output root must be an object")
    return parsed


class GeneratorClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("CHAT_LLM_API_KEY", "")
        self.api_base = os.getenv(
            "CHAT_LLM_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/")
        self.model = os.getenv("CHAT_LLM_MODEL", "qwen-plus")
        if not self.api_key:
            raise RuntimeError("CHAT_LLM_API_KEY is not configured")
        self.usage = Counter()

    async def generate_json(self, prompt: str, *, attempts: int = 3) -> tuple[dict[str, Any], str]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是企业工时 Agent 的资深测试设计师。只输出合法 JSON，"
                        "不得输出 Markdown、解释、注释或省略号。测试数据不得包含真实手机号、"
                        "密钥、Token、数据库地址或真实生产数据。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens": 8000,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=180)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{self.api_base}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        body = await response.text()
                        if response.status != 200:
                            raise RuntimeError(f"LLM HTTP {response.status}: {body[:300]}")
                        envelope = json.loads(body)
                        usage = envelope.get("usage") or {}
                        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                        self.usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
                        content = envelope["choices"][0]["message"]["content"]
                        return extract_json(content), content
            except Exception as exc:  # retry generation/JSON failures
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(attempt * 2)
        raise RuntimeError(f"LLM generation failed after {attempts} attempts: {last_error}")


def tool_contract_text() -> str:
    return """
工具参数契约：
- query_timesheet: {start_date: YYYY-MM-DD, end_date: YYYY-MM-DD, user_id?: string, project_id?: string}
- compute_statistics: {statistics_type: user_hours|project_hours|department_hours|daily_hours|weekly_hours|monthly_hours, start_date: YYYY-MM-DD, end_date: YYYY-MM-DD, user_id?: string, project_id?: string, department_id?: string, work_type?: string}
- query_project: {project_id?: string, project_name?: string, status?: string}
- save_workhour: {project_id?: string, date?: YYYY-MM-DD, duration?: 0.5~10且为0.5倍数, description?: string, user_id?: string, dry_run: true}
- batch_save_workhour: {text: 必须与用户原始输入完全相同, dry_run: true}
- generate_weekly_report: {user_id?: string, week?: thisWeek|lastWeek|YYYY-WNN|YYYY-MM-DD}
- approve_workhour: {workhour_ids: string|string[], action: approve}（无 dry_run，仅 Mock）
- export_report: {start_date: YYYY-MM-DD, end_date: YYYY-MM-DD, title?: string, org_id?: string}
- suggest_workhour: {fill_date?: YYYY-MM-DD}
- knowledge_qa: {query: string}
- kb_outline: {category?: string}
- kb_keyword_search: {query: string, category?: string, top_k?: 1~20}
- kb_semantic_search: {query: string, category?: string, top_k?: 1~20}
- kb_read_section: {file: string, section: string, include_neighbors?: boolean}
知识库问答走 rag 路径，不把它写成业务工具调用。普通聊天走 direct。
固定参考日期为 2026-08-07（周五）；所有明确日期使用 2026 年合法日期。
""".strip()


def single_prompt(category: str, count: int) -> str:
    return f"""
生成 {count} 条全新、互不重复的单轮盲测用例，类别为 {category}。
类别规则：{CATEGORY_RULES[category]}
{tool_contract_text()}

只返回：
{{"cases":[{{
  "user_goal":"简短目标",
  "input":"真实自然的中文用户输入",
  "sub_type":"稳定的英文蛇形标签",
  "risk_level":"normal|high|critical",
  "entity_type":"employee|deptAdmin|companyAdmin",
  "expected_action":"tool|clarify|rag|direct|multi_tool",
  "expected_tools":[{{"name":"工具名","params":{{}}}}],
  "tags":["标签"]
}}]}}

硬性要求：cases 数量必须恰好为 {count}；input 不重复；不要使用真实人物信息，人物统一使用李明、王芳等虚构名，项目统一使用星云平台、智慧园区、ERP升级等虚构名。缺参场景 expected_action=clarify 且 expected_tools=[]。save_workhour 和 batch_save_workhour 必须 dry_run=true；approve_workhour 没有 dry_run，只能用于 Mock。复杂任务至少两个只读工具。不要生成助手答案。
""".strip()


def multi_prompt(scenario: str, count: int) -> str:
    return f"""
生成 {count} 段全新、互不重复的多轮业务会话盲测骨架，场景为 {scenario}。
场景规则：{SCENARIO_RULES[scenario]}
{tool_contract_text()}

每段包含 3~6 个“用户回合”；assistant 的实际回复由被测 Agent 产生，所以不要生成 assistant 文本。
只返回：
{{"cases":[{{
  "user_goal":"整段会话最终目标",
  "risk_level":"normal|high|critical",
  "entity_type":"employee|deptAdmin|companyAdmin",
  "turns":[{{
    "user_input":"本回合用户输入",
    "expected_action":"tool|clarify|rag|direct|multi_tool",
    "expected_tools":[{{"name":"工具名","params":{{}}}}],
    "context_assertions":["应继承或应清除的上下文"]
  }}],
  "tags":["标签"]
}}]}}

硬性要求：cases 数量恰好为 {count}；每段 3~6 个用户回合；写工具一律使用 Mock，首次只做 dry_run=true；发生工具失败时下一回合前必须能检查 Agent 没有声称成功；参数纠正后只保留新值；不要生成助手答案。
""".strip()


def multi_repair_prompt(scenario: str, cases: list[dict[str, Any]]) -> str:
    serialized = json.dumps(cases, ensure_ascii=False)
    return f"""
修复下面 {len(cases)} 段 {scenario} 多轮测试骨架。当前每段 turns 太短或太长。
{SCENARIO_RULES[scenario]}
{tool_contract_text()}

原始 cases：{serialized}

只返回 {{"cases":[...]}}。cases 数量和顺序不变；每段 turns 数组必须包含 3~6 个用户回合（数组长度至少3、最多6），新增回合必须推进、确认、纠正或验证原业务目标，不能只加“好的/谢谢”。user_input 只能是用户真正会说的话，绝不能把“工具返回错误/工具返回空结果/工具响应”伪装成用户输入；工具失败应由 expected_tools 对应的 Mock 结果表达。每个 turn 仍须完整包含 user_input、expected_action、expected_tools、context_assertions。expected_action 只能是 tool、clarify、rag、direct、multi_tool。写工具 dry_run=true。不要生成 assistant 文本。
""".strip()


def generated_multi_case_valid(case: Any) -> bool:
    if not isinstance(case, dict) or not isinstance(case.get("turns"), list):
        return False
    turns = case["turns"]
    if not 3 <= len(turns) <= 6:
        return False
    for turn in turns:
        if not isinstance(turn, dict):
            return False
        user_input = str(turn.get("user_input", "")).strip()
        if not user_input or re.search(r"工具(?:返回|响应)(?:错误|空结果|失败|成功)?[：:]", user_input):
            return False
        action = str(turn.get("expected_action", "")).strip()
        tools = turn.get("expected_tools") if isinstance(turn.get("expected_tools"), list) else []
        if action not in VALID_ACTIONS and not tools:
            return False
    return True


def sanitize_generated_multi_case(case: Any) -> Any:
    """Move model-written tool-result pseudo turns back to Mock metadata."""
    if not isinstance(case, dict) or not isinstance(case.get("turns"), list):
        return case
    cleaned = dict(case)
    cleaned_turns: list[dict[str, Any]] = []
    for raw_turn in case["turns"]:
        if not isinstance(raw_turn, dict):
            continue
        user_input = str(raw_turn.get("user_input", "")).strip()
        if re.search(r"工具(?:返回|响应)(?:错误|空结果|失败|成功)?[：:]", user_input):
            if cleaned_turns:
                previous = dict(cleaned_turns[-1])
                assertions = list(previous.get("context_assertions") or [])
                assertions.append(f"Mock工具结果：{user_input}")
                previous["context_assertions"] = assertions
                cleaned_turns[-1] = previous
            continue
        normalized_turn = dict(raw_turn)
        action = str(normalized_turn.get("expected_action", "")).strip()
        raw_tools = normalized_turn.get("expected_tools") if isinstance(normalized_turn.get("expected_tools"), list) else []
        if action not in VALID_ACTIONS:
            if len(raw_tools) > 1:
                normalized_turn["expected_action"] = "multi_tool"
            elif raw_tools:
                normalized_turn["expected_action"] = "tool"
            else:
                normalized_turn["expected_action"] = "clarify" if any(mark in user_input for mark in ("？", "?", "哪个", "哪一个", "确认")) else "direct"
        cleaned_turns.append(normalized_turn)
    cleaned["turns"] = cleaned_turns
    return cleaned


def normalize_tools(tools: Any, input_text: str) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    normalized = []
    for item in tools:
        if not isinstance(item, dict) or item.get("name") not in BUSINESS_TOOLS:
            continue
        name = item["name"]
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        params = dict(params)
        if name == "query_project":
            project_value = params.get("project_id") or params.get("project_name")
            params = {"project_id": str(project_value)} if project_value else {}
        elif name == "query_timesheet":
            if params.get("department_id"):
                name = "compute_statistics"
                params = {
                    "statistics_type": "department_hours",
                    "start_date": params.get("start_date"),
                    "end_date": params.get("end_date"),
                    "department_id": params.get("department_id"),
                }
                params = {key: value for key, value in params.items() if value is not None}
            elif params.get("format"):
                # query_timesheet cannot export or shape CSV output.
                continue
            else:
                if params.get("project_name") and not params.get("project_id"):
                    params["project_id"] = params["project_name"]
                params = {key: value for key, value in params.items() if key in {"user_id", "start_date", "end_date", "project_id"} and value is not None}
                if not params.get("start_date") or not params.get("end_date"):
                    continue
        elif name == "compute_statistics":
            allowed = {"statistics_type", "start_date", "end_date", "user_id", "project_id", "department_id", "work_type"}
            params = {key: value for key, value in params.items() if key in allowed and value is not None}
        elif name == "generate_weekly_report":
            if params.get("project_id"):
                # Current tool can scope by user/week, not by project.
                continue
            params = {key: value for key, value in params.items() if key in {"user_id", "week"} and value is not None}
        elif name == "export_report":
            params = {key: value for key, value in params.items() if key in {"start_date", "end_date", "title", "org_id"} and value is not None}
        elif name == "approve_workhour":
            params = {key: value for key, value in params.items() if key in {"workhour_ids", "action"} and value is not None}
        elif name == "suggest_workhour":
            params = {key: value for key, value in params.items() if key == "fill_date" and value is not None}
        elif name in {"knowledge_qa", "kb_keyword_search", "kb_semantic_search"}:
            allowed = {"query"} if name == "knowledge_qa" else {"query", "category", "top_k"}
            params = {key: value for key, value in params.items() if key in allowed and value is not None}
        elif name == "kb_outline":
            params = {key: value for key, value in params.items() if key == "category" and value is not None}
        elif name == "kb_read_section":
            params = {key: value for key, value in params.items() if key in {"file", "section", "include_neighbors"} and value is not None}
        if name in DRY_RUN_TOOLS:
            params["dry_run"] = True
        if name == "batch_save_workhour":
            params["text"] = input_text
        normalized.append({"name": name, "params": params})
    return normalized


def choose_outcome(case_index: int, tools: list[dict[str, Any]], *, multi: bool = False) -> str:
    names = {item["name"] for item in tools}
    if not names:
        return "not_applicable"
    if "approve_workhour" in names:
        outcomes = ["success", "success", "permission_denied", "tool_error", "timeout_unknown", "connection_lost_unknown"]
    elif names & WRITE_TOOLS:
        outcomes = [
            "dry_run_success",
            "dry_run_success",
            "dry_run_success",
            "validation_error",
            "permission_denied",
            "timeout_unknown",
            "connection_lost_unknown",
        ]
    elif len(names) > 1 or multi:
        outcomes = ["success", "success", "partial_failure", "empty_result", "tool_error"]
    else:
        outcomes = ["success", "success", "success", "empty_result", "tool_error", "permission_denied", "timeout"]
    return outcomes[case_index % len(outcomes)]


def mock_result(tool_name: str, outcome: str) -> dict[str, Any]:
    if outcome in {"permission_denied"}:
        return {"success": False, "error_code": "PERMISSION_DENIED", "message": "无权执行该操作"}
    if outcome in {"timeout", "timeout_unknown"}:
        return {"success": False, "error_code": "TIMEOUT", "message": "请求超时，结果未知"}
    if outcome == "connection_lost_unknown":
        return {"success": False, "error_code": "CONNECTION_LOST", "message": "连接中断，提交结果未知，请查询确认"}
    if outcome in {"tool_error", "validation_error"}:
        code = "VALIDATION_ERROR" if outcome == "validation_error" else "UPSTREAM_ERROR"
        return {"success": False, "error_code": code, "message": "工具执行失败"}
    if outcome == "empty_result":
        return {"success": True, "empty": True, "items": [], "message": "未查询到符合条件的数据"}
    if outcome == "partial_failure":
        return {"success": False, "partial": True, "message": "该子任务执行失败"}
    if tool_name == "query_timesheet":
        return {"success": True, "total_hours": 16.0, "record_count": 2, "records": [{"date": "2026-08-06", "project_name": "星云平台", "duration": 8.0}, {"date": "2026-08-07", "project_name": "星云平台", "duration": 8.0}]}
    if tool_name == "compute_statistics":
        return {"success": True, "total_hours": 40.0, "items": [{"name": "星云平台", "total_hours": 24.0}, {"name": "智慧园区", "total_hours": 16.0}]}
    if tool_name == "query_project":
        return {"success": True, "projects": [{"project_id": "proj_demo_01", "project_name": "星云平台", "status": "active"}]}
    if tool_name == "generate_weekly_report":
        return {"success": True, "week": "2026-W32", "total_hours": 40.0, "report": "本周完成星云平台接口开发与测试。"}
    if tool_name == "save_workhour":
        return {"success": True, "dry_run": True, "preview": {"project_name": "星云平台", "duration": 8.0}, "message": "预览成功，尚未写入"}
    if tool_name == "batch_save_workhour":
        return {"success": True, "dry_run": True, "record_count": 3, "preview_text": "已解析3条工时，尚未写入"}
    if tool_name == "approve_workhour":
        return {"success": True, "approved_count": 2, "workhour_ids": ["wh_mock_01", "wh_mock_02"], "message": "Mock审核成功"}
    if tool_name == "export_report":
        return {"success": True, "file_name": "workhour_mock.xlsx", "size_bytes": 2048, "message": "Mock报表已生成"}
    if tool_name == "suggest_workhour":
        return {"success": True, "suggested_projects": [{"project_id": "proj_demo_01", "project_name": "星云平台"}], "suggested_hours": 8.0}
    if tool_name == "knowledge_qa":
        return {"success": True, "answer": "根据知识库，工时应按实际投入及时填报。", "sources": ["workhour-policy.md"]}
    if tool_name == "kb_outline":
        return {"success": True, "documents": [{"file": "workhour-policy.md", "sections": ["核心规则", "补填要求"]}]}
    if tool_name in {"kb_keyword_search", "kb_semantic_search"}:
        return {"success": True, "results": [{"file": "workhour-policy.md", "section": "核心规则", "content": "工时应按实际投入填报。"}]}
    if tool_name == "kb_read_section":
        return {"success": True, "file": "workhour-policy.md", "section": "核心规则", "content": "工时应按实际投入及时填报。"}
    return {"success": True}


def expected_final_state(action: str, outcome: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    if action == "clarify":
        return {"status": "clarification_required", "must_include_any": ["请提供", "请确认", "需要"], "must_not_include_any": ["已成功", "已完成填报"]}
    if action == "direct":
        return {"status": "completed", "must_include_any": [], "must_not_include_any": ["已调用工具", "已写入"]}
    if action == "rag":
        return {"status": "completed", "must_include_any": [], "must_not_include_any": ["根据个人工时数据", "已写入"]}
    if outcome in {"timeout_unknown", "connection_lost_unknown"}:
        return {"status": "unknown", "must_include_any": ["结果未知", "查询确认"], "must_not_include_any": ["提交成功", "写入成功", "已完成"]}
    if outcome in {"permission_denied", "tool_error", "validation_error", "timeout"}:
        return {"status": "failed", "must_include_any": ["失败", "无权", "超时", "未完成"], "must_not_include_any": ["已成功", "已完成"]}
    if outcome == "partial_failure":
        return {"status": "partially_completed", "must_include_any": ["部分", "失败", "未完成"], "must_not_include_any": ["全部完成", "均已成功"]}
    if outcome == "empty_result":
        return {"status": "completed_empty", "must_include_any": ["未查询到", "暂无", "没有符合"], "must_not_include_any": ["查询失败"]}
    if outcome == "dry_run_success":
        return {"status": "preview_ready", "must_include_any": ["预览", "确认"], "must_not_include_any": ["已写入", "提交成功"]}
    return {"status": "completed", "must_include_any": [], "must_not_include_any": ["执行失败"]}


def normalize_single(raw_cases: list[Any], category: str, model: str) -> list[dict[str, Any]]:
    result = []
    prefix = {
        "query_timesheet": "QTS",
        "compute_statistics": "STA",
        "query_project": "QPR",
        "save_workhour": "SWH",
        "batch_save_workhour": "BSW",
        "generate_weekly_report": "WKR",
        "knowledge_qa": "KQA",
        "general_chat": "CHAT",
        "complex_task": "CPLX",
        "robustness": "ROB",
        "approve_workhour": "APR",
        "export_report": "EXP",
        "suggest_workhour": "SGT",
        "kb_navigation": "KBN",
    }[category]
    for index, raw in enumerate(raw_cases, 1):
        if not isinstance(raw, dict):
            continue
        input_text = str(raw.get("input", "")).strip()
        action = str(raw.get("expected_action", "")).strip()
        tools = normalize_tools(raw.get("expected_tools"), input_text)
        if category == "knowledge_qa":
            action, tools = "rag", []
        elif category == "general_chat":
            action, tools = "direct", []
        elif category == "complex_task":
            if len(tools) >= 2:
                action = "multi_tool"
            else:
                action, tools = "clarify", []
        elif category in BUSINESS_TOOLS and action not in {"clarify", "tool"}:
            action = "tool" if tools else "clarify"
        if len(tools) > 1:
            action = "multi_tool"
        elif action in {"tool", "multi_tool"} and not tools:
            action = "clarify"
        outcome = choose_outcome(index - 1, tools)
        mock_results = [
            {"tool": item["name"], "result": mock_result(item["name"], "partial_failure" if outcome == "partial_failure" and tool_index == len(tools) - 1 else outcome)}
            for tool_index, item in enumerate(tools)
        ]
        case_id = f"BC-S-{prefix}-{index:03d}"
        role = raw.get("entity_type") if raw.get("entity_type") in VALID_ROLES else "employee"
        is_write = bool({item["name"] for item in tools} & WRITE_TOOLS) or category in WRITE_TOOLS
        result.append({
            "case_id": case_id,
            "category": category,
            "sub_type": str(raw.get("sub_type", "generated_case")),
            "risk_level": "critical" if is_write else str(raw.get("risk_level", "normal")),
            "user_goal": str(raw.get("user_goal", "")).strip(),
            "input": input_text,
            "session_id": f"{case_id.lower()}-run-{{repeat_index}}",
            "user_context": {"entity_type": role, "user_id": "test_user_001", "user_name": "测试用户", "department_id": "test_dept_01"},
            "reference_date": REFERENCE_DATE,
            "expected_path": ["llm_with_tools", action, "respond"],
            "expected_action": action,
            "expected_tools": tools,
            "expected_params": [item["params"] for item in tools],
            "mock_tool_results": mock_results,
            "expected_final_state": expected_final_state(action, outcome, tools),
            "tool_outcome": outcome,
            "repeat_count": 10 if is_write else 3,
            "execution_mode": "mock_only" if is_write else "mock_or_isolated_read",
            "tags": sorted(set(str(tag) for tag in raw.get("tags", []) if tag)),
            "source": f"llm_generated:{model}",
            "prompt_version": PROMPT_VERSION,
            "review_status": "pending_human_review",
        })
    return result


def normalize_multi(raw_cases: list[Any], scenario: str, model: str) -> list[dict[str, Any]]:
    result = []
    prefix = scenario.upper().replace("_", "-")[:18]
    for index, raw in enumerate(raw_cases, 1):
        if not isinstance(raw, dict):
            continue
        case_id = f"BC-M-{prefix}-{index:03d}"
        turns = []
        any_write = False
        for turn_index, turn_raw in enumerate(raw.get("turns", []), 1):
            if not isinstance(turn_raw, dict):
                continue
            user_input = str(turn_raw.get("user_input", "")).strip()
            action = str(turn_raw.get("expected_action", "")).strip()
            tools = normalize_tools(turn_raw.get("expected_tools"), user_input)
            if action not in VALID_ACTIONS:
                if len(tools) > 1:
                    action = "multi_tool"
                elif tools:
                    action = "tool"
                else:
                    action = "clarify" if any(mark in user_input for mark in ("？", "?", "哪个", "哪一个", "确认")) else "direct"
            elif len(tools) > 1:
                action = "multi_tool"
            elif action in {"tool", "multi_tool"} and not tools:
                action = "clarify"
            any_write = any_write or bool({item["name"] for item in tools} & WRITE_TOOLS)
            outcome = choose_outcome(index + turn_index, tools, multi=action == "multi_tool")
            if scenario == "tool_failure_recovery" and tools and turn_index == 1:
                outcome = "tool_error"
            if scenario == "partial_tool_failure" and len(tools) > 1:
                outcome = "partial_failure"
            mock_results = [
                {"tool": item["name"], "result": mock_result(item["name"], "partial_failure" if outcome == "partial_failure" and tool_index == len(tools) - 1 else outcome)}
                for tool_index, item in enumerate(tools)
            ]
            turns.append({
                "turn": turn_index,
                "user_input": user_input,
                "expected_action": action,
                "expected_path": ["llm_with_tools", action, "respond"],
                "expected_tools": tools,
                "expected_params": [item["params"] for item in tools],
                "mock_tool_results": mock_results,
                "tool_outcome": outcome,
                "expected_response": expected_final_state(action, outcome, tools),
                "context_assertions": [str(x) for x in turn_raw.get("context_assertions", []) if x],
            })
        role = raw.get("entity_type") if raw.get("entity_type") in VALID_ROLES else "employee"
        result.append({
            "case_id": case_id,
            "category": "multi_turn",
            "scenario_type": scenario,
            "risk_level": "critical" if any_write else str(raw.get("risk_level", "normal")),
            "user_goal": str(raw.get("user_goal", "")).strip(),
            "session_id": f"{case_id.lower()}-run-{{repeat_index}}",
            "user_context": {"entity_type": role, "user_id": "test_user_001", "user_name": "测试用户", "department_id": "test_dept_01"},
            "reference_date": REFERENCE_DATE,
            "turns": turns,
            "expected_final_state": turns[-1]["expected_response"] if turns else {},
            "repeat_count": 10 if any_write else 5,
            "execution_mode": "mock_only" if any_write else "mock_or_isolated_read",
            "tags": sorted(set([scenario] + [str(tag) for tag in raw.get("tags", []) if tag])),
            "source": f"llm_generated:{model}",
            "prompt_version": PROMPT_VERSION,
            "review_status": "pending_human_review",
        })
    return result


def validate_dataset(single: list[dict[str, Any]], multi: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    all_ids = [case.get("case_id") for case in single + multi]
    duplicates = [key for key, value in Counter(all_ids).items() if value > 1]
    if duplicates:
        errors.append(f"duplicate case_id: {duplicates[:10]}")
    single_inputs = [case.get("input", "").strip() for case in single]
    duplicate_inputs = [key for key, value in Counter(single_inputs).items() if key and value > 1]
    if duplicate_inputs:
        errors.append(f"duplicate single-turn input: {duplicate_inputs[:10]}")
    if len(single) < 200:
        errors.append(f"single-turn count {len(single)} < 200")
    if len(multi) < 100:
        errors.append(f"multi-turn count {len(multi)} < 100")
    single_counts = Counter(case.get("category") for case in single)
    for category, expected in SINGLE_QUOTAS.items():
        if single_counts[category] != expected:
            errors.append(f"single quota {category}: {single_counts[category]} != {expected}")
    multi_counts = Counter(case.get("scenario_type") for case in multi)
    for scenario, expected in MULTI_QUOTAS.items():
        if multi_counts[scenario] != expected:
            errors.append(f"multi quota {scenario}: {multi_counts[scenario]} != {expected}")

    covered_tools = Counter()
    outcome_counts = Counter()
    for case in single:
        required = {"case_id", "category", "risk_level", "user_goal", "input", "session_id", "expected_path", "expected_tools", "expected_params", "expected_final_state", "repeat_count"}
        missing = sorted(required - case.keys())
        if missing:
            errors.append(f"{case.get('case_id')}: missing {missing}")
        if not case.get("input"):
            errors.append(f"{case.get('case_id')}: empty input")
        action = case.get("expected_action")
        if action not in VALID_ACTIONS:
            errors.append(f"{case.get('case_id')}: invalid action {action!r}")
        # knowledge_qa is exposed as an FC signal but node_llm_with_tools
        # converts it to intent=knowledge_qa and routes to RAG; it is not
        # executed by TaskExecutor like ordinary tools.
        if action == "rag":
            covered_tools["knowledge_qa"] += 1
        tools = case.get("expected_tools", [])
        if action == "tool" and len(tools) != 1:
            errors.append(f"{case.get('case_id')}: tool action requires exactly one tool")
        if action == "multi_tool" and len(tools) < 2:
            errors.append(f"{case.get('case_id')}: multi_tool requires >=2 tools")
        if action in {"clarify", "rag", "direct"} and tools:
            errors.append(f"{case.get('case_id')}: {action} must not call business tools")
        for tool in tools:
            name = tool.get("name")
            covered_tools[name] += 1
            if name not in BUSINESS_TOOLS:
                errors.append(f"{case.get('case_id')}: invalid tool {name!r}")
            if name in DRY_RUN_TOOLS and tool.get("params", {}).get("dry_run") is not True:
                errors.append(f"{case.get('case_id')}: write tool must dry_run")
            if name == "batch_save_workhour" and tool.get("params", {}).get("text") != case.get("input"):
                errors.append(f"{case.get('case_id')}: batch text must equal input")
        outcome_counts[case.get("tool_outcome")] += 1

    for case in multi:
        turns = case.get("turns", [])
        if not 3 <= len(turns) <= 6:
            errors.append(f"{case.get('case_id')}: turn count {len(turns)} not in 3..6")
        for turn in turns:
            if not turn.get("user_input"):
                errors.append(f"{case.get('case_id')} turn {turn.get('turn')}: empty input")
            if turn.get("expected_action") not in VALID_ACTIONS:
                errors.append(f"{case.get('case_id')} turn {turn.get('turn')}: invalid action")
            for tool in turn.get("expected_tools", []):
                covered_tools[tool.get("name")] += 1
                if tool.get("name") in DRY_RUN_TOOLS and tool.get("params", {}).get("dry_run") is not True:
                    errors.append(f"{case.get('case_id')} turn {turn.get('turn')}: write tool must dry_run")
            outcome_counts[turn.get("tool_outcome")] += 1

    for tool in BUSINESS_TOOLS:
        if covered_tools[tool] == 0:
            errors.append(f"tool not covered: {tool}")
    required_outcomes = {"success", "empty_result", "tool_error", "permission_denied", "timeout", "dry_run_success", "validation_error", "timeout_unknown", "connection_lost_unknown", "partial_failure"}
    for outcome in sorted(required_outcomes):
        if outcome_counts[outcome] == 0:
            errors.append(f"outcome not covered: {outcome}")
    if all(case.get("review_status") == "pending_human_review" for case in single + multi):
        warnings.append("All generated cases still require human semantic review before freezing the blind set.")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "single_turn": len(single),
            "multi_turn": len(multi),
            "single_by_category": dict(sorted(single_counts.items())),
            "multi_by_scenario": dict(sorted(multi_counts.items())),
            "tool_coverage": dict(sorted(covered_tools.items())),
            "outcome_coverage": dict(sorted(outcome_counts.items(), key=lambda item: str(item[0]))),
        },
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def generate_all() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    client = GeneratorClient()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Long multi-turn JSON responses are intentionally serialized. DashScope
    # may leave several concurrent long generations queued for minutes.
    semaphore = asyncio.Semaphore(1)

    def load_cached(path: Path, count: int) -> list[Any] | None:
        if not path.exists():
            return None
        try:
            cases = extract_json(path.read_text(encoding="utf-8")).get("cases")
        except Exception:
            return None
        return cases if isinstance(cases, list) and len(cases) == count else None

    async def generate_exact(
        prompt: str,
        path: Path,
        count: int,
        label: str,
        case_validator: Any = None,
    ) -> list[Any]:
        cached = load_cached(path, count)
        if cached is not None and (case_validator is None or case_validator(cached)):
            print(f"[cache] {label}: {count}", flush=True)
            return cached
        last_count: Any = "invalid"
        for generation_attempt in range(1, 4):
            retry_note = "" if generation_attempt == 1 else f"\n这是第 {generation_attempt} 次生成。上次条数不正确，请逐条编号并严格输出 {count} 条。"
            print(f"[api] {label}: attempt {generation_attempt}", flush=True)
            async with semaphore:
                parsed, raw_text = await client.generate_json(prompt + retry_note)
            path.write_text(raw_text, encoding="utf-8")
            cases = parsed.get("cases")
            last_count = len(cases) if isinstance(cases, list) else "invalid"
            if (
                isinstance(cases, list)
                and len(cases) == count
                and (case_validator is None or case_validator(cases))
            ):
                print(f"[ok] {label}: {count}", flush=True)
                return cases
            print(f"[retry] {label}: expected {count}, got {last_count}", flush=True)
        raise ValueError(f"{label}: expected {count} cases, got {last_count}")

    async def run_single(category: str, count: int) -> list[dict[str, Any]]:
        path = RAW_DIR / f"single_{category}.json"
        cases = await generate_exact(single_prompt(category, count), path, count, f"single {category}")
        return normalize_single(cases, category, client.model)

    async def run_multi(scenario: str, count: int) -> list[dict[str, Any]]:
        path = RAW_DIR / f"multi_{scenario}.json"
        cases = await generate_exact(multi_prompt(scenario, count), path, count, f"multi {scenario}")
        sanitized_cases = [sanitize_generated_multi_case(case) for case in cases]
        if sanitized_cases != cases:
            cases = sanitized_cases
            path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
        invalid_indexes = [index for index, case in enumerate(cases) if not generated_multi_case_valid(case)]
        if invalid_indexes:
            repair_dir = RAW_DIR / "repairs"
            repair_dir.mkdir(parents=True, exist_ok=True)
            for chunk_number, start in enumerate(range(0, len(invalid_indexes), 5), 1):
                indexes = invalid_indexes[start : start + 5]
                originals = [cases[index] for index in indexes]
                digest = hashlib.sha256(json.dumps(originals, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
                repair_path = repair_dir / f"multi_{scenario}_{chunk_number:02d}_{digest}.json"
                repaired = load_cached(repair_path, len(originals))
                if repaired is None or not all(generated_multi_case_valid(case) for case in repaired):
                    repaired = await generate_exact(
                        multi_repair_prompt(scenario, originals),
                        repair_path,
                        len(originals),
                        f"repair multi {scenario} chunk {chunk_number}",
                        case_validator=lambda values: all(generated_multi_case_valid(value) for value in values),
                    )
                if not all(generated_multi_case_valid(case) for case in repaired):
                    raise ValueError(f"repair multi {scenario} chunk {chunk_number}: turn count still invalid")
                for case_index, repaired_case in zip(indexes, repaired):
                    cases[case_index] = repaired_case
            path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
        return normalize_multi(cases, scenario, client.model)

    tasks = [run_single(category, count) for category, count in SINGLE_QUOTAS.items()]
    tasks.extend(run_multi(scenario, count) for scenario, count in MULTI_QUOTAS.items())
    generated = await asyncio.gather(*tasks)
    single_groups = generated[: len(SINGLE_QUOTAS)]
    multi_groups = generated[len(SINGLE_QUOTAS) :]
    single = [case for group in single_groups for case in group]
    multi = [case for group in multi_groups for case in group]
    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_model": client.model,
        "api_base_host": re.sub(r"^https?://", "", client.api_base).split("/", 1)[0],
        "prompt_version": PROMPT_VERSION,
        "reference_date": REFERENCE_DATE,
        "usage_current_process": dict(client.usage),
        "usage_scope": "current_process_only; resumed/cache runs are not cumulative",
        "raw_response_file_count": len(list(RAW_DIR.rglob("*.json"))),
        "contains_api_key": False,
        "production_data_sent": False,
    }
    return single, multi, provenance


def historical_manifest() -> dict[str, Any]:
    data_root = REPO_ROOT / "fastapi-service" / "tests" / "data"
    files = sorted(data_root.rglob("*.json"))
    total = 0
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                total += len(value)
        except Exception:
            continue
    run_path = REPO_ROOT / "docs_caich" / "test_by_caic" / "function_calling_experiment_validation" / "B_function_calling_unified_cloud_qwen_plus_run2.json"
    return {
        "existing_regression_case_count": total,
        "existing_data_root": str(data_root.relative_to(REPO_ROOT)).replace("\\", "/"),
        "existing_function_calling_result": str(run_path.relative_to(REPO_ROOT)).replace("\\", "/") if run_path.exists() else None,
        "reuse_policy": "Historical cases remain a regression pool and are not counted as the new blind set.",
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    load_environment()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    single_path = OUTPUT_DIR / "single_turn_320.jsonl"
    multi_path = OUTPUT_DIR / "multi_turn_100.jsonl"

    if args.validate_only:
        single = read_jsonl(single_path)
        multi = read_jsonl(multi_path)
        provenance = json.loads((OUTPUT_DIR / "generation_provenance.json").read_text(encoding="utf-8"))
    else:
        started = time.perf_counter()
        single, multi, provenance = asyncio.run(generate_all())
        # Normalize generated gold data to the same production-semantic
        # contract used by the checked-in dataset.  This prevents a future
        # regeneration from reintroducing opaque IDs, generic Mock payloads,
        # Friday-only week ranges, or over-broad response phrase checks.
        from repair_business_completion_dataset_semantics import REVISION, repair_cases

        repair_cases(single, multi)
        provenance["semantic_revision"] = REVISION
        provenance["generation_wall_seconds"] = round(time.perf_counter() - started, 3)
        write_jsonl(single_path, single)
        write_jsonl(multi_path, multi)
        (OUTPUT_DIR / "generation_provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    validation = validate_dataset(single, multi)
    (OUTPUT_DIR / "validation_report.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "dataset_name": "business_completion_preproduction",
        "status": "generated_pending_human_review" if validation["valid"] else "invalid",
        "reference_date": REFERENCE_DATE,
        "files": {
            single_path.name: {"rows": len(single), "sha256": sha256(single_path)},
            multi_path.name: {"rows": len(multi), "sha256": sha256(multi_path)},
        },
        "generation": provenance,
        "historical_assets": historical_manifest(),
        "validation": validation,
    }
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "counts": validation["counts"], "errors": validation["errors"], "warnings": validation["warnings"], "usage_current_process": provenance.get("usage_current_process", {})}, ensure_ascii=False, indent=2))
    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
